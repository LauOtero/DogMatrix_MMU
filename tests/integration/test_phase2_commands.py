"""Pruebas de integracion de la Fase 2/3 (comandos y contrato ampliado)."""

from __future__ import annotations

import pytest

from tests.fixtures.fakes import FakeGcodeError


def test_status_exposes_advanced_subsystems(make_core):
    core, _ = make_core()
    status = core.get_status(0.0)
    for key in (
        "slicer_tool_map", "calibration", "tip_forming", "purge",
        "environment", "espooler", "led", "td1", "nfc",
        "ejection_buttons", "sequences", "telemetry", "spoolman",
        "sync_feedback",
    ):
        assert key in status


def test_select_bypass_command(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_SELECT_BYPASS", {})
    assert core.bypass_active is True
    assert core.current_gate == -2


def test_motors_off_on_aliases(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_MOTORS_OFF", {})
    assert core.mmu_enabled is False
    printer.gcode.run("DM_MOTORS_ON", {})
    assert core.mmu_enabled is True


def test_sync_feedback_status(make_core):
    _, printer = make_core()
    cmd = printer.gcode.run("DM_SYNC_FEEDBACK", {})
    assert any("Sync-feedback" in r for r in cmd.responses)


def test_slicer_tool_map_set(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_SLICER_TOOL_MAP", {"MAP": "1,0,2"})
    assert core.slicer_map.as_tool_map()[:3] == [1, 0, 2]
    status = core.get_status(0.0)
    assert status["slicer_tool_map"][:3] == [1, 0, 2]


@pytest.mark.parametrize(
    "command,params",
    [
        ("DM_CALC_PURGE_VOLUMES", {}),
        ("DM_HEATER", {"DRY": 50}),
        ("DM_TD1", {"ACTION": "READ"}),
        ("DM_LED", {"EFFECT": "blink"}),
        ("DM_SET_LED", {"INDEX": 0, "COLOR": "ff0000"}),
    ],
)
def test_feature_disabled_command_raises(make_core, command, params):
    """Los comandos de subsistemas deshabilitados fallan con error G-code."""
    _, printer = make_core()
    with pytest.raises(FakeGcodeError):
        printer.gcode.run(command, params)


def test_purge_matrix_command(make_core):
    core, printer = make_core(enable_purge=True)
    core.gate_filament[0]["material"] = "PLA"
    core.gate_filament[1]["material"] = "ABS"
    cmd = printer.gcode.run("DM_CALC_PURGE_VOLUMES", {})
    assert any("Purga total" in r for r in cmd.responses)


def test_heater_dry_cycle(make_core):
    core, printer = make_core(enable_environment=True, dryer_target_temp=55)
    printer.gcode.run("DM_HEATER", {"DRY": "60", "DURATION": "60"})
    assert core.environment.status.drying is True
    assert core.environment.status.dryer_target_temp == 60.0
    printer.gcode.run("DM_HEATER", {"STOP": 1})
    assert core.environment.status.drying is False


def test_espooler_burst(make_core):
    core, printer = make_core(enable_espooler=True)
    printer.gcode.run("DM_ESPOOLER", {"ACTION": "BURST", "DURATION": "1", "SPEED": "0.5"})
    assert core.espooler.status.burst_until > 0
    assert any(name == "mmu:espooler_burst" for name, _ in printer.sent_events)


def test_led_effect_command(make_core):
    core, printer = make_core(enable_led=True)
    printer.gcode.run("DM_LED", {"EFFECT": "blink"})
    assert core.led.effect == "blink"
    printer.gcode.run("DM_SET_LED", {"INDEX": "2", "COLOR": "ff0000"})
    assert core.led._base_colors[2] == (255, 0, 0)


def test_tip_forming_test_command(make_core):
    core, printer = make_core(enable_tip_forming=True)
    cmd = printer.gcode.run("DM_TEST_FORM_TIP", {})
    assert any("tip forming" in r.lower() for r in cmd.responses)


def test_td1_read_with_simulation(make_core):
    core, printer = make_core(td1_enabled=True, td1_count=1)
    cmd = printer.gcode.run("DM_TD1", {"ACTION": "READ", "GATE": 1, "TD": "0.5", "COLOR": "ff0000"})
    assert any("TD-1 gate 1" in r for r in cmd.responses)
    assert 1 in core.td1.gate_readings


def test_nfc_scan_simulated(make_core):
    core, printer = make_core(enable_nfc=True)
    core.nfc.simulate_tag("DEADBEEF", {"material": "PLA", "color": "#ff0000"})
    cmd = printer.gcode.run("DM_NFC_SCAN", {})
    assert any("DEADBEEF" in r for r in cmd.responses)


def test_spoolman_tag_and_support(make_core):
    core, printer = make_core(enable_spoolman=True, spoolman_support="readonly")
    assert core.spoolman.support == "readonly"
    printer.gcode.run("DM_SPOOLMAN", {"TAG": "UID1", "SPOOL_ID": 7})
    assert core.spoolman.resolve_tag("UID1") == 7
    printer.gcode.run("DM_SPOOLMAN", {"SUPPORT": "push"})
    assert core.spoolman.support == "push"


def test_cold_pull_command(make_core):
    core, printer = make_core()
    cmd = printer.gcode.run("DM_COLD_PULL", {"TEMP": "90", "LENGTH": "30"})
    assert any("Cold pull" in r for r in cmd.responses)


def test_park_command(make_core):
    core, printer = make_core(park_positions={"default": [10, 10]})
    cmd = printer.gcode.run("DM_PARK", {})
    assert any("Parking OK" in r for r in cmd.responses)


def test_test_move_and_tracking(make_core):
    _, printer = make_core()
    cmd = printer.gcode.run("DM_TEST_MOVE", {"DISTANCE": "10"})
    assert any("Test move" in r for r in cmd.responses)
    cmd = printer.gcode.run("DM_TEST_TRACKING", {"DISTANCE": "10"})
    assert any("Tracking" in r for r in cmd.responses)


def test_test_runout_classification(make_core):
    _, printer = make_core()
    cmd = printer.gcode.run("DM_TEST_RUNOUT", {"DISTANCE": "50"})
    assert any("runout" in r for r in cmd.responses)


def test_soaktest_selector(make_core):
    core, printer = make_core()
    cmd = printer.gcode.run("DM_SOAKTEST_SELECTOR", {"COUNT": "2"})
    assert any("Soak test selector" in r for r in cmd.responses)
    assert core.telemetry.channels()


def test_log_and_dump_vars(make_core):
    _, printer = make_core()
    cmd = printer.gcode.run("DM_LOG", {"MESSAGE": "hola", "LEVEL": "info"})
    assert any("hola" in r for r in cmd.responses)
    cmd = printer.gcode.run("DM_DUMP_VARS", {})
    assert any("num_gates" in r for r in cmd.responses)


def test_eject_button_action_dispatches_gcode(make_core):
    core, printer = make_core(enable_ejection_buttons=True)
    core.ejection_buttons.trigger("eject", eventtime=1.0)
    assert any(script.startswith("DM_EJECT") for script in printer.gcode.scripts)


def test_run_load_sequence(make_core):
    core, _ = make_core()
    status = core.run_load_sequence(2, 1)
    assert "completed" in status


def test_status_compact_console(make_core):
    _, printer = make_core()
    cmd = printer.gcode.run("DM_STATUS", {"COMPACT": 1})
    assert any("print=" in r for r in cmd.responses)
    assert any("gates:" in r for r in cmd.responses)


def test_calibrate_gear_with_simulated_encoder(make_core):
    core, printer = make_core()
    core.encoder.filter_alpha = 1.0

    class _MotionWithFeedback:
        def load_filament(self, distance, speed):
            core.encoder.set_simulated_position(core.encoder.read_position() + distance * 0.99)
            return True

    core.calibrator.motion = _MotionWithFeedback()
    cmd = printer.gcode.run("DM_CALIBRATE_GEAR", {"GATE": "1", "DISTANCE_MM": "100"})
    assert any("Calibracion gear gate 1 OK" in r for r in cmd.responses)
    assert "gear" in core.calibration_results
