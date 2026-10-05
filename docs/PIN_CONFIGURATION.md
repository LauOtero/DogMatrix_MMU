# Dog Matrix MMU — Sistema de configuración de pines y placas

**Documento:** DOC-PINS · **Versión:** 0.1.0 · **Audiencia:** integradores / desarrolladores
**Estado:** implementado y verificado (151 pruebas en verde)

Este documento especifica el **sistema integral de configuración de pines** de
Dog Matrix MMU: la revisión de la configuración nativa de Happy Hare, el mapeo
uniforme de alias, el soporte de placas (incluidas las cinco requeridas), la
integración de **4 bobinas por unidad** y la validación de conflictos.

---

## 1. Objetivos y requisitos cubiertos

| # | Requisito | Implementación |
|---|---|---|
| 1 | Mapeo uniforme con alias Klipper coherentes | Namespace `MMU_*` (Happy Hare) + espejo `DM_*`; generado por `boards.py` |
| 2 | Soporte nativo de las 5 placas + todas las de Happy Hare | `config/boards/*.yaml` + `happy_hare_board_catalog()` |
| 3 | 4 bobinas por unidad múltiple | `COILS_PER_UNIT = 4`, reserva de recursos en `PinPlan.coils()` |
| 4 | Compatibilidad Klipper/Happy Hare y detección de conflictos | `validate_pin_plan`, `validate_board_definition`, `SystemValidator.validate_board_plan` |
| 5 | Documentación de cambios, alias, migración y dev | Este documento + `docs/MULTI_UNIT.md` |

---

## 2. Revisión de la configuración nativa de Happy Hare

### 2.1 Modelo de dispositivo

Happy Hare v4 describe el hardware en tres capas:

- **`[mmu_unit XXX]`**: una MMU física (vendor/version, nº de gates,
  capacidades, geometría `selector_type`).
- **`[mmu_machine]`**: agrupación lógica de una o más unidades; los gates se
  numeran **de forma contigua** entre unidades (p. ej. ERCF 0-8 + ViViD 9-12).
- **`[mmu_toolhead]`**, `[mmu_stepper]`, `[mmu_encoder]`, `[mmu_buffer]`,
  `[mmu_espooler]`, `[mmu_nfc_reader]`: componentes referenciados por nombre.

### 2.2 Convención de alias

Happy Hare usa un bloque `[board_pins <mcu>]` cuyas **aliases coinciden con los
tokens internos `PIN_*`** del instalador. La convención observable en las
configs publicadas de placa (FYSETC ERB V2, Mellow FLY-MMU) es el prefijo
`MMU_`:

| Grupo | Alias |
|---|---|
| Gear (tracción) | `MMU_GEAR_STEP`, `MMU_GEAR_DIR`, `MMU_GEAR_ENABLE`, `MMU_GEAR_UART`, `MMU_GEAR_DIAG` |
| Gear multigear | `MMU_GEAR_STEP_1..N`, `MMU_GEAR_DIR_1..N`, … (el primer driver sin sufijo) |
| Selector | `MMU_SEL_STEP`, `MMU_SEL_DIR`, `MMU_SEL_ENABLE`, `MMU_SEL_UART`, `MMU_SEL_DIAG`, `MMU_SEL_ENDSTOP` |
| Servos | `MMU_SERVO` (selector), `MMU_CUT_SERVO` (corte) |
| Sensores | `MMU_ENCODER`, `MMU_GATE_SENSOR`, `MMU_PRE_GATE_0..N`, `MMU_POST_GATE_0..N`, `MMU_SHARED_EXIT` |
| I2C | `MMU_I2C_SCL`, `MMU_I2C_SDA` |
| LED | `MMU_NEOPIXEL` |
| Periféricos | `MMU_ESPOOLER_*`, `PIN_FAN*`, `PIN_HEATER_FAN*` |

> Nota: en HH v4 el template `mmu_hardware.cfg` referencia tokens `PIN_*` que el
> instalador rellena con pines reales; los alias `MMU_*` son la capa
> `[board_pins]` que se teclea en los `mmu.cfg` de placa.

### 2.3 Tipologías (mecanismo de selección)

| Tipo | Mecanismo | Ejemplos | Topología Dog Matrix |
|---|---|---|---|
| **A** | Gear compartido + selector móvil | ERCF, Tradrack, ViViD, MMX | `selector` |
| **B** | Gear por gate, sin selector | Box Turtle, Night Owl, EMU, KMS, VVD, QIDI | `gear_per_gate` |
| **C** | Gear por gate **y** selector | custom (manual) | `hybrid` |

### 2.4 Catálogo de placas soportadas por Happy Hare

`happy_hare_board_catalog()` mapea cada `BOARD_TYPE_*` a una placa Dog Matrix
(arquetipo). Los tipos con pinout público tienen definición completa; el resto
se mapean a arquetipos genéricos que exigen confirmar los pines (igual que el
`Other`/`Manual` de Happy Hare).

| `BOARD_TYPE_*` | Placa | Arquetipo Dog Matrix |
|---|---|---|
| `EASY_BRD`, `EASY_BRD_RP2040` | ERCF Easy-BRD | `ercf_easy_brd_v1_1` |
| `MELLOW_EASY_BRD_CAN_1` | Mellow EASY-BRD v1.x | `mellow_fly_ercf_v1` |
| `MELLOW_EASY_BRD_CAN_2` | Mellow EASY-BRD v2.x | `mellow_fly_ercf_v2` |
| `ERB_1` | FYSETC Burrows ERB v1 | `fysetc_erb_v1` |
| `ERB_2` | FYSETC Burrows ERB v2 | `fysetc_erb_v2` |
| `SKR_PICO_1`, `EBB42_1_2` | BTT | `generic_selector` |
| `MMB_1_0`, `MMB_1_1`, `MMB_2_0` | BTT MMB CAN | `btt_mmb_can_v1_0` / `_v1_1` / `_v2_0` |
| `KMS_1_0`, `VVD_1_0`, `QIDI_BOX_2_0` | BIQU/BTT/QIDI (tipo-B) | `generic_gear_per_gate` |
| `AFC_*`, `CHAMELEON_X5_1`, `TZB_1_0`, `WGB_3_0`, `OWLFC_MINI_1_0` | AFC / varios | genéricos |
| `OTHER`, `MANUAL` | No listada / MCU externo | genéricos |

---

## 3. Arquitectura del sistema de pines

### 3.1 Componentes

| Fichero | Rol |
|---|---|
| `klippy/extras/dog_matrix/boards.py` | Registro de placas, resolución de `PinPlan`, validación y render |
| `config/boards/*.yaml` | Definiciones físicas de placa (`schema_version: 1`) |
| `installer/generator.py` | Emite `[board_pins dogmatrix]` y `[dm_pins]` con alias |
| `installer/templates/dog_matrix_generated.cfg.tmpl` | Plantilla con `${board_pins_block}` |
| `installer/validator.py` | `validate_pin_plan`, `validate_board_plan` |
| `klippy/extras/dog_matrix/units.py` | 4 bobinas por unidad (`coils_per_unit`) |

### 3.2 Esquema de placa (`config/boards/<id>.yaml`)

```yaml
schema_version: 1
board_id: mellow_fly_mmu
display_name: Mellow FLY-MMU
happy_hare_board_type: OTHER
topology: both           # selector | gear_per_gate | both
coils_per_unit: 4
max_gates: 8
mcu: { name: mmu, transport: can, arch: stm32h723 }
drivers:                 # DRIVER0..N (indexados)
  - { step: PE14, dir: PE13, enable: "!PE12", uart: PE11, diag: PE9 }
pins:                    # roles no-driver
  selector_endstop: PE2
  encoder: "^PE3"
  gate_sensor: "^PC3"
  servo: PA3
  cut_servo: PA2
  neopixel: PE10
  pre_gate: [PC6, PD15, PD14, PD13, PD12, PD11, PD10, PD9]
i2c_bus: i2c3          # nombre del bus Klipper (si la placa expone I2C)
shared_pins:             # pares que comparten pin de forma legítima
  - "SEL_DIAG,SEL_ENDSTOP"
reserved_pins:                # recursos reservados sin alias
  neopixel_alt: PA8
capabilities: { selector: true, gear_per_gate: true, encoder: true, servo: true, led: true }
```

### 3.3 Namespace uniforme de alias

`build_pin_plan()` genera, para **cualquier** placa, los mismos alias canónicos
según la capacidad declarada, garantizando coherencia total:

- `MMU_<ROL>` — compatible Happy Hare (retrocompatibilidad).
- `DM_<ROL>` — espejo con nomenclatura nativa.

Ejemplo de salida (FLY-ERCF V2 como selectora):

```ini
[board_pins dogmatrix]
mcu: mmu
aliases:
  MMU_SEL_STEP=gpio4
  MMU_SEL_DIR=gpio3
  MMU_SEL_ENABLE=!gpio5
  MMU_GEAR_STEP=gpio7
  MMU_ENCODER=^gpio15
  MMU_PRE_GATE_0=gpio24
  ...
  DM_SEL_STEP=gpio4
  ...
```

---

## 4. Mapeo por placa (placas requeridas)

### 4.1 Mellow FLY-MMU (STM32H723)

| Función | Alias | Pin | Fuente |
|---|---|---|---|
| Selector STEP/DIR/EN/UART/DIAG | `MMU_SEL_*` | PE14 / PE13 / !PE12 / PE11 / PE9 | [Mellow cfg](https://mellow.klipper.cn/en/docs/ProductDoc/ToolBoard/fly-mmu/mmu/cfg) |
| Gear STEP/DIR/EN/UART/DIAG | `MMU_GEAR_*` | PC5 / PB1 / !PB0 / PE7 / PE8 | idem |
| Gear bobinas 1-3 (gear-per-gate) | `MMU_GEAR_*_1..3` | PC4,PA6,!PA7,PA5 / PE4,PE6,!PE5,PC13 | [Mellow pin](https://mellow.klipper.cn/en/docs/ProductDoc/ToolBoard/fly-mmu/mmu/pin) |
| Selector endstop | `MMU_SEL_ENDSTOP` | PE2 | Mellow cfg |
| Encoder | `MMU_ENCODER` | ^PE3 | Mellow cfg |
| Gate sensor | `MMU_GATE_SENSOR` | ^PC3 | Mellow cfg |
| Servo / Cut servo | `MMU_SERVO` / `MMU_CUT_SERVO` | PA3 / PA2 | Mellow cfg |
| Neopixel | `MMU_NEOPIXEL` | PE10 | Mellow cfg |
| Pre-gate 0..7 | `MMU_PRE_GATE_0..7` | PC6, PD15..PD9 | Mellow cfg (por confirmar vs esquema) |

> Los pines `*_DIAG` (PE8/PE9) y los `PRE_GATE` provienen del bloque oficial de
> Happy Hare; **verificar contra el esquema físico** si se usa stallguard.

### 4.2 Mellow FLY-ERCF V1 (RP2040)

| Función | Alias | Pin |
|---|---|---|
| Selector STEP/DIR/EN/UART/DIAG | `MMU_SEL_*` | gpio2 / gpio1 / !gpio3 / gpio0 / gpio22 |
| Gear STEP/DIR/EN/UART/DIAG | `MMU_GEAR_*` | gpio7 / gpio8 / !gpio6 / gpio9 / gpio23 |
| Selector endstop | `MMU_SEL_ENDSTOP` | ^gpio20 |
| Encoder | `MMU_ENCODER` | ^gpio15 |
| Servo | `MMU_SERVO` | gpio21 |
| Neopixel | `MMU_NEOPIXEL` | gpio14 |
| Pre-gate 0..9 | `MMU_PRE_GATE_*` | gpio10,26,11,27,12,28,24,29,13,25 |

### 4.3 Mellow FLY-ERCF V2 (RP2040)

| Función | Alias | Pin |
|---|---|---|
| Selector STEP/DIR/EN/UART/DIAG | `MMU_SEL_*` | gpio4 / gpio3 / !gpio5 / gpio2 / gpio20 |
| Gear STEP/DIR/EN/UART | `MMU_GEAR_*` | gpio7 / gpio8 / !gpio6 / gpio9 |
| Selector endstop | `MMU_SEL_ENDSTOP` | ^gpio20 (comparte con SEL_DIAG) |
| Encoder | `MMU_ENCODER` | ^gpio15 |
| Shared exit | `MMU_SHARED_EXIT` | ^gpio25 (comparte con PRE_GATE_2) |
| Servo | `MMU_SERVO` | gpio21 |
| Neopixel | `MMU_NEOPIXEL` | gpio14 |
| Pre-gate 0..11 | `MMU_PRE_GATE_*` | gpio24,22,25,23,13,26,12,27,11,28,10,29 |

### 4.4 ERCF Easy Brd V1.1 (Seeeduino XIAO / SAMD21)

| Función | Alias | Pin |
|---|---|---|
| Selector STEP/DIR/EN/UART/DIAG | `MMU_SEL_*` | PA9 / PB8 / !PA11 / PA8 (addr 1) / PA7 |
| Gear STEP/DIR/EN/UART | `MMU_GEAR_*` | PA4 / PA10 / !PA2 / PA8 (addr 0) |
| Selector endstop | `MMU_SEL_ENDSTOP` | ^!PB9 |
| Encoder (comparte salida) | `MMU_ENCODER` | ^PA6 |
| Servo | `MMU_SERVO` | PA5 |

> MCU a 3.3 V. UART TMC2209 en bus compartido (`uart_address` 0/1). Sin
> neopixel ni sensor de toolhead en placa.

### 4.5 FYSETC ERB V1.0 y V2.0 (RP2040)

| Función | Alias | ERB V1 | ERB V2 |
|---|---|---|---|
| Selector STEP/DIR/EN/UART/DIAG | `MMU_SEL_*` | gpio16 / !gpio15 / !gpio14 / gpio17 / gpio19 | idem |
| Gear STEP/DIR/EN/UART/DIAG | `MMU_GEAR_*` | gpio10 / !gpio9 / !gpio8 / gpio20 / gpio13 | gpio10 / !gpio9 / !gpio8 / **gpio11** / gpio13 |
| Selector endstop | `MMU_SEL_ENDSTOP` | ^gpio24 | ^gpio24 |
| Encoder | `MMU_ENCODER` | ^gpio22 | ^gpio22 |
| Shared exit | `MMU_SHARED_EXIT` | ^gpio25 | ^gpio25 |
| Servo / Neopixel | `MMU_SERVO` / `MMU_NEOPIXEL` | gpio23 / gpio21 | gpio23 / gpio21 |
| Pre-gate 0..11 | `MMU_PRE_GATE_*` | gpio0-7, 26-29 | gpio12,18,2-7,26-29 |

> V1: errata de silkscreen (GPIO24/25 intercambiados). V2: añade CANbus
> (Katapult), 12 IO de gate y termistor. El pin de termistor y los pines CAN no
> están documentados en el repo: **por confirmar**.

### 4.6 BIGTREETECH MMB CAN V1.0 / V1.1 (STM32G0B1, 4 drivers EZ)

Placa de 4 drivers. Como **selectora** `DRIVER0=SEL`, `DRIVER1=GEAR`; como
**gear-per-gate** `DRIVER0..3` = `MMU_GEAR_STEP/_1/_2/_3` (4 bobinas). El único
cambio entre V1.0 y V1.1 es el enable de `DRIVER0` y el DIAG de `DRIVER3`.

| Función | Alias (selectora) | V1.0 | V1.1 |
|---|---|---|---|
| M1 STEP/DIR/ENABLE/UART/DIAG | `MMU_SEL_*` | PB15 / PB14 / !PA8 / PA10 / PA3 | PB15 / PB14 / **!PB8** / PA10 / PA3 |
| M2 STEP/DIR/ENABLE/UART/DIAG | `MMU_GEAR_*` | PD2 / PB13 / !PD1 / PC7 / PA4 | idem |
| M3 STEP/DIR/ENABLE/UART/DIAG | `MMU_GEAR_*_2` | PD0 / PD3 / !PA15 / PC6 / PB9 | idem |
| M4 STEP/DIR/ENABLE/UART/DIAG | `MMU_GEAR_*_3` | PB6 / PB7 / !PB5 / PA9 / **PB8** | PB6 / PB7 / !PB5 / PA9 / **PA8** |
| Servo | `MMU_SERVO` | PA0 | PA0 |
| Sensor de filamento | `MMU_GATE_SENSOR` | ^PA1 | ^PA1 |
| Neopixel | `MMU_NEOPIXEL` | PA2 | PA2 |
| Selector endstop | `MMU_SEL_ENDSTOP` | ^PC15 | ^PC15 |
| Pre-gate 0..5 | `MMU_PRE_GATE_*` | PC13, PC14, PB12, PB11, PB10, PB2 | idem |
| **I2C** | `MMU_I2C_SCL` / `MMU_I2C_SDA` | PB3 / PB4 (bus `i2c3`) | PB3 / PB4 |
| CAN (reservado) | — | PB0 (RX) / PB1 (TX) | idem |
| USB | — | PA11 / PA12 | idem |

> DIAG comparte pin con los endstops STP1-4 (sensorless homing). El mapeo de
> `SEL_ENDSTOP`/`PRE_GATE` sobre STP5-11 está **por confirmar** contra esquema.

### 4.7 BIGTREETECH MMB CAN V2.0 (STM32G0B1RET6, rediseño)

| Función | Alias (selectora) | Pin |
|---|---|---|
| M1 STEP/DIR/ENABLE/UART/DIAG | `MMU_SEL_*` | PD4 / PD3 / !PD5 / PB5 / PB9 |
| M2 STEP/DIR/ENABLE/UART/DIAG | `MMU_GEAR_*` | PC9 / PC8 / !PD2 / PB4 / PB8 |
| M3 STEP/DIR/ENABLE/UART/DIAG | `MMU_GEAR_*_2` | PC15 / PC11 / !PC10 / PB3 / PB7 |
| M4 STEP/DIR/ENABLE/UART/DIAG | `MMU_GEAR_*_3` | PC13 / PC12 / !PC14 / PD6 / PB6 |
| Servo 1 / Servo 2 | `MMU_SERVO` / `MMU_CUT_SERVO` | PA1 / PA0 |
| Sensor de filamento | `MMU_GATE_SENSOR` | ^PC2 |
| Neopixel | `MMU_NEOPIXEL` | PC3 |
| Selector endstop | `MMU_SEL_ENDSTOP` | ^PA15 |
| Pre-gate 0..14 | `MMU_PRE_GATE_*` | PA10, PD9, PD8 (endstops 2-4) + 12 IO header: PC7, PA9, PB12, PB10, PB1, PC5, PC6, PA8, PB11, PB2, PB0, PC4 |
| **I2C** | `MMU_I2C_SCL` / `MMU_I2C_SDA` | PC0 / PC1 (bus `i2c3`) |
| CAN (reservado) | — | PD0 (RX) / PD1 (TX) |
| USB | — | PA11 / PA12 |

> Rediseño completo frente a V1.x: CAN en PD0/PD1 y 2 servos. DC12-60 V.
> DIAG independiente por driver. Mapeo del header 2×7 **por confirmar**.

### 4.8 Conector I2C (identificación por placa)

Sólo las MMB CAN exponen un conector I2C dedicado; el resto de placas de esta
familia **no lo exponen** (o sus pines nativos están ocupados por otras
funciones). El sistema genera los alias uniformes `MMU_I2C_SCL` /
`MMU_I2C_SDA` (+ espejo `DM_*`) cuando la placa declara `capabilities.i2c: true`.

| Placa | ¿I2C? | Bus | SCL | SDA | Notas |
|---|---|---|---|---|---|
| BTT MMB CAN V1.0 / V1.1 | **Sí** | `i2c3` | PB3 | PB4 | Conector dedicado ("reserved for run-out/clog") |
| BTT MMB CAN V2.0 | **Sí** | `i2c3` | PC0 | PC1 | Conector dedicado |
| Mellow FLY-MMU | No | — | — | — | No documentado en config/pinout oficial |
| Mellow FLY-ERCF V1 | No | — | — | — | No documentado |
| Mellow FLY-ERCF V2 | No | — | — | — | No documentado |
| ERCF Easy Brd V1.1 | No usable | — | (PA9) | (PA8) | I2C nativo del XIAO ocupado por UART TMC (PA8) y STEP selector (PA9) |
| FYSETC ERB V1.0 | No | — | — | — | No documentado |
| FYSETC ERB V2.0 | No | — | — | — | No documentado |

> Las MMB exponen la lógica a **3.3 V** (no tolerante a 5 V en las E/S). El orden
> SCL/SDA del MMB se deduce de la convención de Klipper (`i2cN_<SCL>_<SDA>`); la
> serigrafía y los pines de alimentación (VCC/GND) del conector están **por
> confirmar** contra el esquemático.

#### 4.8.1 Periféricos I2C soportados

El conector I2C puede alojar sensores de humedad/temperatura, multiplexores I2C
(TCA9548A, etc.) y lectores RFID/NFC I2C (p. ej. PN532 en modo I2C). La placa
declara los dispositivos previstos en `i2c.devices`:

```yaml
i2c:
  bus: i2c3
  scl: PB3
  sda: PB4
  devices: [humidity_temperature, multiplexer, rfid]
```

El runtime expone los alias `MMU_I2C_SCL`/`MMU_I2C_SDA` para definir el
`[i2c_bus]` de Klipper y colgar de él los periféricos (temperatura/humedad vía
`[temperature_sensor]`/`[bme280]`, multiplexor y lectores NFC).

### 4.9 Uso dual pre-gate / post-gate

Los pines `PRE_GATE_n` pueden reutilizarse como `POST_GATE_n` (mismo conector
físico; el rol depende del cableado). La semántica:

- `PRE_GATE`: confirma la **presencia** de filamento en el gate.
- `POST_GATE`: confirma la **llegada** del filamento tras el recorrido.

Al activarse el `pre_gate` de un gate, la unidad carga filamento **hasta** que se
activa el `post_gate`. El sistema emite ambos alias apuntando al mismo pin y los
declara como compartición legítima (no generan conflicto):

```ini
MMU_PRE_GATE_0=PC6
MMU_POST_GATE_0=PC6
```

Se activa por placa con `dual_gate_sensors: true` en el YAML. Si un `PRE_GATE_n`
comparte pin con otra función (p. ej. `SHARED_EXIT`), la compartición se hereda
automáticamente al `POST_GATE_n`.

---

## 5. Integración de 4 bobinas por unidad

Cada placa/unidad Dog Matrix es **gear-per-gate y controla exactamente 4
bobinas** (`COILS_PER_UNIT = 4`). `build_pin_plan()` con `topology:
gear_per_gate` reserva, por unidad:

| Categoría | Recursos reservados (× 4 bobinas) |
|---|---|
| Control | STEP, DIR, ENABLE por bobina |
| Comunicación | UART por bobina |
| Sensores | DIAG, encoder, gate sensor, pre-gate |
| Periféricos | servo, cut servo, neopixel |

```python
from dog_matrix.boards import load_board, build_pin_plan, TOPOLOGY_GEAR_PER_GATE

plan = build_pin_plan(load_board("mellow_fly_mmu"), topology=TOPOLOGY_GEAR_PER_GATE)
len(plan.coils())          # -> 4
plan.coils()[3]["gear_step"]  # -> "PE4"
plan.resource_summary()["control"]  # STEP/DIR/ENABLE de las 4 bobinas
```

Los sistemas **selectores (ERCF)** admiten un número indeterminado de gates
(4, 9, 12, …) con un único gear compartido: `build_pin_plan(board, gates=12)`.

En multi-unidad, cada unidad es un MCU independiente y sus gates se numeran de
forma contigua: unidad 0 → gates 0-3, unidad 1 → gates 4-7, … (`DogMatrixUnit`
deriva `gates = coils_per_unit` cuando no se especifica `num_gates`).

---

## 6. Validación de conflictos y compatibilidad

- **`validate_board_definition(board)`**: detecta pines duplicados en la propia
  definición (p. ej. dos drivers compartiendo STEP).
- **`validate_pin_plan(plan)`**: detecta pines asignados a funciones distintas
  tras resolver el plan, ignorando el espejo `DM_*` y respetando `shared_pins`.
- **`SystemValidator.validate_board_plan(board_id, ...)`**: carga, construye y
  valida; devuelve `BoardValidationResult` con `passed`/`errors`.
- **Wizard**: antes de escribir, valida el plan y aborta con
  `ERR_CONFLICTING_PINS` si hay conflictos.

Reglas de compatibilidad Klipper/Happy Hare:

1. Los alias sólo mapean nombre→pin; los modificadores `!`, `^`, `~` se
   preservan en el valor del alias (válido en `[board_pins]`).
2. Los pares declarados en `shared_pins` (UART compartido, DIAG/ENDSTOP,
   SHARED_EXIT/PRE_GATE) **no** se reportan como conflicto.
3. Todo `PinPlan` requiere `MMU_*` para que una config Happy Hare pueda
   reutilizarlo sin cambios.

Ejecución de las pruebas asociadas:

```bash
PYTHONPATH=.dm_pylibs python -m pytest tests/unit/test_boards.py \
    tests/integration/test_board_generation.py -q
```

---

## 7. Guía de migración

### 7.1 Desde Happy Hare

1. Identifica tu `BOARD_TYPE_*` en la tabla §2.4 y su arquetipo Dog Matrix.
2. Selecciona la placa en el perfil (`hardware.board`) o con
   `hardware_map={"board": "<placa>"}` al generar.
3. Conserva tus `MMU_*` existentes: Dog Matrix emite los mismos alias, por lo
   que las configs HH siguen resolviendo.
4. Revisa los pares compartidos (`shared_pins`) de tu placa.
5. Ejecuta `dog-matrix validate` y corrige cualquier conflicto reportado.

### 7.2 Desde una config Dog Matrix previa (sin placa)

- Sin `hardware.board`, el generador mantiene el comportamiento anterior
  (`[dm_pins]` con pines directos `filament_gate_N`). No hay cambios
  incompatibles.
- Para adoptar el nuevo sistema: añade `hardware.board` al perfil (o
  `hardware.coils_per_unit`) y regenera. Los alias `DM_*`/`MMU_*` sustituyen a
  los pines directos en `[dm_pins]`.

### 7.3 Multi-unidad con 4 bobinas

- Define `board` (arquetipo gear-per-gate) y, si no usas `num_gates`, cada
  unidad obtendrá 4 gates automáticamente.
- Las asignaciones dispositivo→bobina persisten igual que antes
  (`docs/MULTI_UNIT.md`).

---

## 8. Especificación para desarrolladores

### 8.1 Añadir una placa nueva

1. Crea `config/boards/<id>.yaml` con `schema_version: 1` y los `drivers` +
   `pins` + `capabilities`.
2. Documenta los pares legítimamente compartidos en `shared_pins`.
3. Añade la entrada correspondiente a `happy_hare_board_catalog()` si existe un
   `BOARD_TYPE_*` equivalente.
4. Añade un test de mapeo en `tests/unit/test_boards.py`.

### 8.2 API pública (`dog_matrix.boards`)

| Símbolo | Descripción |
|---|---|
| `load_board(id, dir=None)` / `list_boards()` / `load_all_boards()` | Carga de definiciones |
| `build_pin_plan(board, gates, units, topology, coils_per_unit, ...)` | Resolución del plan |
| `PinPlan.aliases` / `.coils()` / `.resource_summary()` / `.resolve(alias)` | Consulta |
| `validate_pin_plan(plan)` / `validate_board_definition(board)` | Conflictos |
| `render_board_pins(plan, section="dogmatrix")` | Bloque `[board_pins]` |
| `happy_hare_board_catalog()` | Catálogo `BOARD_TYPE_*` de Happy Hare |
| `COILS_PER_UNIT` / `TOPOLOGY_SELECTOR` / `TOPOLOGY_GEAR_PER_GATE` | Constantes |

### 8.3 Cobertura de pruebas

| Fichero | Casos |
|---|---|
| `tests/unit/test_boards.py` | 17 casos: carga, alias HH, 4 bobinas, uniformidad, conflictos, render, catálogo, MMB (4 drivers + I2C), diferencia V1.0/V1.1, I2C por placa |
| `tests/integration/test_board_generation.py` | 5 casos: generación con/sin placa, determinismo, validador, 4 bobinas multi-unidad |

---

## 9. Roadmap / pendientes

| Prioridad | Tarea | Complejidad |
|---|---|---|
| P1 | Confirmar contra esquema los DIAG/PRE_GATE del FLY-MMU | Baja |
| P1 | Documentar pines de termistor/CAN del FYSETC ERB V2 | Baja |
| P1 | Confirmar SDA/SCL y endstops (STP5-11/header 2×7) de las MMB CAN contra esquema | Baja |
| P2 | Pinout completo de las placas `generic_*` (SKR Pico, EBB42, KMS, VVD, QIDI) | Media |
| P2 | Per-gate MCU (`MMU_HAS_PER_GATE_MCU`) con `DogMatrixUnit` por gate | Media |
| P3 | Validación de recursos energéticos (corriente agregada PSU) por plan | Alta |

---

## 10. Índice de ficheros

- [boards.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/klippy/extras/dog_matrix/boards.py)
- [config/boards/](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/config/boards)
- [units.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/klippy/extras/dog_matrix/units.py)
- [generator.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/installer/generator.py)
- [validator.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/installer/validator.py)
- [test_boards.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/tests/unit/test_boards.py)
- [test_board_generation.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/tests/integration/test_board_generation.py)
- [MULTI_UNIT.md](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/docs/MULTI_UNIT.md)
