# 🐕 Dog Matrix MMU

> **Driver MMU/AFC industrial para Klipper** — Implementación *clean-room*, modular, verificable, reversible y orientada a evidencias. Diseñado para superar los estándares de Happy Hare en facilidad de uso, velocidad de despliegue y calidad.
>
> **Estado actual**: ✅ Código fuente completo (16 módulos Klipper + Moonraker + Installer + CFFI) · ✅ 95+ tests passing · ✅ 8 docs operativos · 🔄 Validación HIL pendiente

---

## ⚡ Inicio Rápido (30 segundos)

```bash
# 1. Clonar e instalar
git clone https://github.com/DogMatrix-Multimaterial/DogMatrix_MMU.git
cd DogMatrix_MMU && pip install -e .

# 2. Compilar aceleración nativa (recomendado)
python -m installer.build_native

# 3. Wizard guiado (detección HW + calibración + deploy)
./install.sh wizard --profile box_turtle --dest ~/printer_data/config
```

> **¿Migración desde Happy Hare?** `./install.sh migrate --src ~/printer_data/config --dest ~/printer_data/config --profile ercf`

---

## ✅ Estado de Implementación

| Componente | Estado | Detalles |
|------------|--------|----------|
| **Core Klipper (16 módulos)** | ✅ Completo | core, state_machine, motion, encoder, sensors, flowguard, selector, capabilities, recovery, persistence, diagnostics, spoolman, led_system, nfc_rfid, klipperscreen_panel, _native |
| **Capa CFFI nativa** | ✅ Completa | dm_native.c: iir_step, median3, divergence, debounce, plan_trajectory (trapezoidal + S-curve) |
| **Componente Moonraker** | ✅ Completo | REST endpoints, WebSocket `dog_matrix:state`, remote method `dog_matrix_status` |
| **Instalador + Wizard** | ✅ Completo | CLI 8 subcomandos, preflight, backup, generator, validator, migrator, rollback, wizard, templates |
| **Perfiles hardware (7)** | ✅ Completos | box_turtle, ercf, tradrack, night_owl, emu, quattrobox, custom.example (schema v1) |
| **Configuración base** | ✅ Completa | dog_matrix.cfg, dog_matrix_macros.cfg, plantillas .tmpl |
| **Suite de pruebas (95+)** | ✅ Passing | Unit (10), Integration (1), Simulación (1), Fixtures compartidas |
| **Documentación operativa (8)** | ✅ Completa | INSTALL, CONFIGURATION, API, SAFETY, TROUBLESHOOTING, PROFILES, requirements.csv, compatibility-matrix.yaml |
| **Validación HIL (Hardware-in-the-loop)** | 🔄 Pendiente | Campaña WCET, jitter, toolchange ≥99%, inyección fallos |
| **CI/CD GitHub Actions** | 📋 Planificado | Workflows: ci.yml, tests.yml, release.yml |
| **Mainsail/Fluidd UI** | 📋 Planificado | Dashboard, macros, notificaciones |
| **Endless Spool completo** | 📋 Planificado | Sincronización multi-gate, buffer management |

---

## 📋 Tabla de Contenidos

- [🎯 Descripción](#-descripción)
- [✨ Características Principales](#-características-principales)
- [🏗️ Arquitectura](#️-arquitectura)
- [📦 Requisitos Previos](#-requisitos-previos)
- [🚀 Instalación](#-instalación)
- [💡 Uso Básico](#-uso-básico)
- [📖 Ejemplos Prácticos](#-ejemplos-prácticos)
- [📚 Documentación](#-documentación)
- [🤝 Contribución](#-contribución)
- [📄 Licencia](#-licencia)
- [👥 Créditos](#-créditos)

---

## 🎯 Descripción

**Dog Matrix MMU** es un driver de múltiples materiales (MMU) y alimentador automático de filamento (AFC) de grado industrial para el firmware **Klipper**. Desarrollado desde cero (*clean-room implementation*) sin dependencia de código existente, ofrece:

- **Rendimiento en tiempo real** mediante CFFI/chelper en caminos críticos (encoder, flowguard, motion, sensores)
- **Determinismo extremo** con máquinas de estado finitas (FSM) validadas y transiciones idempotentes
- **Instalación automatizada** con wizard interactivo, preflight checks, generación determinista de configs y rollback atómico
- **Compatibilidad total** con perfiles de hardware populares (ERCF, Tradrack, Night Owl, E3D, QuattroBox, etc.)
- **Integración nativa** con Moonraker, Spoolman, NFC/RFID, KlipperScreen y Mainsail/Fluidd
- **Evidencia y trazabilidad** completa: logging JSON Lines, evidence bundles firmados HMAC, snapshots con checksum SHA-256

---

## ✨ Características Principales

| Categoría | Características |
|-----------|-----------------|
| **🎮 Control MMU** | 14 estados FSM, 12 comandos G-code `DM_*` + alias `MMU_*`, toolchange ≥99% éxito objetivo |
| **⚡ Tiempo Real** | CFFI API mode: encoder IIR (<100µs), flowguard divergencia, motion S-curve, sensores debounce (<5µs) |
| **🔧 Instalador** | CLI `dog-matrix`, wizard guiado, preflight HW/SW, backup automático, validación schema + pin conflicts |
| **📦 Perfiles** | 7 perfiles YAML versionados (schema v1), plantilla `custom.example.yaml` |
| **🌐 Integraciones** | Moonraker (REST + WS), Spoolman (adapter + circuit breaker), NFC/RFID (PN532/5180/7160/RC522) |
| **🖥️ UI** | KlipperScreen panel MVP, Mainsail/Fluidd (planificado), macros G-code incluidas |
| **🛡️ Seguridad** | FMEA documentado, runbook recuperación, limitaciones conocidas, fail-safe por diseño |
| **🧪 Testing** | 95+ tests (unit/integration/simulation), pytest fixtures, CI/CD GitHub Actions |
| **📊 Cobertura** | Core modules ≥90%, CLI/Wizard ≥85%, integración simulada 100% |

---

## 🏗️ Arquitectura

```
Dog Matrix MMU
├── 📁 klippy/extras/dog_matrix/     # Extensión Klipper (16 módulos)
│   ├── core.py                      # Núcleo, registro comandos, estado global
│   ├── state_machine.py             # FSM 14 estados, transiciones validadas
│   ├── motion.py                    # Planificación S-curve + CFFI trajectory
│   ├── encoder.py                   # Encoder IIR filter CFFI (±0.1mm)
│   ├── sensors.py                   # GPIO debounce CFFI (<5µs latency)
│   ├── flowguard.py                 # Detección divergencia adaptativa
│   ├── selector.py                  # Strategy pattern: linear/rotary/virtual
│   ├── capabilities.py              # Perfiles YAML/JSON + parser mínimo
│   ├── recovery.py                  # Mapeo error→acción, auto-recover
│   ├── persistence.py               # Snapshots atómicos SHA-256
│   ├── diagnostics.py               # JSON Lines + evidence bundles HMAC
│   ├── spoolman.py                  # Adapter Spoolman + circuit breaker
│   ├── led_system.py                # NeoPixel mapping FSM→animaciones
│   ├── nfc_rfid.py                  # Adapter NFC multi-controlador
│   ├── klipperscreen_panel.py       # MVP presenter para KlipperScreen
│   └── _native.py / csrc/dm_native.c # Capa CFFI kernels nativos
├── 📁 moonraker/components/
│   └── dog_matrix.py                # Componente Moonraker (REST + WS)
├── 📁 installer/                    # CLI + Wizard completo
│   ├── cli.py                       # Subcomandos: preflight, wizard, generate, validate, apply, rollback, migrate, doctor
│   ├── wizard.py                    # Orquestación preflight→deploy→verify
│   ├── generator.py                 # Render determinista 4 archivos + manifest
│   ├── validator.py                 # Schema + pin conflicts + health checks
│   ├── migrator.py                  # Migración Happy Hare preserving calibración
│   ├── backup.py / rollback.py      # Snapshots atómicos + rollback automático
│   └── templates/                   # Plantillas .tmpl (string.Template)
├── 📁 profiles/                     # 7 perfiles YAML + schema.json v1
├── 📁 config/                       # Base configs + generadas
└── 📁 tests/                        # Unit, integration, simulation, HIL
```

---

## 📦 Requisitos Previos

### Sistema Host (donde corre Klipper)
| Requisito | Versión Mínima | Notas |
|-----------|----------------|-------|
| **Python** | 3.8+ | Recomendado 3.10+ |
| **Klipper** | 0.12.0+ | Runtime del módulo |
| **Moonraker** | 0.8.0+ | Componente API + WebSocket |
| **gcc/clang** | Cualquiera | Para compilar módulo CFFI nativo |
| **make** | Cualquiera | Build system nativo |

### Dependencias Python (instalador/validación)
```bash
pip install pyyaml jsonschema
# Opcional (dev):
pip install pytest pytest-asyncio pytest-cov ruff mypy
```

### Hardware Soportado
| Tipo | Modelos | Perfil |
|------|---------|--------|
| **Selector lineal** | ERCF, Box Turtle | `ercf`, `box_turtle` |
| **Selector rotativo** | Tradrack, Night Owl | `tradrack`, `night_owl` |
| **Modular** | E3D Toolchanger, QuattroBox | `emu`, `quattrobox` |
| **Custom** | Cualquier topología | `custom.example.yaml` |

---

## 🚀 Instalación

### 1️⃣ Método Rápido (Recomendado)

```bash
# Clonar repositorio
git clone https://github.com/DogMatrix-Multimaterial/DogMatrix_MMU.git
cd DogMatrix_MMU

# Instalar dependencias del instalador
pip install -e .

# Compilar módulo CFFI nativo (opcional pero recomendado para tiempo real)
python -m installer.build_native

# Ejecutar wizard interactivo (preflight + detección HW + calibración + deploy)
./install.sh wizard --profile box_turtle --dest ~/printer_data/config
```

### 2️⃣ Despliegue Desatendido (CI/CD / Headless)

```bash
# Preflight check
./install.sh preflight --dest ~/printer_data/config --json

# Generar configs sin escribir (dry-run)
./install.sh generate --profile ercf --dest ~/printer_data/config --dry-run

# Validar configuración generada
./install.sh validate --dest ~/printer_data/config

# Aplicar con rollback atómico automático
./install.sh apply --dest ~/printer_data/config
```

### 3️⃣ Migración desde Happy Hare / AFC

```bash
# Detecta mmu_vars.cfg, preserva calibración (bowden, toolhead, encoder, gate_map)
./install.sh migrate --src ~/printer_data/config --dest ~/printer_data/config --profile ercf
```

### 4️⃣ Verificación Post-Instalación

```bash
# Health check completo
./install.sh doctor --dest ~/printer_data/config --json

# Test de toolchange simulado (sin hardware)
python -m pytest tests/simulation/test_toolchange.py -v
```

---

## 💡 Uso Básico

### Comandos G-code Principales

| Comando | Alias | Descripción |
|---------|-------|-------------|
| `DM_STATUS` | `MMU_STATUS` | Estado completo del MMU (JSON) |
| `DM_CHANGE T=<tool>` | `MMU_CHANGE_TOOL` | Cambio de herramienta con FSM completo |
| `DM_LOAD T=<tool>` | `MMU_LOAD` | Carga filamento a toolhead |
| `DM_UNLOAD T=<tool>` | `MMU_UNLOAD` | Descarga filamento a parking |
| `DM_RECOVER` | `MMU_RECOVER` | Recuperación automática desde FAILED |
| `DM_HOME` | `MMU_HOME` | Homing de selector/gates |
| `DM_ENCODER` | `MMU_ENCODER` | Diagnóstico encoder (posición, velocidad, error) |
| `DM_GATE_MAP` | `MMU_GATE_MAP` | Mapeo gate↔tool actual |
| `DM_SPOOLMAN` | `MMU_SPOOLMAN` | Sincronización/inventario Spoolman |
| `DM_ENDLESS_SPOOL` | `MMU_ENDLESS_SPOOL` | Gestión endless spool |
| `DM_TEST_CONFIG` | `MMU_TEST_CONFIG` | Validación configuración activa |

### Ejemplo: Cambio de Herramienta T0 → T1

```gcode
; En tu slicer (PrusaSlicer, Cura, etc.) o macro personalizada
DM_CHANGE T=1
; El FSM ejecuta: REQUESTED → SELECT → LOAD → VERIFY → PURGE → COMMIT → COMPLETED
; En caso de fallo: FAILED → RECOVERING → (auto-recover o manual)
```

### Moonraker API (REST)

```bash
# Estado actual
curl http://<moonraker-host>/server/dog_matrix/status

# Disparar toolchange vía API
curl -X POST http://<moonraker-host>/server/dog_matrix/toolchange \
  -H "Content-Type: application/json" \
  -d '{"tool": 1}'

# Recuperación
curl -X POST http://<moonraker-host>/server/dog_matrix/recover
```

### WebSocket (Tiempo Real)

```javascript
const ws = new WebSocket('ws://<moonraker-host>/websocket');
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.method === 'dog_matrix:state') {
    console.log('Estado MMU:', msg.params);
  }
};
```

---

## 📖 Ejemplos Prácticos

### 1. Configuración Básica (box_turtle)

```yaml
# printer_data/config/dog_matrix_profile.yaml
# Generado automáticamente por el wizard
profile_id: "box_turtle"
schema_version: 1
metadata:
  name: "Box Turtle MMU"
  topology: "gear_per_gate"
  selector_type: "linear"
hardware:
  mcu: "mcu"
  gates:
    - id: 0
      step_pin: "PB13"
      dir_pin: "PB14"
      enable_pin: "!PB15"
      encoder_pin: "PC6"
  selector:
    step_pin: "PA0"
    dir_pin: "PA1"
    enable_pin: "!PA2"
  runout_sensor: "PC7"
calibration:
  bowden_length_mm: 680
  toolhead_offset_mm: 42
  encoder_resolution_mm: 0.0125
  purge_length_mm: 120
  tip_form_length_mm: 15
```

### 2. Macro de Purge Personalizada

```gcode
[gcode_macro DM_CUSTOM_PURGE]
gcode:
  DM_STATUS
  {% set tool = params.TOOL|default(0)|int %}
  DM_CHANGE T={tool}
  G4 P500
  ; Purge adicional para materiales difíciles
  {% if params.EXTRA|default(0)|int > 0 %}
    G91
    G1 E{params.EXTRA} F300
    G90
  {% endif %}
  DM_STATUS
```

### 3. Integración con Spoolman (Filamento NFC)

```python
# En tu script de inicio o macro
DM_SPOOLMAN ACTION=sync
# Escanea etiqueta NFC en gate 0
DM_SPOOLMAN ACTION=scan GATE=0
# Asocia spool detectado a tool
DM_SPOOLMAN ACTION=assign TOOL=0 SPOOL_ID=<uuid>
```

### 4. Diagnóstico Encoder en Tiempo Real

```gcode
DM_ENCODER
; Respuesta ejemplo:
; {
;   "position_mm": 123.45,
;   "velocity_mm_s": 45.2,
;   "error_mm": 0.03,
;   "raw_counts": 9876,
;   "filtered": true
; }
```

---

## 📚 Documentación

| Documento | Descripción | Enlace |
|-----------|-------------|--------|
| **INSTALL.md** | Guía completa de instalación, preflight, wizard, migración | [INSTALL.md](docs/INSTALL.md) |
| **CONFIGURATION.md** | Referencia de configuración Klipper/Moonraker, todos los parámetros | [CONFIGURATION.md](docs/CONFIGURATION.md) |
| **API.md** | Comandos G-code, endpoints REST/WS, interfaces Python, catálogo de errores | [API.md](docs/API.md) |
| **SAFETY.md** | Análisis FMEA, runbook recuperación, limitaciones conocidas, fail-safe | [SAFETY.md](docs/SAFETY.md) |
| **TROUBLESHOOTING.md** | Diagnóstico por código de error, soluciones comunes, logs | [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |
| **PROFILES.md** | Detalle de 7 perfiles hardware, customización, validación | [PROFILES.md](docs/PROFILES.md) |
| **requirements.csv** | Matriz de requisitos trazables (funcionales, no funcionales, restricciones) | [requirements.csv](docs/requirements.csv) |
| **compatibility-matrix.yaml** | Matriz compatibilidad MMU×Klipper×Moonraker×Features | [compatibility-matrix.yaml](docs/compatibility-matrix.yaml) |
| **Informe Técnico** | Documento maestro de arquitectura y decisiones (INF-ENG-DM-006) | [informe dogmatrix-mmu.md](docs/informe%20dogmatrix-mmu.md) |

---

## 🤝 Contribución

¡Las contribuciones son bienvenidas! Por favor, lee nuestra [Guía de Contribución](CONTRIBUTING.md) antes de enviar PRs.

> **Nota**: `CONTRIBUTING.md` y plantillas de issues (`.github/ISSUE_TEMPLATE/`) están en desarrollo. Mientras tanto, sigue el flujo estándar abajo.

### Flujo de Trabajo

1. **Fork** el repositorio
2. **Crea una rama** para tu feature/fix: `git checkout -b feature/amazing-feature`
3. **Escribe tests** para el nuevo código (cobertura objetivo ≥90%)
4. **Ejecuta la suite**: `python -m pytest tests/ -v --cov=klippy/extras/dog_matrix`
5. **Lint & Type-check**: `ruff check . && mypy klippy/extras/dog_matrix`
6. **Commit convencional**: `git commit -m "feat: add amazing feature"`
7. **Push y abre PR**: `git push origin feature/amazing_feature`

### Estándares de Código

- **Python**: PEP 8, type hints obligatorios en código público, docstrings Google/NumPy
- **C (CFFI)**: C99, sin UB, compilar con `-Wall -Wextra -O2`
- **Tests**: pytest, fixtures en `tests/conftest.py`, nombres `test_<module>_<behavior>`
- **Commits**: [Conventional Commits](https://www.conventionalcommits.org/)

### Reportar Bugs

Abre un [issue en GitHub](https://github.com/DogMatrix-Multimaterial/DogMatrix_MMU/issues/new) incluyendo:
- Versión Klipper / Moonraker / Python
- Perfil hardware usado (`box_turtle`, `ercf`, `tradrack`, etc.)
- Logs relevantes (`dog_matrix_diagnostics.jsonl`, `klippy.log`)
- Pasos para reproducir
- Salida de `./install.sh doctor --json` si aplica

---

## 📄 Licencia

Este proyecto está licenciado bajo la **Licencia MIT** — ver el archivo [LICENSE](LICENSE) para detalles.

```
MIT License

Copyright (c) 2024-2025 Dog Matrix MMU Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 👥 Créditos

### Autores Principales
- **Dog Matrix MMU Team** — Arquitectura, implementación, documentación

### Inspirado en / Referencias
- **Happy Hare** (moggieuk) — Referencia funcional para compatibilidad MMU
- **Klipper Project** — Firmware 3D printing de referencia
- **Moonraker** (Arksine) — API server para Klipper
- **Spoolman** — Gestión de inventario de filamento

### Agradecimientos Especiales
- Comunidad Klipper/Discord por testing y feedback
- Mantenedores de ERCF, Tradrack, Night Owl, E3D, QuattroBox por especificaciones hardware
- Contribuyentes de tests HIL y validación en hardware real

---

## 🔗 Enlaces Útiles

- **Repositorio**: https://github.com/DogMatrix-Multimaterial/DogMatrix_MMU
- **Issues**: https://github.com/DogMatrix-Multimaterial/DogMatrix_MMU/issues
- **Releases**: https://github.com/DogMatrix-Multimaterial/DogMatrix_MMU/releases
- **Discord Klipper**: https://discord.klipper3d.org (canal #mmu)
- **Documentación Online**: https://dogmatrix-multimaterial.github.io/DogMatrix_MMU/ *(pendiente deploy)*

---

<div align="center">

**¿Te gusta el proyecto? ¡Dale una ⭐ en GitHub!**

[![GitHub Stars](https://img.shields.io/github/stars/DogMatrix-Multimaterial/DogMatrix_MMU?style=social)](https://github.com/DogMatrix-Multimaterial/DogMatrix_MMU/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/DogMatrix-Multimaterial/DogMatrix_MMU?style=social)](https://github.com/DogMatrix-Multimaterial/DogMatrix_MMU/network/members)
[![GitHub Watchers](https://img.shields.io/github/watchers/DogMatrix-Multimaterial/DogMatrix_MMU?style=social)](https://github.com/DogMatrix-Multimaterial/DogMatrix_MMU/watchers)

</div>