# Dog Matrix MMU — Guía de Diagnóstico (Troubleshooting)

**Documento:** DOC-TROUBLESHOOTING · **Versión:** 0.1.0 · **Audiencia:** técnicos / soporte

Procedimientos de diagnóstico y resolución de incidencias frecuentes.

---

## 1. Herramientas de diagnóstico

| Herramienta | Uso |
|---|---|
| `bash install.sh doctor` | Estado del entorno y bindings CFFI |
| `bash install.sh preflight` | Comprobaciones previas al despliegue |
| `bash install.sh validate --dest DIR` | Integridad de la configuración |
| `DM_STATUS` | Estado resumido del MMU |
| `DM_TEST_CONFIG` | Revalidación del perfil en caliente |
| `DM_ENCODER ACTION=READ` | Lectura del encoder |
| `GET /server/dog_matrix/status` | Estado vía Moonraker |

### 1.1 Logs y evidencias

| Artefacto | Ruta por defecto |
|---|---|
| Log estructurado | `~/printer_data/logs/dog_matrix.jsonl` |
| Estado persistente | `~/printer_data/config/dog_matrix_state.json` |
| Snapshots de estado | `<state_store>.snapshots/` |
| Snapshots de despliegue | `<dest>/.dm_backups/` |
| Evidence bundles | `~/printer_data/evidence/` |

Generar y firmar un evidence bundle:

```python
from dog_matrix.diagnostics import Diagnostics
d = Diagnostics({"log_path": "...", "evidence_dir": "..."})
path = d.create_evidence_bundle()
print(d.sign_evidence(path))
```

---

## 2. Incidencias de toolchange

### 2.1 `ERR_VERIFY_FAILED` — filamento no detectado en toolhead

**Causas:** sensor de toolhead no activado; filamento no llegó al cabezal;
sensor invertido o mal calibrado.

**Diagnóstico:**
```gcode
DM_STATUS
DM_ENCODER ACTION=READ
```
**Solución:** verifica el sensor (polaridad NO/NC), recalibra `toolhead` con el
wizard (`run_hardware_calibration_steps(["toolhead"])`), y reintenta la carga.

### 2.2 `ERR_LOAD_FAILED` / `ERR_UNLOAD_FAILED`

**Causas:** obstrucción en bowden; tensión incorrecta; velocidad excesiva.

**Solución:** reduce `max_load_speed_mm_s` / `max_unload_speed_mm_s` en el
perfil; comprueba el recorrido; ejecuta `DM_RECOVER CODE=ERR_LOAD_FAILED`.

### 2.3 `ERR_SELECT_FAILED`

**Causas:** selector bloqueado o sin homing.

**Solución:**
```gcode
DM_HOME
```
Verifica el mecanismo y `selector_type` en el perfil.

### 2.4 `ERR_INVALID_GATE` / `ERR_INVALID_TOOL`

La tool/gate solicitados están fuera del rango del perfil. Revisa `topology.gates`
y `DM_GATE_MAP`.

### 2.5 `ERR_PHASE_TIMEOUT`

Una fase excedió su timeout. Aumenta el timeout correspondiente
(`StateMachine.phase_timeouts`) o investiga el bloqueo mecánico.

---

## 3. Detección de flujo (FlowGuard)

### 3.1 Falsos positivos de divergencia

**Síntomas:** `flowguard.errors` sube sin atasco real.

**Solución:**
- Aumenta `encoder_error_mm` en el perfil.
- Activa el modo adaptativo: `FlowGuard.set_adaptive_mode(True)`.
- Ajusta `flowguard_ratio_threshold`.
- Verifica que la resolución del encoder sea correcta (`DM_ENCODER ACTION=READ`).

### 3.2 No se detectan atascos

- Confirma que el encoder reporta movimiento (`get_raw_counts()` cambia).
- Revisa `hysteresis` (por defecto 3 violaciones consecutivas).
- Validar con inyección de fallos reales.

---

## 4. Sensores

### 4.1 Sensor no cambia de estado

En ausencia de hardware accesible, `SensorManager` mantiene el estado simulado.
Verifica el cableado y la definición del pin en `[dm_pins]`.

### 4.2 Rebote / ruido

Ajusta el debounce:
```python
manager.set_debounce_time("toolhead", 10.0)   # ms, rango 0.1–50.0
manager.enable_interrupt_mode("toolhead", True)
```

---

## 5. Despliegue e instalación

### 5.1 `ERR_CONFLICTING_PINS`

Dos usos comparten el mismo pin. Revisa `[dm_pins]` y `printer.cfg`.

```
[dm_pins]
encoder: main:PA1
filament_toolhead: main:PA2
filament_gate_0: main:PB0
```

### 5.2 `ERR_INVALID_PROFILE`

El perfil no cumple el esquema. Valida:

```bash
python -c "from dog_matrix.capabilities import Capabilities; print(Capabilities('profiles/box_turtle.yaml').validate())"
```

### 5.3 `ERR_ROLLBACK_EXECUTED`

El despliegue falló y se restauró el snapshot previo. Revisa el mensaje y los
logs; el estado anterior está intacto. Reintenta tras corregir la causa.

### 5.4 `ERR_DEPLOYMENT_TIMEOUT`

El despliegue superó `WizardContext.timeout_s` (300 s por defecto). Investiga
bloqueos de E/S; aumenta el timeout si es legítimo.

### 5.5 CFFI no disponible

`doctor` reporta `"cffi_native": "fallback Python activo"`. El sistema funciona
con el fallback, pero para mínimo jitter compila:

```bash
python -m installer.build_native
bash install.sh doctor
```

---

## 6. Moonraker y UI

### 6.1 Los endpoints no responden

- Comprueba que `moonraker/components/dog_matrix.py` está instalado y que
  `[dog_matrix]` está en `moonraker.conf`.
- Reinicia Moonraker.
- Verifica la conexión Klipper–Moonraker.

### 6.2 La UI no se actualiza tras reconexión

La notificación `dog_matrix:state` requiere que el método remoto
`dog_matrix_status` esté registrado. Revisa los logs de Moonraker.

### 6.3 Spoolman no sincroniza

**Síntomas:** `get_inventory_status().online == False`.

**Causas:** servidor inalcanzable; circuit breaker abierto; URL incorrecta.

**Solución:** verifica `DM_SPOOLMAN_URL`, revisa el endpoint
`/server/spoolman/` de Moonraker; tras 30 s el disyuntor se recupera; fuerza con
`DM_SPOOLMAN ACTION=SYNC`.

---

## 7. Estado y persistencia

### 7.1 `ChecksumError` al cargar el estado

El estado fue manipulado o corrompido. Restaura un snapshot:

```python
from dog_matrix.persistence import Persistence
p = Persistence("~/printer_data/config/dog_matrix_state.json")
print(p.list_snapshots())
p.restore_snapshot(p.list_snapshots()[-1])
```

### 7.2 Estado inconsistente tras corte de energía

La escritura es atómica y con checksum; si la última escritura quedó a medias,
`load()` devuelve el último estado válido o error. Recupera desde snapshot.

---

## 8. Preguntas frecuentes

**¿Necesito PyYAML en el host de Klipper?**
No. El runtime incluye un parser YAML mínimo y soporta JSON. PyYAML solo lo
requiere el instalador.

**¿Puedo usar los comandos `MMU_*` de Happy Hare?**
Sí, se registran alias (`MMU_STATUS`, `MMU_CHANGE_TOOL`, `MMU_LOAD`, …).

**¿Cómo migro desde Happy Hare?**
`bash install.sh migrate --source mmu_vars.cfg --dest <dir>`. Se preservan
bowden, toolhead y resolución de encoder.

**¿Cómo obtengo un paquete de evidencias para soporte?**
Ejecuta `create_evidence_bundle()` (sección 1.1) y adjunta el ZIP firmado.

**¿Dónde ajusto la velocidad de carga?**
En el perfil (`limits.max_load_speed_mm_s`) o en `[dm_calibration]` generado.

---

## 9. Escalado de incidencias

Adjunta siempre:

1. Salida de `dog-matrix doctor --json`.
2. Salida de `dog-matrix validate --dest DIR --json`.
3. `DM_STATUS`.
4. Evidence bundle firmado.
5. Perfil utilizado (`dog_matrix_profile.json`).

---

## 10. Referencias

- API y códigos de error: [API.md](API.md)
- Seguridad y runbook: [SAFETY.md](SAFETY.md)
- Configuración: [CONFIGURATION.md](CONFIGURATION.md)
