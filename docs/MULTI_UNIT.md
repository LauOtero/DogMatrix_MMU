# Sistema Multi-Unidad — Dog Matrix MMU

> **Documento:** DOC-DM-MULTI-UNIT · **Versión:** 1.0 · **Fecha:** 2026-10-05
> **Componente:** `klippy/extras/dog_matrix/units.py`
> **Estado:** Implementado y verificado (22 pruebas específicas, suite total en verde)

---

## 1. Objetivo

Permitir que una misma impresora opere **varias unidades MMU/AFC** (varios
dispositivos físicos), cada una con su bloque de bobinas/gates, coordinadas por
un único componente:

- **Detección automática** de dispositivos por **USB** y **CANbus**, con escaneo
  continuo y eventos de conexión/desconexión.
- **Asignación secuencial** de un número de bobina (*spool number*) a cada
  dispositivo **en el orden de su primera conexión**.
- **Persistencia permanente** de la asignación, estable frente a reinicios,
  desconexiones y reconexiones.
- **Refactorización** de la lógica multi-unidad en `DogMatrixController` y
  `DogMatrixUnit` (modularidad, mantenibilidad, escalabilidad).

---

## 2. Arquitectura

```
                    +-------------------------------------------+
                    |            DogMatrixController            |
                    |  (orquesta, arbitra toolhead, expone API) |
                    +-------------------------------------------+
                       |             |                  |
        +--------------+     +-------+-------+   +------+---------+
        | CompositeScanner     | DeviceDiscovery|   | UnitAssignment |
        |  - UsbDeviceScanner  |  (poll/eventos)|   | Store (persist)|
        |  - CanDeviceScanner  +----------------+   +----------------+
        +--------------+
                       |
                    +--+-------------------------------------------+
                    |             DogMatrixUnit[]                   |
                    | spool_number, gates, gate_offset, estado       |
                    +-----------------------------------------------+
```

### 2.1 Clases principales (`units.py`)

| Clase | Responsabilidad |
|---|---|
| `DiscoveredDevice` | Representa un dispositivo detectado (id, interfaz, dirección). |
| `DeviceScanner` | Contrato de escaneo. Implementaciones: `UsbDeviceScanner`, `CanDeviceScanner`, `CompositeScanner`. |
| `DeviceDiscovery` | Escaneo + reconciliación conexión/desconexión + emisión de `UnitEvent`. |
| `UnitAssignmentStore` | Persistencia atómica (checksum) de `device_id → spool_number`. |
| `DogMatrixUnit` | Modelo de una unidad: número de bobina, gates, offset global y estado. |
| `DogMatrixController` | Fachada: descubrimiento, asignación, arbitraje del toolhead y `get_status()`. |
| `build_default_controller` | Construye el controller desde la configuración de Klipper. |

### 2.2 Flujo de datos

```
scanners.scan() ─► DeviceDiscovery.poll()
      │                    │
      │  dispositivo nuevo │  ya conocido
      ▼                    ▼
UnitAssignmentStore.ensure(id)     UnitAssignmentStore.get(id)
      │  (primera vez → nº secuencial; si no → conserva el asignado)
      ▼
DogMatrixUnit(connected=True) ──► UnitEvent(connected/assigned/disconnected)
      │
      ▼
DogMatrixController._recompute_offsets() ──► gate_offset por unidad
      │
      ▼
core.get_status()["units"]  /  comando DM_UNIT  /  callback _DM_UNIT_CHANGED
```

---

## 3. Detección automática

### 3.1 USB
`UsbDeviceScanner` lista los enlaces de `/dev/serial/by-id` y filtra por patrón
(por defecto `DogMatrix`). El **identificador estable** del dispositivo es el
nombre del enlace (`usb:<by-id-name>`), que no cambia entre reconexiones.

### 3.2 CANbus
`CanDeviceScanner` obtiene los UUIDs de un proveedor inyectable. En producción se
alimenta de la lista `can_uuids` de la configuración (o de la enumeración de MCUs
CAN de Klipper). El identificador es `can:<uuid>`.

### 3.3 Escaneo continuo
`DogMatrixController.start(reactor)` registra un temporizador de Klipper con
periodo `unit_scan_interval_s` (por defecto 5 s). Si no hay reactor disponible,
el escaneo se ejecuta una vez en `klippy:ready` y bajo demanda con `DM_UNIT`.

Cada transición genera un `UnitEvent` y se traduce a la macro/callback
`_DM_UNIT_CHANGED ACTION=<connected|disconnected|assigned> DEVICE_ID=... SPOOL_NUMBER=...`.

---

## 4. Asignación secuencial

Al detectar por **primera vez** un dispositivo, `UnitAssignmentStore.ensure()`
le asigna `max(valores_existentes) + 1` (empezando en `0`). El orden es el orden
de primera conexión observado por el scanner (los nombres USB se ordenan de
forma determinista).

Ejemplo:

| Orden de primera conexión | device_id | spool_number |
|---|---|---|
| 1º | `usb:usb-DogMatrix_MMU-1-if00` | 0 |
| 2º | `usb:usb-DogMatrix_MMU-2-if00` | 1 |
| 3º | `can:1a2b3c` | 2 |

### 4.1 Bloques de gates
Cada unidad recibe un bloque contiguo de gates globales:
`gate_offset = Σ gates de las unidades con spool_number menor`. Con 2 unidades de
4 gates y orden 0/1, los offsets son `0` y `4`.

---

## 5. Persistencia permanente

La asignación se guarda en un fichero JSON con escritura **atómica** (`fsync` +
`rename`) y **checksum SHA-256** (reutilizando `Persistence`). Ruta por defecto:
`<dir de state_store>/dog_matrix_units.json` (configurable con `units_store`).

Reglas de estabilidad:

- Una **desconexión** NO libera ni reordena el número: la unidad pasa a
  `connected=False` conservando su `spool_number`.
- Una **reconexión** restaura el mismo número.
- Un **reinicio** recarga las asignaciones del fichero.
- La asignación **solo cambia** en dos escenarios:
  1. **Borrado completo** de la configuración (`DM_UNIT ACTION=CLEAR`).
  2. **Asignación manual** del usuario (parámetro `unit_assignments` o
     `DM_UNIT ACTION=ASSIGN`). La asignación manual tiene prioridad y se persiste.

---

## 6. Arbitraje del toolhead compartido

Varias unidades comparten el toolhead/extrusor. `DogMatrixController` implementa
acceso exclusivo:

- `acquire_toolhead(unit_id)` → `True` si estaba libre o ya lo posee esa unidad.
- `release_toolhead(unit_id)` → libera si es el propietario.
- `select_unit(unit_id)` → marca la unidad activa.

---

## 7. Configuración (Klipper)

Añadir a `[dog_matrix]` (ver `config/base/dog_matrix.cfg`):

```ini
[dog_matrix]
enable_multi_unit: true

# Descubrimiento
usb_scan_dir: /dev/serial/by-id
usb_scan_pattern: DogMatrix
enable_usb_scan: true
enable_can_scan: true
can_uuids: ["1a2b3c4d", "5e6f7a8b"]   # opcional
unit_scan_interval_s: 5.0

# Persistencia de asignaciones
units_store: ~/printer_data/config/dog_matrix_units.json

# Asignación manual (opcional; tiene prioridad y se persiste)
# unit_assignments: {"usb:usb-DogMatrix_MMU-2-if00": 5}
```

---

## 8. Comandos G-code

| Comando | Alias | Acción |
|---|---|---|
| `DM_UNIT` | `MMU_UNIT` | Lista las unidades y su estado (por defecto). |
| `DM_UNIT ACTION=RESCAN` | | Fuerza un escaneo inmediato (conexión/desconexión). |
| `DM_UNIT ACTION=ASSIGN DEVICE=<id> SPOOL=<n>` | | Asignación manual de número de bobina. |
| `DM_UNIT ACTION=CLEAR` | | Borrado completo de asignaciones (reasigna secuencialmente). |
| `DM_UNIT ACTION=SELECT DEVICE=<id>` | | Marca la unidad activa. |

Ejemplo de salida:

```
unit usb:usb-DogMatrix_MMU-1-if00 spool=0 if=usb gates=4 offset=0 connected=True
unit usb:usb-DogMatrix_MMU-2-if00 spool=1 if=usb gates=4 offset=4 connected=True
```

---

## 9. Casos de uso

| Caso | Comportamiento esperado |
|---|---|
| Arranque con 2 unidades conectadas | Se asignan 0 y 1 en orden de conexión; offsets 0 y 4. |
| Desconexión temporal de la unidad 0 | La unidad 0 pasa a `connected=False` y conserva `spool=0`. |
| Reconexión de la unidad 0 | Recupera `spool=0` (no se renumera). |
| Reinicio del sistema | Las asignaciones se recargan del fichero; sin cambios. |
| Nueva unidad añadida en caliente | Recibe el siguiente número secuencial (`max+1`). |
| Asignación manual del usuario | `unit_assignments` / `DM_UNIT ASSIGN` fijan el número y se persiste. |
| Borrado de configuración | `DM_UNIT CLEAR` reasigna secuencialmente por `device_id`. |
| Escaneo sin hardware accesible | Los scanners devuelven listas vacías; el sistema permanece estable. |

---

## 10. Escenarios de fallo

| Escenario | Mitigación |
|---|---|
| Directorio USB inexistente | `UsbDeviceScanner` captura `OSError` y devuelve `[]`. |
| Proveedor CAN no disponible / excepción | `CanDeviceScanner` captura y devuelve `[]`. |
| Fichero de asignaciones corrupto | `Persistence` lanza `ChecksumError`; el store arranca vacío y reasigna. |
| Listener de eventos con excepción | Se aísla; no interrumpe el escaneo ni a otros listeners. |
| Sin reactor (entornos de test/headless) | El escaneo se ejecuta bajo demanda; no falla. |

---

## 11. Pruebas

Ejecución:

```powershell
$env:PYTHONPATH=".dm_pylibs"
py -3 -m pytest tests -p no:cacheprovider
```

Cobertura específica del sistema multi-unidad:

- `tests/unit/test_units.py`
  - Scanners USB/CAN/composite (patrón, directorio ausente, deshabilitado, dedup).
  - Asignación secuencial en orden de conexión.
  - Desconexión conserva número y reconexión lo restaura.
  - Nuevo dispositivo recibe `max+1`.
  - Offsets de gates por número de bobina.
  - Persistencia entre "reinicios" (nuevo controller, mismo almacén).
  - Prioridad de la asignación manual y su persistencia.
  - `CLEAR` reasigna secuencialmente.
  - Emisión de eventos connect/assign/disconnect.
  - Arbitraje exclusivo del toolhead.
- `tests/integration/test_multi_unit.py`
  - Multi-unidad deshabilitado por defecto (`DM_UNIT` falla con error claro).
  - Listado, numeración secuencial y offsets vía `DM_UNIT`.
  - `ASSIGN`/`CLEAR` por comando.
  - Override manual desde `unit_assignments`.
  - Persistencia entre reinicios del `DogMatrixCore`.
  - Desconexión no renumera.

---

## 12. Trazabilidad

- Requisito de origen: informe de brechas, brecha **P2 "Arquitectura multi-unidad"**
  (`docs/funcionalidades_faltantes.md`).
- Ficheros:
  - Implementación: `klippy/extras/dog_matrix/units.py`
  - Integración: `klippy/extras/dog_matrix/core.py` (`enable_multi_unit`, `DM_UNIT`, `get_status["units"]`)
  - Configuración: `config/base/dog_matrix.cfg`
  - Pruebas: `tests/unit/test_units.py`, `tests/integration/test_multi_unit.py`

### 12.1 Alcance y límites

- La enumeración CANbus se alimenta de `can_uuids` (o de un proveedor inyectable);
  el descubrimiento por introspección directa del bus CAN queda como integración
  específica de plataforma.
- El control de movimiento de cada unidad sigue delegándose en los subsistemas
  existentes (`motion`, `selector`, `sensors`); `DogMatrixUnit` es el modelo de
  estado/identidad y `DogMatrixController` el orquestador.
