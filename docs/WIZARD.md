# Dog Matrix MMU — Asistente de configuración (estilo menuconfig)

**Documento:** DOC-WIZARD · **Versión:** 0.1.0 · **Audiencia:** instaladores / integradores
**Estado:** implementado y verificado

Dog Matrix incorpora un asistente de configuración inspirado en el **Kconfig /
menuconfig** de Happy Hare v4, pero sin su DSL propietario ni sus >130 ficheros
`Kconfig*`: es un **modelo declarativo Python**, determinista, validable y
testeable, con **derivación de capacidades** y **soporte multi-unidad**.

---

## 1. Flujo del asistente

```
dog-matrix menuconfig            (o:  dog-matrix wizard --vendor <id> --units N)
        │
        ├─ 1. MMU Type     → fabricante/modelo (catálogo de vendors)
        ├─ 2. Machine      → nº de unidades encadenadas + conexión (usb/can)
        ├─ 3. Board        → placa controladora (o la del fabricante por defecto)
        ├─ 4. Sensors      → encoder, gate sensors, dual pre/post-gate, sync-feedback
        ├─ 5. Extras       → LEDs, eSpooler, NFC, I2C, cortador, Spoolman, EndlessSpool
        └─ 6. Calibration  → bowden, velocidades
                │
                ▼
        ResolvedConfiguration (vendor, placa, unidades, gates, layout de máquina)
                │
                ▼
        perfil MMUProfile + [dm_machine] + [board_pins dogmatrixN] + [dm_pins]
```

La configuración es **determinista** (misma entrada → mismo hash) y se valida
contra `profiles/schema.json`.

---

## 2. Comandos

| Comando | Descripción |
|---|---|
| `dog-matrix list-vendors` | Lista los presets de MMU/ERCF (fabricante, gates/u, topología, placa) |
| `dog-matrix menuconfig [--dest D]` | Configuración interactiva (estilo menuconfig) y despliegue |
| `dog-matrix wizard --vendor ID --units N [--board B]` | Despliegue no interactivo desde un preset |
| `dog-matrix generate --vendor ID --units N [--dry-run]` | Genera la configuración sin desplegar |
| `dog-matrix apply --vendor ID --units N` | Aplica con rollback atómico |

Ejemplos:

```bash
dog-matrix list-vendors
dog-matrix generate --vendor box_turtle --units 3 --dry-run
dog-matrix wizard  --vendor ercf_2_0 --board btt_mmb_can_v2_0
```

---

## 3. Catálogo de proveedores

Réplica de los *defaults* de `installer/mmu_types/Kconfig.*` de Happy Hare v4.

| Vendor (id) | Fabricante (HH) | Gates/u | Topología | Placa por defecto |
|---|---|---|---|---|
| `vvd` | BTT ViViD | 4 | selector (indexado) | `generic_selector` |
| `box_turtle` | Box Turtle | 4 | gear-per-gate | `dogmatrix_unit_4coil` |
| `ercf_1_1` | ERCF v1.1 | 9 | selector lineal | `ercf_easy_brd_v1_1` |
| `ercf_2_0` | ERCF v2.0 | 8 | selector lineal | `btt_mmb_can_v2_0` |
| `emu` | EMU | 5 | gear-per-gate | `generic_gear_per_gate` |
| `tradrack` | Tradrack | 10 | selector lineal | `mellow_fly_ercf_v1` |
| `night_owl` | Night Owl | 2 | gear-per-gate | `generic_gear_per_gate` |
| `angry_beaver` | Angry Beaver | 4 | gear-per-gate | `btt_mmb_can_v2_0` |
| `3ms` | 3MS | 4 | gear-per-gate | `generic_gear_per_gate` |
| `quattrobox` | QuattroBox | 4 | gear-per-gate | `generic_gear_per_gate` |
| `quattrobox_v2` | QuattroBox V2 | 4 | gear-per-gate | `generic_gear_per_gate` |
| `chameleon` | 3D Chameleon | 4 | selector rotativo | `generic_selector` |
| `pico_mmu` | PicoMMU | 4 | selector por servo | `generic_selector` |
| `kms` | KMS | 4 | gear-per-gate | `generic_gear_per_gate` |
| `mmx` | MMX | 4 | selector por servo | `generic_selector` |
| `qidi_box` | QIDI Box | 4 | gear-per-gate | `generic_gear_per_gate` |

Cada preset declara además **capacidades derivadas** (encoder, servo, LEDs,
sync-feedback, eSpooler, NFC…) y **límites** (velocidades, bowden, distancias).

### Derivación de capacidades (reglas, no datos duplicados)

- Topología `gear_per_gate` → `selector=false`, `servo=false`.
- Topología `selector` → `selector=true`, `servo=true`.
- Overrides por vendor (p. ej. ERCF añade encoder y servo; Box Turtle añade
  eSpooler y sync-feedback). Esto sustituye a los `select`/`imply` de Kconfig.

---

## 4. Arquitectura multi-unidad (encadenado)

Permite **encadenar N unidades iguales** para multiplicar los colores
(ej. 3 × Box Turtle = 12 gates).

- `units` ∈ [1..16]. `total_gates = units × gates_por_unidad`.
- Los gates se numeran **de forma contigua** entre unidades: unidad 0 → 0-3,
  unidad 1 → 4-7, unidad 2 → 8-11.
- Se genera una sección `[dm_machine]` con el layout y **un bloque
  `[board_pins dogmatrixN]` por unidad** (cada unidad es un MCU independiente,
  con nombre `mmu0`, `mmu1`, …).

```ini
[dm_machine]
vendor: box_turtle
units: 2
gates_per_unit: 4
total_gates: 8
# unit0: gates 0-3
# unit1: gates 4-7
```

La lógica de offsets ya está integrada en el runtime multi-unidad
(`DogMatrixUnit.gate_offset`, `docs/MULTI_UNIT.md`).

---

## 5. Comparativa con el Kconfig de Happy Hare

| Aspecto | Happy Hare v4 (Kconfig) | Dog Matrix (asistente declarativo) |
|---|---|---|
| Definición de menús | >130 ficheros `Kconfig*` + DSL propio | 1 módulo Python (`configurator.py`) + catálogo `vendors.py` |
| Toolchain | kconfiglib vendorizado + curses + Jinja | stdlib (headless e interactivo de texto) |
| Derivación de capacidades | `select`/`imply` en Kconfig | reglas en `VendorPreset.resolved_capabilities()` |
| Validación | avisos/errores Kconfig | JSON Schema (`profiles/schema.json`) + tests |
| Búsqueda de opciones | grepconfig (limitado) | `Configurator.search()` |
| Multi-unidad | `-n`, config por unidad | `--units N` + `[dm_machine]` + `[board_pins]` por unidad |
| Determinismo | depende de Kconfig | hash SHA-256 reproducible |
| Extender con un vendor | crear `Kconfig.<name>` | añadir un `VendorPreset` |

---

## 6. Cómo añadir un proveedor

1. Añade un `VendorPreset` en [vendors.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/klippy/extras/dog_matrix/vendors.py)
   (id, topología, gates/unidad, placa por defecto, capacidades, límites).
2. Si la placa no existe, créala en `config/boards/<id>.yaml`.
3. Añade un caso en `tests/unit/test_vendors.py` y `tests/unit/test_configurator.py`.

---

## 7. Simulador del instalador (verificación end-to-end)

`installer/simulator.py` ejecuta el **mismo flujo** que el instalador real
(`preflight` → `menuconfig`/`wizard` → generación → verificación) sobre
directorios temporales, sin tocar la configuración real. Permite **ver** los
archivos generados e **interactuar** por consola para comprobar que todo funciona.

```bash
py -3 -m installer.simulator                                   # consola interactiva
py -3 -m installer.simulator --vendor ercf_2_0 --units 2       # wizard no interactivo
py -3 -m installer.simulator --all --json                      # todos los vendors y perfiles
py -3 -m installer.simulator --command "wizard vendor=emu" --command "verify"
py -3 -m installer.simulator --script escenario.txt            # guion (un comando por línea)
```

Comandos de consola:

| Comando | Descripción |
|---|---|
| `help` | Ayuda y lista de comandos |
| `list-vendors` / `list-boards` / `list-profiles` | Catálogos disponibles |
| `preflight [dest=...]` | Diagnóstico del entorno |
| `menuconfig key=val ...` | Resuelve la configuración (equivalente a `Configurator`) |
| `wizard vendor=ID units=N [board=B] [dry_run=1]` | Despliegue completo |
| `simulate-all [dest=D]` | Genera para todos los vendors y perfiles |
| `verify [dest=...]` | Verifica la **última** implantación (o `dest` indicado) |
| `ls` / `show <fichero>` | Lista y muestra los ficheros generados |
| `dest` | Directorio base y último destino |
| `exit` | Salir |

`verify` usa por defecto el **último destino** de despliegue; el wizard ya ejecuta
la verificación internamente y revierte (rollback atómico) si falla.

Cobertura de tests: `tests/unit/test_installer_simulator.py` (consola, script,
CLI, `--json`) y `tests/integration/test_vendor_generation.py` (generación y
verificación para **los 16 vendors y 6 perfiles** soportados, multi-unidad y
determinismo).

---

## 8. Índice de ficheros

- [vendors.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/klippy/extras/dog_matrix/vendors.py) — catálogo de presets y layout multi-unidad
- [configurator.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/installer/configurator.py) — motor menuconfig + CLI headless
- [wizard.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/installer/wizard.py) — orquestación y despliegue
- [cli.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/installer/cli.py) — comandos `list-vendors`, `menuconfig`, `wizard`
- [simulator.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/installer/simulator.py) — simulador interactivo/por script del instalador
- [generator.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/installer/generator.py) — render de `[dm_machine]` y `[board_pins]` por unidad
- [PIN_CONFIGURATION.md](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/docs/PIN_CONFIGURATION.md) — alias, placas, post-gate dual e I2C
- [MULTI_UNIT.md](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/docs/MULTI_UNIT.md) — sistema multi-unidad en runtime
