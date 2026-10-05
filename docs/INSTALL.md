# Dog Matrix MMU — Guía de Instalación

**Documento:** DOC-INSTALL · **Versión:** 0.1.0 · **Audiencia:** técnicos / integradores

Esta guía describe el despliegue de Dog Matrix MMU: requisitos, instalación del
instalador, uso del asistente (wizard), integración en Klipper y Moonraker, y
procedimientos de rollback y migración.

> Los tiempos y cifras de rendimiento citados son **objetivos de diseño**, no
> resultados medidos. Su validez queda condicionada a la campaña de pruebas
> hardware-in-the-loop (HIL).

---

## 1. Requisitos

### 1.1 Sistema

| Componente | Mínimo | Recomendado |
|---|---|---|
| Python | 3.8 | 3.10+ |
| Klipper | 0.10.0 | 0.12.0+ |
| Moonraker | 0.7.0 | 0.9.0+ |
| Compilador C (opcional, para CFFI) | gcc / clang | gcc 11+ |

El instalador requiere además las dependencias declaradas en `pyproject.toml`
(`PyYAML`, `jsonschema`). El runtime de Klipper **no** necesita `PyYAML`: el
módulo incluye un parser YAML mínimo y soporta perfiles en JSON.

### 1.2 Estructura entregada

```
dog-matrix-mmu/
├── pyproject.toml
├── install.sh
├── profiles/                    # perfiles YAML + schema.json
├── klippy/extras/dog_matrix/    # extensión Klipper
├── moonraker/components/        # componente Moonraker
├── installer/                   # CLI, wizard, generador, validador
├── config/base/                 # configuración base
└── tests/                       # suite de pruebas
```

---

## 2. Instalación del instalador

```bash
cd dog-matrix-mmu
pip install -e .            # instala el CLI 'dog-matrix' y dependencias
pip install -e ".[dev]"     # opcional: pytest, black, flake8
```

Verificación rápida:

```bash
dog-matrix --version
bash install.sh doctor      # comprueba entorno y bindings CFFI
```

---

## 3. Asistente de despliegue (wizard)

El asistente automatiza preflight, detección de MCU, calibración guiada,
generación de configuración, validación, escritura atómica y rollback.

```bash
# Despliegue interactivo
bash install.sh wizard --profile box_turtle --dest ~/printer_data/config

# Despliegue desatendido (CI / scripted)
bash install.sh wizard --profile ercf --dest ~/printer_data/config --headless

# Previsualización sin escribir en disco
bash install.sh wizard --profile ercf --dest ~/printer_data/config --dry-run
```

### 3.1 Subcomandos del CLI

| Comando | Descripción |
|---|---|
| `preflight` | Comprueba entorno (Python, perfiles, destino, PyYAML, jsonschema, CFFI, Klipper/Moonraker, dispositivos serie) |
| `wizard` | Despliegue completo (preflight + calibración + generación + validación + rollback) |
| `generate` | Genera la configuración sin ejecutar la calibración |
| `validate` | Valida la integridad de la configuración desplegada |
| `apply` | Aplica configuración con snapshot previo y rollback automático |
| `rollback` | Revierte al último snapshot (o a uno concreto con `--snapshot`) |
| `migrate` | Migra desde Happy Hare / AFC (`--source mmu_vars.cfg`) |
| `doctor` | Diagnóstico del entorno y de los bindings nativos |

Opciones comunes: `--dest DIR`, `--json` (salida estructurada).

### 3.2 Archivos generados

En `--dest` se escriben:

| Archivo | Contenido |
|---|---|
| `dog_matrix_generated.cfg` | Sección `[dm_pins]` y `[dm_calibration]` |
| `dog_matrix_macros.cfg` | Macros G-code (`DM_STATUS`, `DM_CHANGE`, …) |
| `dog_matrix_profile.json` | Snapshot JSON del perfil (usado por el runtime) |
| `moonraker_dog_matrix.conf` | Fragmento para `moonraker.conf` |
| `dog_matrix_manifest.json` | Hash SHA-256 de cada archivo |

La generación es **determinista**: con la misma entrada y `generated_at` fijo se
obtiene el mismo hash.

### 3.3 Calibración guiada

Pasos ejecutados por `wizard` (en HIL validan sensores/actuadores reales):

1. `bowden` — longitud de tubo bowden / reverse bowden
2. `toolhead` — distancias internas del cabezal
3. `encoder` — resolución de encoder (pulsos por mm)

---

## 4. Integración en Klipper

1. Copiar/symlinkear la extensión en Klipper:

```bash
ln -s "$(pwd)/klippy/extras/dog_matrix" ~/klipper/klippy/extras/dog_matrix
```

2. Incluir la configuración base en `printer.cfg`:

```ini
[include dog_matrix.cfg]           # config/base/dog_matrix.cfg
[include dog_matrix_macros.cfg]    # generado en --dest
[include dog_matrix_generated.cfg] # generado en --dest
```

3. Ajustar la sección `[dog_matrix]` (perfil, rutas, capacidades). Ver
   [CONFIGURATION.md](CONFIGURATION.md).

4. `FIRMWARE_RESTART` en Klipper.

Comprobación:

```gcode
DM_STATUS
DM_TEST_CONFIG
```

---

## 5. Integración en Moonraker

1. Copiar el componente:

```bash
cp moonraker/components/dog_matrix.py ~/moonraker/moonraker/components/dog_matrix.py
```

2. Añadir a `moonraker.conf` (ver `moonraker_dog_matrix.conf` generado):

```ini
[dog_matrix]
enable_file_preprocessor: true
enable_toolchange_next_pos: true
update_spoolman_location: true
```

3. Reiniciar Moonraker.

---

## 6. Migración desde Happy Hare / AFC

```bash
# Previsualizar la migración (sin escribir)
bash install.sh migrate --source ~/printer_data/config/mmu_vars.cfg --dry-run

# Migrar y escribir en el destino
bash install.sh migrate --source ~/printer_data/config/mmu_vars.cfg \
                        --dest ~/printer_data/config
```

Se preservan longitudes de bowden, distancia de toolhead y resolución de
encoder del sistema origen. Se genera `dog_matrix_migration_diff.json` con el
detalle.

---

## 7. Rollback y recuperación

Antes de cualquier escritura, `apply`/`wizard` crean un snapshot en
`<dest>/.dm_backups/<id>/` y marcan el despliegue como pendiente
(`<dest>/.dm_pending_snapshot`).

```bash
bash install.sh rollback --dest ~/printer_data/config            # último snapshot
bash install.sh rollback --dest ~/printer_data/config --snapshot 20260101T120000-pre-apply
```

Si la verificación posterior al despliegue falla, el rollback se ejecuta
**automáticamente** y se devuelve el código `ERR_ROLLBACK_EXECUTED`.

---

## 8. Aceleración nativa (CFFI)

Opcional. Sin ella, el runtime usa el fallback Python (misma semántica).

```bash
python -m installer.build_native
```

Compila `dog_matrix._dm_native` a partir de `csrc/dm_native.c`. Verificar con
`bash install.sh doctor` → `"cffi_integrity": true`.

---

## 9. Variables de entorno

| Variable | Uso |
|---|---|
| `DM_PROJECT_ROOT` | Raíz del proyecto (perfiles/plantillas) |
| `DM_DEST` | Destino por defecto si no se pasa `--dest` |
| `DM_LOG_LEVEL` | `debug` / `info` / `warning` / `error` / `critical` |
| `DM_LOG_PATH` | Ruta del log JSON Lines |
| `DM_EVIDENCE_DIR` | Directorio de evidence bundles |
| `DM_EVIDENCE_KEY` | Clave HMAC para firmar evidence bundles |
| `DM_MOONRAKER_API_KEY` | API key de Moonraker (referenciada en `moonraker.conf`) |
| `DM_SPOOLMAN_URL` | URL base de Spoolman (vía Moonraker) |

---

## 10. Desinstalación

1. Retirar los `[include ...]` de `printer.cfg`.
2. Eliminar `klippy/extras/dog_matrix/` y `moonraker/components/dog_matrix.py`.
3. `FIRMWARE_RESTART` + reinicio de Moonraker.
4. Opcional: restaurar el snapshot previo con `install.sh rollback`.

---

## 11. Verificación de la instalación

```bash
bash install.sh preflight
bash install.sh validate --dest ~/printer_data/config
bash install.sh doctor
pytest -q          # si se instaló el extra [dev]
```
