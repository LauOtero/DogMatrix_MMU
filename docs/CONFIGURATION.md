# Dog Matrix MMU — Guía de Configuración

**Documento:** DOC-CONFIGURATION · **Versión:** 0.1.0 · **Audiencia:** integradores / operadores

Describe la configuración del módulo Klipper `[dog_matrix]`, el componente
Moonraker, los perfiles de hardware, la persistencia y las variables de entorno.

---

## 1. Estructura de configuración

| Nivel | Archivo | Responsable |
|---|---|---|
| Perfil de hardware | `profiles/<perfil>.yaml` | Usuario (o generado) |
| Config del módulo | `config/base/dog_matrix.cfg` | Usuario |
| Config generada | `dog_matrix_generated.cfg` | Instalador (no editar) |
| Macros | `dog_matrix_macros.cfg` | Instalador (no editar) |
| Componente Moonraker | `moonraker.conf` → `[dog_matrix]` | Usuario |

---

## 2. Sección `[dog_matrix]` (Klipper)

Ejemplo en `printer.cfg`:

```ini
[dog_matrix]
profile: box_turtle
state_store: ~/printer_data/config/dog_matrix_state.json
log_level: info
enable_endless_spool: true
enable_spoolman: false
enable_led: true
enable_nfc: false
enable_purge: false
auto_recover: false
```

### 2.1 Opciones

| Opción | Tipo | Default | Efecto |
|---|---|---|---|
| `profile` | str | `box_turtle` | Nombre del perfil en `profiles/` |
| `profile_path` | str | — | Ruta explícita a un perfil YAML/JSON (tiene prioridad sobre `profile`) |
| `profiles_dir` | str | `<repo>/profiles` | Directorio de búsqueda de perfiles |
| `state_store` | str | `~/printer_data/config/dog_matrix_state.json` | Ruta del estado persistente |
| `log_level` | str | `info` | `debug`/`info`/`warning`/`error`/`critical` |
| `log_path` | str | `~/printer_data/logs/dog_matrix.jsonl` | Log estructurado JSON Lines |
| `evidence_dir` | str | `~/printer_data/evidence` | Directorio de evidence bundles |
| `enable_led` | bool | `true` | Instancia el control de LEDs |
| `enable_nfc` | bool | `false` | Instancia el lector NFC/RFID |
| `enable_spoolman` | bool | `false` | Instancia el adaptador Spoolman |
| `enable_purge` | bool | `false` | Añade la fase de purga al toolchange |
| `auto_recover` | bool | `false` | Permite recuperación sin confirmación interactiva |
| `enable_flowguard` | bool | `false` | **Reservado** (FlowGuard se expone siempre) |
| `enable_endless_spool` | bool | `true` | **Reservado** (la disponibilidad depende del perfil) |

> **Nota:** las opciones marcadas como *reservadas* se aceptan y persisten, pero
> su política no está cableada todavía; la funcionalidad asociada se controla por
> capacidades del perfil.

### 2.2 Resolución del perfil

Orden de prioridad:
1. `profile_path` (si está definido).
2. `profile` + `profiles_dir` buscando `<nombre>.yaml`, `<nombre>.yml`, `<nombre>.json`.
3. Por defecto, `profiles/` relativo a la raíz del repositorio.

### 2.3 Inclusión de archivos

```ini
[include dog_matrix.cfg]
[include dog_matrix_macros.cfg]
[include dog_matrix_generated.cfg]
```

---

## 3. Configuración generada

`dog_matrix_generated.cfg` (no editar a mano):

```ini
[dm_pins]
encoder: main:PA1
filament_toolhead: main:PA2
filament_gate_0: main:PB0
...

[dm_calibration]
encoder_resolution: 0.45
bowden_length: 600
purge_length: 25
tip_form_length: 8
max_load_speed_mm_s: 80
max_unload_speed_mm_s: 100
max_distance_mm: 1500
sensor_timeout_ms: 500
encoder_error_mm: 5
```

`dog_matrix_profile.json` es el snapshot del perfil que consume el runtime y el
validador (`SystemValidator.validate_schema`).

### 3.1 Configuración de pines por placa

Si el perfil declara `hardware.board` (o se pasa `board` al generador), la
sección anterior se sustituye por alias uniformes y se añade un bloque
`[board_pins dogmatrix]`:

```ini
[board_pins dogmatrix]
mcu: mmu
aliases:
  MMU_GEAR_STEP=gpio7
  DM_GEAR_STEP=gpio7
  ...

[dm_pins]
gear_step: DM_GEAR_STEP
...
```

Los alias `MMU_*` son compatibles con Happy Hare. Consulta
[PIN_CONFIGURATION.md](PIN_CONFIGURATION.md) para el mapeo por placa, la
integración de 4 bobinas por unidad y la guía de migración.

---

## 4. Componente Moonraker

```ini
[dog_matrix]
enable_file_preprocessor: true
enable_toolchange_next_pos: true
update_spoolman_location: true
```

| Opción | Tipo | Default | Descripción |
|---|---|---|---|
| `enable_file_preprocessor` | bool | `true` | Preprocesado de archivos G-code |
| `enable_toolchange_next_pos` | bool | `true` | Posicionamiento previo al siguiente toolchange |
| `update_spoolman_location` | bool | `true` | Actualiza la ubicación de bobina en Spoolman |

---

## 5. Persistencia de estado

`state_store` guarda un sobre con checksum:

```json
{
  "schema_version": 1,
  "saved_at": 1767225600.0,
  "checksum": "<sha256 del campo data>",
  "data": {
    "current_gate": 3,
    "current_tool": 3,
    "ttg_map": [0, 1, 2, 3],
    "gate_status": ["available", "unknown", "..."],
    "counters": {"toolchanges": 12, "errors": 0}
  }
}
```

- Escritura **atómica** (temporal + `fsync` + renombrado).
- Verificación de **checksum SHA-256** en cada lectura.
- Snapshots en `<state_store>.snapshots/` con rotación (`rotate_backups`).

---

## 6. Logging y evidencias

- Formato: **JSON Lines** (un objeto por línea), compatible con ELK/Loki.
- Campos: `ts`, `level`, `component`, `event` + campos de contexto.
- Rotación automática por tamaño (`log_max_bytes`, `log_backups`).
- Evidence bundle: ZIP con `logs/dog_matrix.jsonl`, `manifest.json` (hashes) y
  `manifest.sig` (HMAC-SHA256 si `DM_EVIDENCE_KEY` está definida).

---

## 7. Spoolman

Activación:

```ini
[dog_matrix]
enable_spoolman: true
```

El adaptador usa el endpoint de Moonraker
`/server/spoolman/...` (timeout 5 s) con **circuit breaker** (3 fallos → abre,
reintento tras 30 s) y caché local. Ante indisponibilidad, opera en modo caché.

---

## 8. Variables de entorno

| Variable | Descripción |
|---|---|
| `DM_PROJECT_ROOT` | Raíz del proyecto |
| `DM_DEST` | Destino por defecto del instalador |
| `DM_LOG_LEVEL` | Nivel de log |
| `DM_LOG_PATH` | Ruta del log |
| `DM_EVIDENCE_DIR` | Directorio de evidencias |
| `DM_EVIDENCE_KEY` | Clave HMAC de firma |
| `DM_MOONRAKER_API_KEY` | API key de Moonraker |
| `DM_SPOOLMAN_URL` | URL base de Spoolman |

Ejemplo (perfil de shell):

```bash
export DM_LOG_LEVEL="info"
export DM_EVIDENCE_DIR="~/printer_data/evidence"
export DM_EVIDENCE_KEY="$(openssl rand -hex 32)"
export DM_SPOOLMAN_URL="http://spoolman.local:7912"
```

---

## 9. Configuración en caliente

`DM_TEST_CONFIG` revalida el perfil activo sin reiniciar:

```gcode
DM_TEST_CONFIG
```

Para reasignar tool→gate sin reinicio:

```gcode
DM_REMAP_TTG TOOL=2 GATE=5
```

---

## 10. Referencias

- Perfiles: [PROFILES.md](PROFILES.md)
- Comandos y API: [API.md](API.md)
- Instalación: [INSTALL.md](INSTALL.md)
- Seguridad: [SAFETY.md](SAFETY.md)
