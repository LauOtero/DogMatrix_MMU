"""Pruebas de simulacion: toolchange end-to-end sin hardware."""

from __future__ import annotations

from tests.fixtures.fakes import FakeGcodeError


def test_toolchange_sequence_multiple_gates(make_core):
    core, printer = make_core(toolhead_present=True)
    for tool in range(4):
        gcmd = printer.gcode.run("DM_CHANGE", {"TOOL": tool})
        assert any("Toolchange OK" in response for response in gcmd.responses)
        assert core.current_tool == tool
    assert core.counters["toolchanges"] == 4
    assert core.get_status(0.0)["state"] == "COMPLETED"


def test_toolchange_emits_movement_scripts(make_core):
    core, printer = make_core(toolhead_present=True)
    printer.gcode.run("DM_CHANGE", {"TOOL": 1})
    # El gate 0 no estaba cargado: debe haber al menos un movimiento de carga.
    assert any(script.startswith("G1 E") for script in printer.gcode.scripts)


def test_toolchange_fails_without_filament_sensor(make_core):
    core, printer = make_core()
    core.sensors.set_simulated_state("toolhead", False)
    try:
        printer.gcode.run("DM_CHANGE", {"TOOL": 0})
        raised = False
    except FakeGcodeError:
        raised = True
    assert raised
    assert core.get_status(0.0)["state"] == "FAILED"
    assert core.counters["errors"] == 1


def test_recovery_after_failure(make_core):
    core, printer = make_core()
    core.sensors.set_simulated_state("toolhead", False)
    try:
        printer.gcode.run("DM_CHANGE", {"TOOL": 0})
    except Exception:  # noqa: BLE001 - fallo esperado
        pass
    gcmd = printer.gcode.run("DM_RECOVER", {"CODE": "ERR_VERIFY_FAILED"})
    assert gcmd.responses  # la recuperacion responde


def test_endless_spool_report(make_core):
    core, printer = make_core()
    gcmd = printer.gcode.run("DM_ENDLESS_SPOOL")
    assert any("EndlessSpool" in response for response in gcmd.responses)
