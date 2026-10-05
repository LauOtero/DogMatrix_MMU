"""Pruebas de integracion de la Fase 3 (ecosistema HH, hardware avanzado, hooks)."""

from __future__ import annotations

import pytest

from dog_matrix.core import DogMatrixCore
from tests.fixtures.fakes import FakeGcodeError


# --- Registro de comandos y alias ------------------------------------------
def test_new_commands_and_aliases_registered(make_core):
    core, _ = make_core()
    commands = core._gcode().commands
    new_commands = [
        "DM_FLOWGUARD", "DM_PAUSE", "DM_TOOL_OVERRIDES", "DM_TEST_PURGE",
        "DM_CHANGE_TOOL_STANDALONE", "DM_PRINT_START", "DM_PRINT_END",
        "DM_START_CHECK", "DM_START_LOAD_INITIAL_TOOL", "DM_END",
        "DM_CALIBRATE_ROTARY_SELECTOR", "DM_CALIBRATE_SELECTOR_INDEXES",
        "DM_CALIBRATE_SERVO_SELECTOR", "DM_NFC", "DM_LOCAL_GATE",
        "DM_STEPPER_CURRENT", "DM_EXTRUDER_MONITOR", "DM_COMPOUND_ENDSTOP",
        "DM_FILAMENT_DISPLAY", "DM_STEP",
    ]
    for name in new_commands:
        assert name in commands, f"falta {name}"
        assert DogMatrixCore.COMMAND_ALIASES[name] in commands, f"falta alias de {name}"


def test_step_commands_registered(make_core):
    core, _ = make_core()
    commands = core._gcode().commands
    for name in ("_MMU_STEP_HOME", "_MMU_STEP_LOAD", "DM_STEP_LOAD", "MMU_STEP_SELECT"):
        assert name in commands, f"falta {name}"


# --- Comandos de ecosistema ------------------------------------------------
def test_flowguard_command(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_FLOWGUARD", {"MODE": "tangle", "THRESHOLD": "3"})
    assert core.flowguard.encoder_mode == "tangle"
    assert core.flowguard.threshold_mm == 3.0
    cmd = printer.gcode.run("MMU_FLOWGUARD", {"RESET": 1})
    assert any("FlowGuard" in r for r in cmd.responses)


def test_pause_command(make_core):
    core, printer = make_core(enable_autoload=True)
    printer.fire("klippy:ready")
    printer.gcode.run("DM_PAUSE", {})
    assert any(name == "mmu:mmu_paused" for name, _ in printer.sent_events)


def test_tool_overrides_command(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_TOOL_OVERRIDES", {"TOOL": 1, "SPEED_FACTOR": "0.5", "PURGE_MM": "40"})
    override = core.tool_overrides.get(1)
    assert override is not None and override.speed_factor == 0.5
    assert core.tool_overrides.apply_purge(1, 10.0) == 40.0
    cmd = printer.gcode.run("MMU_TOOL_OVERRIDES", {})
    assert any("Tool overrides" in r for r in cmd.responses)
    printer.gcode.run("DM_TOOL_OVERRIDES", {"TOOL": 1, "CLEAR": 1})
    assert core.tool_overrides.get(1) is None


def test_test_purge_requires_enable(make_core):
    _, printer = make_core()
    with pytest.raises(FakeGcodeError):
        printer.gcode.run("DM_TEST_PURGE", {})


def test_test_purge_command(make_core):
    core, printer = make_core(enable_purge=True)
    cmd = printer.gcode.run("DM_TEST_PURGE", {"VOLUME": "250"})
    assert any("Test purge" in r for r in cmd.responses)
    assert "test_purge_mm3" in core.telemetry.channels()


def test_change_tool_standalone(make_core):
    core, printer = make_core(toolhead_present=True)
    cmd = printer.gcode.run("DM_CHANGE_TOOL_STANDALONE", {"TOOL": 1})
    assert any("Toolchange OK" in r for r in cmd.responses)


# --- Macros de ciclo de impresion ------------------------------------------
def test_print_lifecycle_commands(make_core):
    core, printer = make_core(toolhead_present=True)
    printer.gcode.run("MMU_PRINT_START", {"INITIAL_TOOL": 1})
    assert core.print_state == "printing"
    assert any(name == "mmu:printing" for name, _ in printer.sent_events)
    printer.gcode.run("DM_PRINT_END", {})
    assert core.print_state == "complete"
    assert any(name == "mmu:not_printing" for name, _ in printer.sent_events)


def test_start_check_and_end(make_core):
    core, printer = make_core()
    cmd = printer.gcode.run("DM_START_CHECK", {})
    assert any("Start check" in r for r in cmd.responses)
    cmd = printer.gcode.run("DM_END", {})
    assert any("MMU end" in r for r in cmd.responses)


def test_start_load_initial_tool(make_core):
    core, printer = make_core(toolhead_present=True)
    cmd = printer.gcode.run("DM_START_LOAD_INITIAL_TOOL", {"TOOL": 2})
    assert any("Toolchange OK" in r for r in cmd.responses)
    assert core.current_tool == 2


def test_hooks_emit_dm_and_mmu_aliases(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_PRINT_STATE", {"STATE": "printing"})
    scripts = " ".join(printer.gcode.scripts)
    assert "_DM_PRINT_STATE_CHANGED" in scripts
    assert "_MMU_PRINT_STATE_CHANGED" in scripts
    assert "user_print_state_changed_extension" in scripts


# --- Calibracion de selectores avanzada ------------------------------------
@pytest.mark.parametrize(
    "command",
    [
        "DM_CALIBRATE_ROTARY_SELECTOR",
        "DM_CALIBRATE_SELECTOR_INDEXES",
        "DM_CALIBRATE_SERVO_SELECTOR",
    ],
)
def test_selector_calibration_commands(make_core, command):
    core, printer = make_core()
    cmd = printer.gcode.run(command, {})
    assert any("Calibracion" in r for r in cmd.responses)
    assert core.calibration_results


# --- Pasos de secuencia ----------------------------------------------------
def test_dm_step_command(make_core):
    core, printer = make_core()
    cmd = printer.gcode.run("DM_STEP", {"NAME": "HOME"})
    assert any("Paso home OK" in r for r in cmd.responses)


def test_mmu_step_alias(make_core):
    core, printer = make_core()
    cmd = printer.gcode.run("_MMU_STEP_HOME", {})
    assert any("Paso home OK" in r for r in cmd.responses)


def test_dm_step_lists_when_no_name(make_core):
    _, printer = make_core()
    cmd = printer.gcode.run("DM_STEP", {})
    assert any("Pasos disponibles" in r for r in cmd.responses)


# --- Hardware avanzado -----------------------------------------------------
def test_local_gate_command(make_core):
    core, printer = make_core(local_gate_enabled=True, local_gate_gates=[0, 2])
    printer.gcode.run("DM_LOCAL_GATE", {"ACTION": "ENABLE"})
    printer.gcode.run("DM_LOCAL_GATE", {"ACTION": "SELECT", "GATE": 2})
    assert core.local_gate.get_status()["active_gate"] == 2
    cmd = printer.gcode.run("MMU_LOCAL_GATE", {})
    assert any("Local gate" in r for r in cmd.responses)


def test_stepper_current_command(make_core):
    core, printer = make_core(stepper_currents={"gear": {"stepper": "stepper_mmu_gear", "run_current": 0.8}})
    cmd = printer.gcode.run("DM_STEPPER_CURRENT", {"MOTOR": "gear", "RUN_CURRENT": "0.9"})
    assert any("Current gear" in r for r in cmd.responses)
    assert any("SET_TMC_CURRENT" in script for script in printer.gcode.scripts)


def test_extruder_monitor_command(make_core):
    core, printer = make_core(extruder_monitor_enabled=True)
    cmd = printer.gcode.run("DM_EXTRUDER_MONITOR", {"ACTION": "READ", "EXPECTED": "1"})
    assert any("Extruder monitor" in r for r in cmd.responses)


def test_compound_endstop_command(make_core):
    core, printer = make_core(toolhead_present=True)
    cmd = printer.gcode.run("DM_COMPOUND_ENDSTOP", {"TEST": 1, "MEASURED": "5", "EXPECTED": "3"})
    assert any("triggered=True" in r for r in cmd.responses)


def test_filament_display_command(make_core):
    core, printer = make_core()
    core.gate_filament[0] = {"material": "PLA", "color": "#ff0000", "spool_id": "1", "availability": "available"}
    cmd = printer.gcode.run("DM_FILAMENT_DISPLAY", {})
    assert any("PLA" in r for r in cmd.responses)


def test_nfc_command_disabled(make_core):
    _, printer = make_core()
    with pytest.raises(FakeGcodeError):
        printer.gcode.run("DM_NFC", {})


def test_nfc_command_status_and_register(make_core):
    core, printer = make_core(enable_nfc=True, nfc_count=2, nfc_type="pn532")
    cmd = printer.gcode.run("DM_NFC", {})
    assert any("NFC" in r for r in cmd.responses)
    printer.gcode.run("DM_NFC", {"ACTION": "REGISTER", "UID": "AABB", "SPOOL_ID": 3})
    printer.gcode.run("DM_NFC", {"ACTION": "RELEASE"})
    assert core.nfc_arbiter is not None and core.nfc_endstop is not None


# --- EndlessSpool failover -------------------------------------------------
def test_endless_failover(make_core):
    core, _ = make_core(enable_endless_spool=True, endless_spool_groups=[[0, 1]])
    core.current_gate = 0
    core.current_tool = 0
    core.gate_status[1] = "available"
    new_gate = core.try_endless_failover()
    assert new_gate == 1
    assert core.current_gate == 1
    assert core.gate_status[0] == "empty"
    assert core.ttg_map[0] == 1


def test_endless_failover_disabled(make_core):
    core, _ = make_core()
    core.current_gate = 0
    assert core.try_endless_failover() is None
