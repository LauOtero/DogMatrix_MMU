# Dog Matrix MMU — Formato y gestión de la configuración

**Documento:** DOC-CONFFMT · **Versión:** 0.1.0 · **Audiencia:** usuarios / desarrolladores

Responde a los requisitos de simplificación, asistente, elección de formato,
soporte multi-configuración, mantenibilidad y validación.

---

## 1. Elección de formato: YAML vs JSON

| Criterio | YAML | JSON |
|---|---|---|
| Legibilidad humana | **Alta** (sangría, sin llaves) | Media (verboso) |
| Comentarios | **Sí (`#`)** | No (JSON estándar) |
| Mantenimiento manual | **Fácil** | Propenso a errores de sintaxis |
| Herramientas | Amplio (editores, linters) | **Universal**, nativo en JS/Python |
| Esquema/validación | JSON Schema (mismo) | JSON Schema |
| Tipos estrictos | Conversión implícita (cuidado) | **Explícito** |
| Adecuado para usuarios no técnicos | **Sí** | No |

**Decisión: YAML como formato primario** (ya usado por perfiles y placas), con
**JSON como alternativa soportada** (el cargador acepta ambos y hay
`--from-json` en el asistente). Motivos: legibilidad y **comentarios** (clave
para que un usuario entienda cada pin), edición manual segura y coherencia con
`profiles/schema.json` y `config/boards/schema.json`.

El runtime de Klipper no incluye PyYAML, por lo que el proyecto usa un **parser
YAML mínimo** (`dog_matrix.capabilities._mini_yaml_load`) y **prefiere PyYAML**
si está disponible (instalador). El YAML generado se limita al subconjunto
soportado (mapas, listas, escalares) y entrecomilla valores especiales
(`!pin`, `^pin`, vacíos).

---

## 2. Organización multi-configuración

| Directorio | Contenido | Ejemplo |
|---|---|---|
| `config/boards/*.yaml` | **Definiciones de placa** (pines, drivers, capacidades) | `mellow_fly_mmu.yaml`, `btt_mmb_can_v2_0.yaml` |
| `config/boards/schema.json` | Esquema de placa | — |
| `klippy/extras/dog_matrix/vendors.py` | **Presets de unidad MMU** (fabricante) | Box Turtle, ERCF… |
| `profiles/*.yaml` | **Perfiles de instalación** (topología, límites) | `box_turtle.yaml` |
| `dog_matrix_generated.cfg` | Salida generada (no editar) | — |

Las configuraciones conviven **sin conflictos**:

- Un **board** se identifica por `board_id` (slug único); `list-boards` los
  enumera y `add-board` **rechaza sobrescribir** uno existente.
- Una **unidad/vendor** se identifica por `vendor_id`; `list-vendors` los enumera.
- La detección de **pines duplicados** (`validate_board_definition`,
  `validate_pin_plan`) evita colisiones dentro de una placa y entre alias.
- Los **alias uniformes** `MMU_*`/`DM_*` hacen que cualquier placa sea
  intercambiable sin cambiar la lógica del sistema.

---

## 3. Asistente para usuarios sin experiencia

```bash
dog-matrix list-boards                 # ver placas disponibles
dog-matrix list-vendors                # ver presets de MMU
dog-matrix add-board                   # asistente interactivo (preguntas guiadas)
dog-matrix add-board --from-json b.json   # modo desatendido/CI
dog-matrix menuconfig                  # configurar una instalacion completa
dog-matrix wizard --vendor box_turtle --units 2
```

El **asistente de placa** ([board_builder.py](file:///d:/Mi_Mundo/PROYECTOS/KLIPPER/PROYECTOS/DogMatrix_MMU/installer/board_builder.py)):

1. Pregunta identificador, nombre, topología, MCU, pines y drivers.
2. Construye el YAML **determinista** (mismo resultado siempre).
3. **Valida** (JSON Schema + pines duplicados, respetando `shared_pins`).
4. Escribe en `config/boards/<id>.yaml` sin sobrescribir.

Flujo guiado completo documentado en [WIZARD.md](WIZARD.md).

---

## 4. Mantenibilidad

**Estructura modular:**

- `klippy/extras/dog_matrix/` — runtime autocontenido (sin dependencias de
  Klipper): `boards.py`, `vendors.py`, `autoload.py`, `capabilities.py`…
- `installer/` — herramientas: `configurator.py`, `board_builder.py`,
  `generator.py`, `validator.py`, `wizard.py`, `cli.py`.
- `config/` — datos de hardware (YAML) + esquemas.
- `docs/` — especificaciones.

**Convenciones de codificación:**

- Python 3.8+, `from __future__ import annotations`, tipos en las firmas.
- Dataclasses para modelos; funciones puras para reglas (fáciles de testear).
- Nombres de placa en `snake_case`; alias de pin en `MAYÚSCULAS` con prefijo
  `MMU_`/`DM_`.
- YAML con comentarios explicativos por campo y `source_url` de procedencia.
- Todo módulo expone `__all__` y tiene pruebas asociadas.
- Escritura de ficheros **atómica** y **determinista** (hash reproducible).

---

## 5. Validación de la implementación

| Nivel | Mecanismo |
|---|---|
| Esquema | `config/boards/schema.json` + `profiles/schema.json` (JSON Schema) |
| Semántica | `validate_board_document`, `validate_board_definition`, `validate_pin_plan` |
| Generación | `SystemValidator.validate_schema` / `validate_board_plan` |
| Usabilidad | `tests/unit/test_board_builder.py` simula a un usuario creando una placa paso a paso |
| Integración | `tests/integration/test_board_generation.py` genera y valida ficheros reales |

Pruebas de usabilidad (usuario sin experiencia):

- `test_interactive_collect_and_build`: entradas guiadas → placa válida y
  recargable.
- `test_cli_add_board_from_json`: `add-board` headless crea un YAML funcional.
- `test_all_shipped_boards_validate_against_schema`: todas las placas incluidas
  cumplen el esquema.

```bash
PYTHONPATH=.dm_pylibs python -m pytest tests/unit/test_board_builder.py \
    tests/unit/test_vendors.py tests/unit/test_configurator.py -q
```
