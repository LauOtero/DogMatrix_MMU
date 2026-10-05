# Dog Matrix MMU — Flujo de carga y gestión de filamento

**Documento:** DOC-FLOW · **Versión:** 0.1.0 · **Audiencia:** integradores / desarrolladores
**Implementación:** [autoload.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/klippy/extras/dog_matrix/autoload.py)
**Estado:** implementado y verificado (24 pruebas)

Este documento describe el ciclo completo de carga y gestión de filamento y su
correspondencia con los dispositivos (hub/splitter, buffer, encoder, sensor de
diámetro) y con la arquitectura multi-unidad.

---

## 1. Especificación ↔ implementación

| # | Requisito | Implementación |
|---|---|---|
| 1 | `pre_gate` se activa al insertar el filamento físicamente | `AutoLoadController.on_pre_gate(gate, unit)` |
| 2 | Arranca el motor de la unidad y sigue hasta `post_gate`; resiliencia (no infinito) | `STATE_FEEDING` + `poll()` + `pre_gate_timeout_s` → `FAULT_PRE_GATE_TIMEOUT` |
| 3 | Hub/splitter con encoder y sensor de diámetro para control en tiempo real | `HubDevice.read_encoder_mm()` / `read_diameter_mm()` / `monitor_print()` |
| 4 | Alcanzado el post_gate, espera hasta la selección | `STATE_WAITING_SELECTION` |
| 5 | Confirmada la selección, avance hub → toolhead | `select_filament()` → `STATE_BOWDEN_FEED` → `STATE_READY` |
| 6 | Buffer intermedio tensión/compresión como splitter secundario multi-MMU | `BufferDevice` + `MultiUnitRouter` |
| 7 | Validación del flujo completo (sensores, motores, intermedios) | `tests/unit/test_autoload.py` (24 casos) |

---

## 2. Cómo lo resuelve Happy Hare (v4)

Revisión del repositorio `moggieuk/Happy-Hare` (`extras/mmu/`):

- **Detección**: los sensores pre-gate son `mmu_entry`. Se registran con las
  **librerías de Klipper**: `buttons.register_debounce_button(...)` en
  `mmu_sensor_utils.py` (sin polling propio). El evento se enruta por
  `reactor.register_callback` y ejecuta el comando `__MMU_SENSOR_INSERT`.
- **Política**: en `commands/mmu_sensor_insert.py`, si la MMU está `IDLE`, no
  imprime y `gate_autoload` está activo, lanza `MMU_PRELOAD`; se evita así
  reentrada y preloads dobles.
- **Movimiento**: `_preload_gate` → `_home_to_gate` mueve el gear **por el trapq
  del toolhead** (`homing.manual_home`, `MmuStepper`), con **límite por
  distancia** (`gate_preload_homing_max`, por defecto 100 mm) y reintentos
  (`attempts=2`). Por defecto el endstop es el **encoder** (validación de
  movimiento), no un sensor post-gate físico.
- **No bloqueo**: modelo cooperativo de Klipper (greenlets + reactor); el
  movimiento se encola en el trapq y **no hay busy-wait**. El camino de sensores
  es totalmente asíncrono (callback → reactor → gcode).
- **Resiliencia**: límite de distancia + reintentos + marcado de estado
  (`GATE_EMPTY`/`GATE_UNKNOWN`/`GATE_AVAILABLE`) y **desenergización del driver en
  reposo**; no hay un watchdog temporal explícito sobre el gear (delega en el
  timeout de comunicación del MCU de Klipper).
- **Hub/buffer**: `unit/mmu_buffer.py` crea `sync_feedback_compression`/`tension`
  (también ADC proporcional); `unit/mmu_sync_feedback.py` gestiona estado y
  FlowGuard. Encoder = `pulse_counter.MCU_counter` expuesto como endstop virtual.
- **Librerías Klipper usadas**: `reactor`, `buttons`, `pins`, `mcu`, `homing`,
  `motion_queuing` + `chelper`, `toolhead`, `kinematics.extruder` (`MmuStepper`
  hereda de `ExtruderStepper`), `force_move`, `gcode`, `pulse_counter`, `tmc`.

### Diseño Dog Matrix (reactor, eventos y deadlines)

`autoload.py` incorpora las lecciones anteriores, manteniendo el módulo
autocontenido y testeable:

| Aspecto | Happy Hare | Dog Matrix (`autoload.py`) |
|---|---|---|
| Entrada de sensores | `buttons.register_debounce_button` | `wire_pre_gate_buttons()` usa la misma API; handler diferido |
| Arranque del motor | macro `MMU_PRELOAD` (greenlet de gcode) | `on_pre_gate()` fija estado + **deadline**; motor ON |
| Parada | homing por distancia/encoder | `on_post_gate()` (borde) detiene el motor |
| Scheduler | movimiento en trapq; timer propio para otras tareas | `AutoLoadService` con **un único** `reactor.register_timer` |
| Reposo | — | el timer devuelve `reactor.NEVER` (**cero despertares**) |
| Watchdog | timeout de comunicación del MCU | **explícito**: `next_deadline()` → FAULT y motor OFF |
| Excepciones | manejo de error/pausa | `AutoLoadService` aísla y llama `emergency_stop()` |
| Determinismo | dependiente del greenlet | transiciones puras `(estado, entradas, eventtime)` |

**Garantías clave:**

1. **No bloquea**: un solo timer; el handler del botón sólo hace
   `reactor.register_callback` y retorna; nunca hay `sleep`/espera activa.
2. **Alto rendimiento**: el timer se reprograma al *deadline* exacto
   (`register_timer`/`update_timer`) y en reposo usa `reactor.NEVER`.
3. **Determinismo**: `service(eventtime)` es una función pura del instante del
   reactor; sin aleatoriedad ni estado oculto.
4. **Resiliencia absoluta**: `_stop_motor()` protegido, `_set_fault()` limpia
   deadlines, `emergency_stop()` idempotente y liberación del router.

> Nota de integración: el movimiento físico del gear (trapq/`homing.manual_home`)
> es responsabilidad del adaptador de motor (`MotorDriver`); `autoload.py` define
> el **contrato** y la lógica de decisión, sin acoplarse a `toolhead`.

---

## 3. Máquina de estados

```
        insert filamento
 IDLE ──on_pre_gate──▶ FEEDING ──post_gate──▶ WAITING_SELECTION
   ▲                      │                          │
   │                   timeout                    select_filament
   │                      ▼                          ▼
   │                    FAULT ◀──timeout── BOWDEN_FEED ──toolhead──▶ READY
   └──────────────── reset() ─────────────────────────────────────────┘
```

| Estado | Significado |
|---|---|
| `idle` | Sin actividad |
| `feeding` | `pre_gate` activo; motor de la unidad en marcha |
| `waiting_selection` | Filamento en el hub/splitter (post_gate); espera de selección |
| `bowden_feed` | Selección confirmada; avance hub → toolhead |
| `ready` | Filamento en el toolhead |
| `fault` | Fallo con motor detenido (resiliencia) |

**Causas de fallo:** `pre_gate_timeout`, `bowden_timeout`,
`diameter_out_of_range`, `no_movement`, `buffer_conflict`.

**Resiliencia clave:** ante cualquier fallo o timeout el motor se **detiene
siempre** (`_stop_motor()` en `_set_fault`, protegido con `try/except`), de modo
que nunca queda encendido indefinidamente.

---

## 4. Hub / splitter principal

`HubDevice` modela el punto donde converge el filamento de cada unidad:

- **Sensor de salida compartido** (`shared_exit_sensor`): es el `post_gate`
  físico del hub; su activación dispara `waiting_selection`.
- **Encoder** opcional: validación de movimiento en tiempo real.
- **Sensor de diámetro** opcional: control de calidad del material.

```python
hub = HubDevice(HubConfig(has_encoder=True, has_diameter_sensor=True),
                sensors, encoder_reader=lambda: 12.5, diameter_reader=lambda: 1.75)
```

Validación durante la impresión:

```python
issues = controller.monitor_print(moved_mm=0.0, diameter_mm=2.5)
# -> ["diameter_out_of_range", "no_movement"]
```

---

## 5. Buffer intermedio (splitter secundario)

`BufferDevice` interpreta el sensor de **tensión/compresión** (`neutral`,
`compressed`, `expanded`, `disabled`) y calcula un **factor de velocidad** para
el motor:

| Estado | Factor | Efecto |
|---|---|---|
| `compressed` | 0.5 | El extrusor tira menos → reducir avance |
| `expanded` | 1.5 | Hay holgura → acelerar |
| `neutral` / `disabled` | 1.0 | Sin ajuste |

El buffer actúa como **splitter secundario**: fusiona la salida de todas las
unidades hacia el toolhead. `MultiUnitRouter` garantiza **exclusividad** (solo
una unidad empuja a la vez); si otra unidad solicita mientras hay una activa, se
registra como pendiente o se reporta `buffer_conflict`.

Hubs/buffers compatibles con este modelo (referencias de la comunidad):
Bambu Lab **AMS Hub** y **Filament Buffer**, Annex **Belay**, ArmoredTurtle
**TurtleNeck** y **TN-Pro**, **Stumpy PSF Sensor** y **OpenAMS filament-buffer**.

---

## 6. Multi-MMU

- Cada unidad tiene su propio motor (`motor_for(unit_id)`).
- El reparto hacia el hub/buffer es exclusivo y ordenado por `MultiUnitRouter`.
- Los gates son contiguos entre unidades (ver [WIZARD.md](WIZARD.md)).

---

## 7. Configuración

En `dog_matrix.cfg` (ver `config/base/dog_matrix.cfg`):

```ini
enable_autoload: true
hub_shared_exit: true
hub_encoder: true
hub_diameter_sensor: true
hub_units: 2
buffer_tension: true
buffer_compression: true
autoload_pre_gate_timeout_s: 30
autoload_bowden_timeout_s: 60
```

Parámetros del motor/tiempos en `AutoLoadConfig` (`feed_speed_mm_s`,
`bowden_speed_mm_s`, `diameter_min_mm`, `diameter_max_mm`, `min_encoder_mm`).

---

## 8. Pruebas (punto 7)

`tests/unit/test_autoload.py` cubre todos los escenarios operativos:

| Escenario | Caso |
|---|---|
| pre_gate arranca motor | `test_pre_gate_starts_motor` |
| post_gate detiene y espera | `test_post_gate_stops_motor_and_waits` |
| resiliencia por timeout | `test_pre_gate_timeout_is_resilient`, `test_bowden_timeout_is_resilient` |
| encoder/diámetro | `test_hub_encoder_and_diameter_readers`, `test_monitor_print_...` |
| selección y avance | `test_selection_moves_filament_to_toolhead`, `test_selection_requires_loaded_gate` |
| buffer / splitter secundario | `test_buffer_state_and_speed_factor`, `test_buffer_change_adjusts_motor_speed` |
| multi-unidad | `test_multiunit_router_is_exclusive`, `test_conflicting_unit_reports_buffer_conflict` |
| reactor (no bloqueante) | `test_service_registers_single_timer_...`, `test_service_faults_at_deadline_...`, `test_service_returns_deadline_while_active`, `test_kick_rearms_timer`, `test_service_exception_triggers_emergency_stop`, `test_stop_disarms_and_stops_motor` |
| handlers de borde | `test_edge_handlers_full_flow`, `test_emergency_stop_is_idempotent...` |
| puente Klipper (`buttons`) | `test_wire_pre_gate_buttons_uses_klipper_api` |
| ciclo de vida | `test_reset_clears_state...`, `test_status_is_observable` |

```bash
PYTHONPATH=.dm_pylibs python -m pytest tests/unit/test_autoload.py -q
```

---

## 9. Índice

- [autoload.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/klippy/extras/dog_matrix/autoload.py)
- [test_autoload.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/tests/unit/test_autoload.py)
- [PIN_CONFIGURATION.md](PIN_CONFIGURATION.md) — pre/post-gate dual e I2C
- [WIZARD.md](WIZARD.md) — multi-unidad y presets
