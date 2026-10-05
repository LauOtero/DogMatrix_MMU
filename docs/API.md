# Dog Matrix MMU — Referencia de API

**Documento:** DOC-API · **Versión:** 0.1.0 · **Audiencia:** desarrolladores / integradores

Documenta la superficie pública: comandos G-code, endpoints Moonraker,
objeto de estado, interfaces de los módulos Python y catálogo de códigos de error.

---

## 1. Objeto de estado (`dog_matrix`)

Expuesto por `DogMatrixCore.get_status(eventtime)` y consultable vía Moonraker.

| Campo | Tipo | Descripción |
|---|---|---|
| `state` | str | Estado FSM (`IDLE`, `LOAD`, `COMPLETED`, `FAILED`, …) |
| `gate` | int \| null | Gate activo (0-based) |
| `tool` | int \| null | Tool activa (lógica) |
| `gates` | int | Número de gates del perfil |
| `ttg_map` | int[] | Mapa tool→gate (`ttg_map[tool] = gate`) |
| `gate_status` | str[] | Estado por gate (`unknown`/`available`/`empty`/`error`) |
| `counters` | dict | `toolchanges`, `loads`, `unloads`, `errors` |
| `flowguard` | dict | Estadísticas de FlowGuard |
| `version` | str | Versión del software |
| `profile` | str | `profile_id` activo |

Ejemplo:

```json
{
  "state": "IDLE",
  "gate": 3,
  "tool": 3,
  "gates": 8,
  "ttg_map": [0, 1, 2, 3, 4, 5, 6, 7],
  "gate_status": ["available", "available", "unknown", "available"],
  "counters": {"toolchanges": 12, "loads": 12, "unloads": 11, "errors": 0},
  "flowguard": {"evaluations": 120, "violations": 0, "errors": 0, "adaptive": false},
  "version": "0.1.0",
  "profile": "dog_matrix.box_turtle.v1"
}
```

---

## 2. Comandos G-code

Cada comando `DM_*` tiene un **alias** `MMU_*` (compatibilidad con Happy Hare).

| Comando | Alias | Parámetros | Descripción |
|---|---|---|---|
| `DM_STATUS` | `MMU_STATUS` | — | Muestra el estado resumido |
| `DM_CHANGE` | `MMU_CHANGE_TOOL` | `TOOL=<int>` | Cambio de herramienta completo |
| `DM_LOAD` | `MMU_LOAD` | — | Carga el filamento del gate activo |
| `DM_UNLOAD` | `MMU_UNLOAD` | — | Descarga el filamento |
| `DM_RECOVER` | `MMU_RECOVER` | `CODE=<str>` | Recuperación tras fallo |
| `DM_HOME` | `MMU_HOME` | — | Homing del selector |
| `DM_ENCODER` | `MMU_ENCODER` | `ACTION=READ\|RESET` | Lectura/reset del encoder |
| `DM_GATE_MAP` | `MMU_GATE_MAP` | `[GATE=<int>] [STATUS=<str>]` | Lista o actualiza gates |
| `DM_REMAP_TTG` | `MMU_REMAP_TTG` | `TOOL=<int> GATE=<int>` | Remapeo tool→gate |
| `DM_SPOOLMAN` | `MMU_SPOOLMAN` | `[ACTION=STATUS\|SYNC]` | Consulta/sincroniza Spoolman |
| `DM_ENDLESS_SPOOL` | `MMU_ENDLESS_SPOOL` | — | Estado de grupos EndlessSpool |
| `DM_TEST_CONFIG` | `MMU_TEST_CONFIG` | — | Revalida la configuración |

### 2.1 Ejemplos

```gcode
DM_STATUS
DM_CHANGE TOOL=2
DM_GATE_MAP
DM_GATE_MAP GATE=3 STATUS=available
DM_REMAP_TTG TOOL=2 GATE=5
DM_ENCODER ACTION=READ
DM_RECOVER CODE=ERR_VERIFY_FAILED
```

### 2.2 Alias `T0`–`Tn`

Los comandos `T0`–`Tn` pertenecen al `toolhead` de Klipper. Para enrutarlos al
MMU, definir macros en `dog_matrix_macros.cfg`:

```ini
[gcode_macro T2]
gcode:
  DM_CHANGE TOOL=2
```

---

## 3. API Moonraker

Componente: `moonraker/components/dog_matrix.py`.

### 3.1 Endpoints HTTP

| Método | Ruta | Cuerpo | Respuesta |
|---|---|---|---|
| `GET` | `/server/dog_matrix/status` | — | `{"status": {...}}` |
| `POST` | `/server/dog_matrix/toolchange` | `{"tool": 2}` | `{"ok": true, "tool": 2, "result": "..."}` |
| `POST` | `/server/dog_matrix/recover` | `{"code": "ERR_..."}` | `{"ok": true, "result": "..."}` |

Ejemplo:

```bash
curl -s http://127.0.0.1:7125/server/dog_matrix/status | jq
curl -s -X POST http://127.0.0.1:7125/server/dog_matrix/toolchange \
     -H 'Content-Type: application/json' -d '{"tool": 2}'
```

### 3.2 Notificación WebSocket

Canal: **`dog_matrix:state`**. Payload: el objeto de estado (sección 1).

Klipper empuja actualizaciones a Moonraker mediante el método remoto
`dog_matrix_status`, que Moonraker reemite como notificación.

### 3.3 Configuración del componente

```ini
[dog_matrix]
enable_file_preprocessor: true
enable_toolchange_next_pos: true
update_spoolman_location: true
```

---

## 4. Interfaces de los módulos Python

### 4.1 Núcleo y control

| Módulo | Clase | Métodos principales |
|---|---|---|
| `core` | `DogMatrixCore` | `get_status(eventtime)`, `snapshot_state()`, `cmd_DM_*` |
| `state_machine` | `StateMachine` | `transition(new_state, op_id)`, `execute_toolchange(gate, tool)`, `abort(reason)`, `get_state()` |
| `recovery` | `Recovery` | `recover_from_failure(info)`, `revalidate_state()`, `request_confirmation(msg)` |
| `capabilities` | `Capabilities` | `load()`, `validate()`, `get(key)`, `has_capability(name)` |
| `persistence` | `Persistence` | `load()`, `save(data)`, `create_snapshot()`, `restore_snapshot(id)`, `validate_checksum(id)`, `rotate_backups(n)` |
| `diagnostics` | `Diagnostics` | `log_event(level, component, event, **kw)`, `create_evidence_bundle(dir)`, `get_recent_errors(n)`, `sign_evidence(path)` |

### 4.2 Hardware

| Módulo | Clase | Métodos principales |
|---|---|---|
| `motion` | `Motion` | `load_filament(mm, mm_s)`, `unload_filament(mm, mm_s)`, `move_selector(pos)`, `park_toolhead()`, `purge(mm)`, `get_trajectory_stats()`, `set_jerk_limit(j)`, `enable_s_curve(b)` |
| `selector` | `Selector` | `home()`, `select_gate(gate)`, `get_position()`, `is_at_gate(gate)` |
| `sensors` | `SensorManager` | `read(name)`, `is_present(name)`, `register_callback(name, cb)`, `get_raw_gpio_state()`, `set_debounce_time(name, ms)`, `enable_interrupt_mode(name, b)`, `poll()` |
| `encoder` | `Encoder` | `read_position()`, `read_velocity()`, `reset()`, `get_error_mm(req)`, `get_raw_counts()`, `set_filter_alpha(a)` |
| `flowguard` | `FlowGuard` | `evaluate(req, meas)`, `update_thresholds(material, temp)`, `reset()`, `get_statistics()`, `set_adaptive_mode(b)` |

### 4.3 Subsistemas

| Módulo | Clase | Métodos principales |
|---|---|---|
| `spoolman` | `SpoolManager` | `get_spool(id)`, `assign_spool_to_gate(id, gate, force)`, `sync_gate_map()`, `update_spool_consumption(id, len)`, `handle_nfc_tag(uid)`, `get_inventory_status()` |
| `led_system` | `LEDSystem` | `set_gate_status_color(gate, status)`, `set_filament_color(gate, hex)`, `set_system_state_animation(state, type)`, `clear_all()`, `update(eventtime)` |
| `nfc_rfid` | `NFCReader` | `scan_tag(timeout_s)`, `write_spool_tag(id, info)`, `register_tag_callback(cb)`, `get_hardware_status()` |
| `klipperscreen_panel` | `KlipperScreenMMUPanel` | `render_gates_view(status)`, `render_spoolman_selector(spools)`, `show_calibration_wizard_step(info)`, `trigger_toolchange(tool)`, `show_error_dialog(error)` |

### 4.4 Capa nativa (CFFI)

Módulo `_native` (fallback Python transparente):

| Función | Descripción |
|---|---|
| `iir_step(prev, new, alpha)` | Filtro IIR de primer orden |
| `median3(a, b, c)` | Filtro de mediana (3 muestras) |
| `divergence(req, meas)` | `(diferencia_mm, ratio)` |
| `debounce(stable, cand, since, now, raw, debounce_s)` | Máquina de debounce temporal |
| `plan_trajectory(...)` | Perfil trapezoidal / S-curve |
| `native_available()` | Indica si la biblioteca C está cargada |

---

## 5. API del instalador

Módulos en `installer/`:

| Clase | Métodos |
|---|---|
| `InstallationWizard` | `probe_hardware_environment()`, `detect_mcu_devices()`, `select_mmu_profile(id)`, `run_hardware_calibration_steps(steps)`, `generate_and_install_configs(dir)`, `verify_system_integrity(dir)`, `execute_atomic_rollback(id)` |
| `ConfigGenerator` | `render_mmu_config(profile, hw)`, `render_macros_config(caps)`, `render_moonraker_extension(opts)`, `calculate_config_hash(content)`, `write_atomic_config_bundle(bundle, dir)` |
| `SystemValidator` | `validate_schema(dict, v)`, `validate_pin_conflicts(map)`, `validate_version_compatibility(env)`, `perform_realtime_health_check()`, `verify_cffi_bindings_integrity()` |
| `ConfigMigrator` | `detect_legacy_configuration(dir)`, `migrate_from_happy_hare(path)`, `migrate_schema_version(dict, from, to)`, `create_migration_backup(dir)` |
| `BackupManager` | `create(label)`, `list()`, `restore(id)` |
| `RollbackManager` | `prepare(label)`, `pending()`, `rollback(id)`, `commit()` |
| `Preflight` | `run()` → `PreflightReport` |

---

## 6. Catálogo de códigos de error

### 6.1 Toolchange (`state_machine`)

| Código | Significado |
|---|---|
| `ERR_INVALID_GATE` | Gate fuera de rango |
| `ERR_INVALID_TOOL` | Tool inválida |
| `ERR_UNLOAD_FAILED` | Fallo al descargar filamento |
| `ERR_SELECT_FAILED` | Selector no alcanzó el gate |
| `ERR_LOAD_FAILED` | Fallo al cargar filamento |
| `ERR_VERIFY_FAILED` | Filamento no detectado en toolhead |
| `ERR_PURGE_FAILED` | Fallo en purga |
| `ERR_PHASE_TIMEOUT` | Fase excedió su timeout |
| `ERR_INTERNAL` | Error interno / transición inválida |

### 6.2 Wizard / despliegue

| Código | Significado |
|---|---|
| `ERR_HW_NOT_FOUND` | Hardware no detectado |
| `ERR_INVALID_PROFILE` | Perfil incompatible/inválido |
| `ERR_CONFLICTING_PINS` | Conflicto de pines GPIO |
| `ERR_DEPLOYMENT_TIMEOUT` | Despliegue excedió el tiempo límite |
| `ERR_ROLLBACK_EXECUTED` | Rollback automático ejecutado |

### 6.3 Mensajes de estado

| Prefijo | Módulo |
|---|---|
| `GEN_SUCCESS` / `GEN_CONFLICT` / `GEN_VERIFIED` / `GEN_ERROR` | `generator` |
| `VALIDATION_PASS` / `VALIDATION_WARNING` / `VALIDATION_FAIL` | `validator` |
| `MIGRATE_START` / `MIGRATE_BACKUP` / `MIGRATE_SUCCESS` / `MIGRATE_ROLLBACK` / `MIGRATE_SKIPPED` | `migrator` |

---

## 7. Referencias

- Configuración: [CONFIGURATION.md](CONFIGURATION.md)
- Perfiles: [PROFILES.md](PROFILES.md)
- Seguridad: [SAFETY.md](SAFETY.md)
- Diagnóstico: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
