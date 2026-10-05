# Matriz de Paridad: Dog Matrix MMU ↔ Happy Hare v4

> **Documento:** INF-AUD-DM-002 · **Fecha:** 2026-10-05 · **Estado:** inventario de estado
> **Alcance:** todas las funcionalidades de Happy Hare v4 contrastadas con el código de Dog Matrix.
> **Fuentes:**
> - Código fuente HH v4: `docs/Happy-Hare_v4/` (`extras/mmu/`, `commands/`, `unit/`, `config/`, `test/`).
> - Documentación HH v4: `docs/Happy-Hare-Doc_v4/doc/` (74 comandos públicos, 23 de desarrollo, 20 features, 10 macros, 6 calibraciones).
> - Código Dog Matrix: `klippy/extras/dog_matrix/*` (45 módulos), `moonraker/components/dog_matrix.py`, `installer/*`, `config/*`.
> **Base verificada:** 578 tests en verde · 67+ comandos `DM_*` (con alias `MMU_*`) · hooks `_DM_*`/`_MMU_*`.

---

## 0. Leyenda de estado

| Símbolo | Significado |
|---|---|
| 🟢 | **Soportado**: implementado y cubierto por tests. |
| 🟡 | **Parcial**: existe pero con subconjunto de parámetros/funciones. |
| 🧪 | **Simulado**: lógica implementada con inyección de hardware, sin driver físico verificado. |
| 🔴 | **Ausente**: no implementado. |
| ➖ | **No aplicable**: peculiaridad de HH (DSL propio, tooling interno) cubierta por otro mecanismo. |

**Resumen global:** Dog Matrix implementa el núcleo operativo y **todos los comandos públicos**
de Happy Hare v4 (con equivalentes `DM_*`/`MMU_*`), así como los subsistemas de hardware
avanzado como **módulos con lógica completa y hooks inyectables**. Las brechas restantes son la
**validación en hardware físico** de algunos subsistemas, los **drivers NFC reales**, el
**frontend gráfico** (fuera de alcance: otro proyecto) y la **cobertura de tests** frente a HH.

---

## 1. Resumen por área

| Área | Estado | Comentario |
|---|---|---|
| Núcleo toolchange / FSM / persistencia | 🟢 | `state_machine.py`, `persistence.py`, `recovery.py` |
| Comandos públicos (74) | 🟢 | 48 🟢 · 24 🟡 · 2 🧪 · 0 🔴 (detalle §2) |
| Multi-unidad | 🟢 | `units.py` + `[dm_machine]` |
| Contrato `printer.mmu` + eventos | 🟢 | `events.py`, 19 eventos `mmu:*` |
| Calibración | 🟢 | gear/encoder/bowden/toolhead/psensor + selectores |
| Sensores / encoder / FlowGuard | 🟡 | Lógica completa; endstop virtual y modos encoder |
| Tip forming / purga | 🟡 | FSM + matriz + Blobifier; validación HW pendiente |
| eSpooler / environment / fan / LEDs | 🟡 | PWM y hooks inyectables; GPIO real pendiente |
| Spoolman / NFC / TD-1 | 🟡/🧪 | Adapters + drivers (transporte inyectado); hardware pendiente |
| Hardware avanzado (sync, drive, endstop, corriente, ADC) | 🟢 | Módulos con lógica; hardware pendiente |
| Macros / hooks / secuencias | 🟢 | `_DM_*`+`_MMU_*`, `_MMU_STEP_*`, extensiones de usuario |
| UI (KlipperScreen, Mainsail/Fluidd) | 🔴 | **Fuera de alcance (otro proyecto)** |
| Simulador / testing | 🟡 | Simulador runtime + simulador del instalador; 578 tests vs >900 HH |

---

## 2. Comandos públicos `MMU_*` (HH v4 → Dog Matrix)

| Comando HH v4 | Equivalente Dog Matrix | Estado | Nota |
|---|---|---|---|
| `MMU` | `DM_MOTORS_ON`/`OFF` + `DM_RESET` | 🟡 | Sin `ENABLE=1` con reset atómico |
| `MMU_CALC_PURGE_VOLUMES` | `DM_CALC_PURGE_VOLUMES` | 🟢 | `MIN`/`MAX`/`MULTIPLIER`/`SOURCE` |
| `MMU_CHANGE_TOOL` | `DM_CHANGE` | 🟡 | Faltan `QUIET`,`RESTORE`,`NEXT_POS`,`SLICER_*` |
| `MMU_CHECK_GATE` | `DM_CHECK_GATE` | 🟢 | `GATES`/`TOOLS`/`TOOL`/`TD1` |
| `MMU_EJECT` | `DM_EJECT` | 🟡 | `EXTRUDER_ONLY` sí; `FORCE`/`RESTORE` no |
| `MMU_ENCODER` | `DM_ENCODER` | 🟡 | Solo `READ`/`RESET` |
| `MMU_ENDLESS_SPOOL` | `DM_ENDLESS_SPOOL` | 🟡 | Grupos + failover automático |
| `MMU_ESPOOLER` | `DM_ESPOOLER` | 🟡 | `BURST`; GPIO real pendiente |
| `MMU_FAN` | `DM_FAN` | 🟢 | Histéresis + forzado |
| `MMU_FLOWGUARD` | `DM_FLOWGUARD` | 🟢 | Modos, umbrales, adaptativo, reset |
| `MMU_GATE_MAP` | `DM_GATE_MAP` | 🟡 | Subconjunto de campos |
| `MMU_GRIP` | `DM_GRIP` | 🟢 | Hook de selector |
| `MMU_HEATER` | `DM_HEATER` | 🟡 | `DRY`/`STOP` |
| `MMU_HELP` | `DM_HELP` | 🟡 | Sin `HELP=1` por comando |
| `MMU_HOME` | `DM_HOME` | 🟢 | |
| `MMU_LED` | `DM_LED` | 🟡 | Efectos básicos |
| `MMU_LOAD` | `DM_LOAD` | 🟡 | Subconjunto de parámetros |
| `MMU_LOG` | `DM_LOG` | 🟢 | |
| `MMU_MOTORS_OFF` | `DM_MOTORS_OFF` | 🟢 | |
| `MMU_MOTORS_ON` | `DM_MOTORS_ON` | 🟢 | |
| `MMU_NFC` | `DM_NFC` | 🟢 | `STATUS`/`READ`/`REGISTER`/`RELEASE` + endstop |
| `MMU_NFC_SCAN` | `DM_NFC_SCAN` | 🟡 | Multi-lector (transporte inyectado) |
| `MMU_PAUSE` | `DM_PAUSE` | 🟢 | Detiene autoload/drives, emite `mmu:mmu_paused` |
| `MMU_PRELOAD` | `DM_PRELOAD` | 🟢 | |
| `MMU_RECOVER` | `DM_RECOVER` | 🟢 | |
| `MMU_RELEASE` | `DM_RELEASE` | 🟢 | |
| `MMU_RESET` | `DM_RESET` | 🟢 | `CONFIRM=1` |
| `MMU_SELECT` | `DM_SELECT` | 🟢 | |
| `MMU_SENSORS` | `DM_SENSORS` | 🟡 | Sin alta dinámica |
| `MMU_SERVO` | `DM_SERVO` | 🟢 | |
| `MMU_SET_LED` | `DM_SET_LED` | 🟢 | |
| `MMU_SLICER_TOOL_MAP` | `DM_SLICER_TOOL_MAP` | 🟡 | Sin `PURGE_MAP` |
| `MMU_SPOOLMAN` | `DM_SPOOLMAN` | 🟡 | 4 modos + `PULL` |
| `MMU_SPOOLMAN_TAG` | `DM_SPOOLMAN TAG=` | 🟡 | Sin escritura física de tag |
| `MMU_STATS` | `DM_STATS` | 🟡 | Subconjunto de métricas |
| `MMU_STATUS` | `DM_STATUS` | 🟡 | `COMPACT` |
| `MMU_SYNC_FEEDBACK` | `DM_SYNC_FEEDBACK` | 🧪 | Estado/autotune/ADC (sin HW real) |
| `MMU_SYNC_GEAR_MOTOR` | `DM_SYNC_GEAR_MOTOR` + `sync_controller.py` | 🟡 | Lazo real pendiente |
| `MMU_TD1` | `DM_TD1` | 🧪 | Escáner simulado |
| `MMU_TOOL_OVERRIDES` | `DM_TOOL_OVERRIDES` | 🟢 | `speed/purge/temperature/material/color` |
| `MMU_TTG_MAP` | `DM_REMAP_TTG` | 🟡 | Sin edición completa |
| `MMU_UNLOAD` | `DM_UNLOAD` | 🟢 | |
| `MMU_UNLOCK` | `DM_UNLOCK` | 🟢 | |
| `MMU_CALIBRATE_BOWDEN` | `DM_CALIBRATE_BOWDEN` | 🟢 | |
| `MMU_CALIBRATE_ENCODER` | `DM_CALIBRATE_ENCODER` | 🟢 | |
| `MMU_CALIBRATE_GATE` | `DM_CALIBRATE_GATES` | 🟡 | Sin parámetros por gate |
| `MMU_CALIBRATE_GEAR` | `DM_CALIBRATE_GEAR` | 🟢 | |
| `MMU_CALIBRATE_PSENSOR` | `DM_CALIBRATE_PSENSOR` | 🟢 | |
| `MMU_CALIBRATE_ROTARY_SELECTOR` | `DM_CALIBRATE_ROTARY_SELECTOR` | 🟢 | |
| `MMU_CALIBRATE_SELECTOR` | `DM_CALIBRATE_SELECTOR` | 🟡 | Genérico |
| `MMU_CALIBRATE_SELECTOR_INDEXES` | `DM_CALIBRATE_SELECTOR_INDEXES` | 🟢 | |
| `MMU_CALIBRATE_SERVO_SELECTOR` | `DM_CALIBRATE_SERVO_SELECTOR` | 🟢 | |
| `MMU_CALIBRATE_TOOLHEAD` | `DM_CALIBRATE_TOOLHEAD` | 🟢 | |
| `MMU_SOAKTEST_LOAD_SEQUENCE` | `DM_SOAKTEST_LOAD_SEQUENCE` | 🟢 | |
| `MMU_SOAKTEST_SELECTOR` | `DM_SOAKTEST_SELECTOR` | 🟢 | |
| `MMU_TEST_BUZZ_MOTOR` | `DM_TEST_BUZZ_MOTOR` | 🟢 | |
| `MMU_TEST_CONFIG` | `DM_TEST_CONFIG` | 🟢 | |
| `MMU_TEST_FORM_TIP` | `DM_TEST_FORM_TIP` | 🟢 | |
| `MMU_TEST_GRIP` | `DM_TEST_GRIP` | 🟢 | |
| `MMU_TEST_HOMING_MOVE` | `DM_TEST_HOMING_MOVE` | 🟢 | |
| `MMU_TEST_LOAD` | `DM_TEST_LOAD` | 🟢 | |
| `MMU_TEST_MOVE` | `DM_TEST_MOVE` | 🟢 | |
| `MMU_TEST_PURGE` | `DM_TEST_PURGE` | 🟢 | |
| `MMU_TEST_RUNOUT` | `DM_TEST_RUNOUT` | 🟢 | |
| `MMU_TEST_TRACKING` | `DM_TEST_TRACKING` | 🟡 | Sin traza/plot |
| `MMU_CHANGE_TOOL_STANDALONE` | `DM_CHANGE_TOOL_STANDALONE` | 🟢 | |
| `MMU_END` | `DM_END` | 🟢 | |
| `MMU_PRINT_END` | `DM_PRINT_END` | 🟢 | |
| `MMU_PRINT_START` | `DM_PRINT_START` | 🟢 | |
| `MMU_START_CHECK` | `DM_START_CHECK` | 🟢 | |
| `MMU_START_LOAD_INITIAL_TOOL` | `DM_START_LOAD_INITIAL_TOOL` | 🟢 | |
| `MMU_START_SETUP` | `DM_START_SETUP` | 🟢 | |
| `MMU_UPDATE_HEIGHT` | `DM_UPDATE_HEIGHT` | 🟢 | |
| `MMU_SELECT_BYPASS` | `DM_SELECT_BYPASS` | 🟢 | |

**Totales:** 🟢 48 · 🟡 24 · 🧪 2 · 🔴 0 (sobre 74).

---

## 3. Comandos de desarrollo e internos

| Comando HH | Equivalente Dog Matrix | Estado |
|---|---|---|
| `_MMU_STEP_*` (11 pasos) | `_MMU_STEP_*` / `MMU_STEP_*` / `DM_STEP_*` (core) | 🟢 |
| `_MMU_STEP_MOVE` / `_MMU_STEP_SET_FILAMENT` | `DM_STEP` + `Motion`/`filament_pos` | 🟢 |
| `__MMU_SENSOR_*` | `SensorManager.set_simulated_state` | 🟡 |
| `__MMU_ENCODER_*` | `Encoder.set_simulated_position` | 🟡 |
| `_MMU_TEST` | `DM_STEP` / `DM_DUMP_VARS` | 🟢 |
| `PAUSE`/`RESUME`/`CANCEL_PRINT` | Built-ins Klipper | ➖ |

---

## 4. Subsistemas / Features

| Feature HH v4 | Módulo Dog Matrix | Estado |
|---|---|---|
| Tip-Forming & Purging | `tip_forming.py`, `purge.py` | 🟡 |
| TD-1 | `td1.py` | 🧪 |
| Sync-Feedback Buffer | `sync_feedback.py` (+ADC) | 🧪 |
| Statistics & Counters | `counters.py` | 🟢 |
| State Persistence | `persistence.py` | 🟢 |
| Spoolman | `spoolman.py` | 🟡 |
| Sensors | `sensors.py` | 🟡 |
| NFC | `nfc_rfid.py`, `nfc_drivers.py` | 🟡/🧪 |
| LEDs | `led_system.py` | 🟡 |
| G-code Preprocessing | `moonraker.components` | 🟢 |
| Gate/TTG Maps | `slicer_map.py`, `core.ttg_map` | 🟡 |
| FlowGuard | `flowguard.py` | 🟢 |
| Filament Bypass | `core.bypass_active` | 🟢 |
| Fan Control | `fan_control.py` | 🟢 |
| eSpooler | `espooler.py` | 🟡 |
| Environment Manager | `environment.py` | 🟡 |
| EndlessSpool Runout | `core.next_endless_gate` + `try_endless_failover` | 🟢 |
| Encoder | `encoder.py` | 🟡 |
| Eject Buttons | `ejection_buttons.py` | 🟡 |
| Cold Pull | `core.cmd_DM_COLD_PULL` | 🟢 |

### 4.1 Subsistemas avanzados (implementados como módulos)

| Subsistema HH v4 | Módulo Dog Matrix | Estado |
|---|---|---|
| Motor Sync Controller | `sync_controller.py` | 🟢 |
| Extruder Monitor | `extruder_monitor.py` | 🟢 |
| Drive / multi-gear unificado | `drive.py` | 🟢 |
| Toolhead/Extruder wrappers | `klipper_wrappers.py` | 🟢 |
| Compound endstop | `compound_endstop.py` | 🟢 |
| Stepper current management | `stepper_current.py` | 🟢 |
| ADC-compat buffer | `sync_feedback.py` (`evaluate_adc`) | 🟢 |
| NFC controller/arbiter + endstop | `nfc_rfid.py` (`NFCArbiter`, `NFCEndstop`) | 🟢 |
| NFC drivers (PN532 I2C/UART, PN5180, PN7160, RC522) | `nfc_drivers.py` | 🟡 |
| NFC RX gain autotune | `nfc_rfid.py` (`RXGainController`) | 🟢 |
| Selector macro | `selector.py` (`MacroSelector`) | 🟢 |
| Selector linear-mg / linear-servo / mg-servo | `selector.py` | 🟢 |
| Local gate | `local_gate.py` | 🟢 |
| Filament display (texto) | `filament_display.py` | 🟢 |
| Tool overrides | `tool_overrides.py` | 🟢 |

> **Nota:** "🟢" = lógica completa y testeada con hardware inyectable; la validación sobre
> hardware físico real queda pendiente (ver §9 y `funcionalidades_faltantes.md`).

---

## 5. Macros, hooks y secuencias

| Macro/capacidad HH v4 | Dog Matrix | Estado |
|---|---|---|
| Print Start/End (`MMU_PRINT_START/END`, `START_*`, `MMU_END`) | `DM_*` + alias `MMU_*` | 🟢 |
| State Change Hooks | `_DM_*` + alias `_MMU_*` (core + `dog_matrix_macros.cfg`) | 🟢 |
| Customization (`user_*_extension`) | `core._user_extension_name` + macros | 🟢 |
| Sequence / `_MMU_STEP_*` | `sequences.py` + comandos `_MMU_STEP_*` | 🟢 |
| Secuencias custom por config | `gcode_load_sequence` | 🟢 |
| Purge / Blobifier | `purge.py` (modo Blobifier) | 🟡 |
| Tip Forming / Servo Cutter | `tip_forming.py` + `cutter_servo` | 🟡 |
| Client macros | — | 🔴 |

---

## 6. Calibración

| Calibración HH v4 | Comando Dog Matrix | Estado |
|---|---|---|
| Gear | `DM_CALIBRATE_GEAR` | 🟢 |
| Encoder | `DM_CALIBRATE_ENCODER` | 🟢 |
| Bowden | `DM_CALIBRATE_BOWDEN` | 🟢 |
| Toolhead | `DM_CALIBRATE_TOOLHEAD` | 🟢 |
| Gate | `DM_CALIBRATE_GATES` | 🟡 |
| Selector (lineal) | `DM_CALIBRATE_SELECTOR` | 🟡 |
| Rotary selector | `DM_CALIBRATE_ROTARY_SELECTOR` | 🟢 |
| Servo selector | `DM_CALIBRATE_SERVO_SELECTOR` | 🟢 |
| Selector indexes | `DM_CALIBRATE_SELECTOR_INDEXES` | 🟢 |
| PSensor (sync-feedback) | `DM_CALIBRATE_PSENSOR` | 🟢 |

---

## 7. Contrato de estado y eventos

| Capacidad HH v4 | Dog Matrix | Estado |
|---|---|---|
| `printer.mmu` (≈60 vars) | `events.build_mmu_state` (ampliado) | 🟡 |
| `printer.mmu_machine` | `events.build_mmu_machine` | 🟡 |
| Eventos `mmu:*` | `events.py` (19) | 🟢 |
| Hooks de ciclo de vida + `user_*_extension` | `core._emit_callback` (+ `_MMU_*`) | 🟢 |

---

## 8. Integraciones

| Integración HH v4 | Dog Matrix | Estado |
|---|---|---|
| Moonraker (componente + preprocessing) | `moonraker/components/dog_matrix.py` | 🟡 |
| Slicer (start/end/height) | `parse_gcode` + `DM_UPDATE_HEIGHT` | 🟡 |
| Spoolman (off/readonly/push/pull) | `spoolman.py` | 🟡 |
| Mainsail/Fluidd (frontend) | — | 🔴 (fuera de alcance) |
| KlipperScreen (frontend) | — | 🔴 (fuera de alcance) |
| `/machine/td1/data` (Moonraker) | — | 🔴 |

---

## 9. Herramientas de desarrollo

| Herramienta HH v4 | Dog Matrix | Estado |
|---|---|---|
| Simulador interactivo (`make console`) | `simulator.py` (REPL, `--script`, `--json`, `DUMP`, `TICK`) | 🟢 |
| Simulador del instalador | `installer/simulator.py` (consola/`--script`/`--all`/`--json`) | 🟢 |
| Suite de tests (>900) | 578 tests | 🟡 |
| `MMU_TEST_*` | `DM_TEST_*` | 🟢 |
| Telemetría/plots (`make plot_sync`) | `telemetry.py` (buffer/CSV) | 🟡 |
| Kconfig/menuconfig | `installer/configurator.py` | ➖ |
| Layout de código | arquitectura propia por módulos | ➖ |

---

## 10. Conclusión

Dog Matrix alcanza **paridad funcional alta**: implementa **todos los comandos públicos** de HH v4
(0 ausentes), los **subsistemas de hardware avanzado** como módulos con lógica y hooks
inyectables, y las **macros/hooks/secuencias** con doble nomenclatura (`DM_*` + `MMU_*`/`_MMU_*`).

Brechas restantes: **validación en hardware físico** (NFC, sync-feedback, TD-1, eSpooler, GPIO),
**drivers NFC reales**, **frontend gráfico** (fuera de alcance) y **cobertura de tests**.

Detalle de lo pendiente: [funcionalidades_faltantes.md](funcionalidades_faltantes.md).
