# Dog Matrix MMU — Referencia de Perfiles

**Documento:** DOC-PROFILES · **Versión:** 0.1.0 · **Audiencia:** integradores / fabricantes de hardware

Un **perfil** es un documento YAML (o JSON) versionado que declara la topología,
las capacidades y los límites físicos de un MMU. Se valida contra
[`profiles/schema.json`](../profiles/schema.json).

---

## 1. Estructura del perfil

```yaml
schema_version: 1
profile_id: dog_matrix.example.v1
display_name: Example MMU
topology:
  type: gear_per_gate        # gear_per_gate | selector | modular | hybrid | virtual
  gates: 8
  units: 1
  selector_type: virtual     # linear | rotary | virtual (si type: selector)
  gate_pitch_mm: 0
capabilities:
  selector: false
  encoder: true
  gate_sensors: true
  toolhead_sensor: true
  sync_feedback: false
  endless_spool: true
  spoolman: true
  nfc: false
  led: true
  cutter: false
  servo: false
  espooler: false
limits:
  max_load_speed_mm_s: 80
  max_unload_speed_mm_s: 100
  max_distance_mm: 1500
  max_accel_mm_s2: 2000
  max_jerk_mm_s3: 10000
  sensor_timeout_ms: 500
  encoder_error_mm: 5
  encoder_resolution: 0.45
  bowden_length_mm: 600
  toolhead_distance_mm: 80
  purge_length_mm: 25
  tip_form_length_mm: 8
hardware:
  mcu:
    - name: mmu_main
      transport: usb       # usb | can | uart | virtual
      serial_by_id: "/dev/serial/by-id/usb-..."
  led:
    type: neopixel         # neopixel | sk6812 | dotstar | none
    count: 8
    pin: main:PA0
  nfc:
    type: pn532            # pn532 | pn5180 | pn7160 | rc522 | none
    bus: i2c               # i2c | spi | uart | none
    address: "0x24"
```

---

## 2. Campos

### 2.1 Identidad

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `schema_version` | int | Sí | Debe ser `1` |
| `profile_id` | str | Sí | Patrón `dog_matrix.<nombre>.vN` |
| `display_name` | str | No | Nombre legible |
| `generated_by` / `generated_at` | str | No | Metadatos del instalador |
| `configuration_hash` | str | No | Hash de la configuración generada |

### 2.2 `topology`

| Campo | Valores | Descripción |
|---|---|---|
| `type` | `gear_per_gate`, `selector`, `modular`, `hybrid`, `virtual` | Familia de topología |
| `gates` | 1–64 | Número de entradas de filamento |
| `units` | 1–16 | Unidades/unidades conectables |
| `selector_type` | `linear`, `rotary`, `virtual` | Obligatorio si `type: selector` |
| `gate_pitch_mm` | number | Paso entre gates (selector lineal) |

**Regla semántica:** si `type: selector`, entonces `capabilities.selector` debe
ser `true` y `selector_type` debe ser válido.

### 2.3 `capabilities`

| Capacidad | Efecto en el código |
|---|---|
| `selector` | Activa la estrategia de selector (lineal/rotativa) |
| `encoder` | Habilita medición de movimiento |
| `gate_sensors` | Crea canales de sensor por gate |
| `toolhead_sensor` | Habilita la fase `VERIFY` del toolchange |
| `sync_feedback` | Reservado (FlowGuard avanzado, fase 2) |
| `endless_spool` | Habilita `DM_ENDLESS_SPOOL` |
| `spoolman` | Permite el adaptador Spoolman |
| `nfc` | Habilita el lector NFC/RFID |
| `led` | Habilita el sistema LED |
| `cutter` | Reservado (corte de filamento, fase 2) |
| `servo` | Reservado (servo selector, fase 2) |
| `espooler` | Reservado (rebobinador motorizado, fase 2) |

### 2.4 `limits`

| Campo | Uso |
|---|---|
| `max_load_speed_mm_s` | Velocidad máxima de carga |
| `max_unload_speed_mm_s` | Velocidad máxima de descarga |
| `max_distance_mm` | Distancia máxima de movimiento |
| `max_accel_mm_s2` | Aceleración máxima (planificación) |
| `max_jerk_mm_s3` | Límite de jerk (perfil S-curve) |
| `sensor_timeout_ms` | Timeout base de sensores |
| `encoder_error_mm` | Umbral de divergencia de FlowGuard |
| `encoder_resolution` | mm por pulso del encoder |
| `bowden_length_mm` | Longitud de tubo bowden |
| `toolhead_distance_mm` | Distancia interna del cabezal |
| `purge_length_mm` | Longitud de purga |
| `tip_form_length_mm` | Longitud de tip forming |

### 2.5 `hardware`

| Campo | Descripción |
|---|---|
| `mcu[]` | Lista de MCU (`name`, `transport`, `serial_by_id`/`canbus_uuid`) |
| `led` | `type`, `count`, `pin` |
| `nfc` | `type`, `bus`, `address` |

---

## 3. Perfiles incluidos

| Perfil | `profile_id` | Topología | Gates | Notas |
|---|---|---|---|---|
| Box Turtle | `dog_matrix.box_turtle.v1` | gear_per_gate | 8 | LED, encoder, EndlessSpool |
| ERCF 2.0 | `dog_matrix.ercf.v1` | selector (linear) | 6 | Cutter + servo |
| Tradrack | `dog_matrix.tradrack.v1` | selector (rotary) | 8 | Servo |
| Night Owl | `dog_matrix.night_owl.v1` | gear_per_gate | 8 | LED |
| EMU | `dog_matrix.emu.v1` | modular | 8 (2×4) | NFC, eSpooler |
| QuattroBox | `dog_matrix.quattrobox.v1` | gear_per_gate | 4 | — |
| Custom | `dog_matrix.custom.v1` | plantilla | 4 | `custom.example.yaml` |

---

## 4. Crear un perfil propio

```bash
cp profiles/custom.example.yaml profiles/mi_mmu.yaml
# editar mi_mmu.yaml: profile_id, topology, capabilities, limits, hardware
```

Validar:

```bash
python -c "from dog_matrix.capabilities import Capabilities; \
print(Capabilities('profiles/mi_mmu.yaml').validate() or 'OK')"
```

Uso en Klipper:

```ini
[dog_matrix]
profile: mi_mmu
```

O con ruta explícita:

```ini
[dog_matrix]
profile_path: ~/printer_data/config/mi_mmu.json
```

---

## 5. Reglas de validación

La validación combina **JSON Schema** y reglas semánticas:

1. `schema_version` == 1.
2. `topology.gates` entero en 1–64.
3. `topology.type` en el conjunto permitido.
4. `type: selector` ⇒ `capabilities.selector: true` y `selector_type` válido.
5. `max_load_speed_mm_s`, `max_unload_speed_mm_s` y `max_distance_mm` > 0.
6. Todas las claves respetan `additionalProperties: false` del esquema.

Si PyYAML / jsonschema no están disponibles, se aplican las reglas semánticas y
se emite advertencia.

---

## 6. Perfil vs configuración generada

- El **perfil** es la fuente de verdad de hardware y límites.
- La **configuración generada** (`dog_matrix_generated.cfg`, `dog_matrix_profile.json`)
  se deriva del perfil y no debe editarse a mano.
- El **runtime** consume `dog_matrix_profile.json` (JSON, sin dependencia de
  PyYAML).

---

## 7. Referencias

- Esquema: [`profiles/schema.json`](../profiles/schema.json)
- Configuración: [CONFIGURATION.md](CONFIGURATION.md)
- API: [API.md](API.md)
