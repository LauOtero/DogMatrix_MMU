"""Pruebas del nucleo (registro de comandos, estado y comandos G-code)."""

from __future__ import annotations

import pytest

from tests.fixtures.fakes import FakeGcodeError


def test_core_registers_commands(make_core):
    core, _ = make_core()
    commands = core._gcode().commands
    assert "DM_STATUS" in commands
    assert "DM_CHANGE" in commands
    assert "MMU_STATUS" in commands  # alias de migracion
    assert "MMU_CHANGE_TOOL" in commands  # alias compatible Happy Hare


def test_all_commands_and_aliases_registered(make_core):
    """Cada comando canonico y su alias ``MMU_*`` quedan registrados."""
    from dog_matrix.core import DogMatrixCore

    core, _ = make_core()
    commands = core._gcode().commands
    for name, alias in DogMatrixCore.COMMAND_ALIASES.items():
        assert name in commands, f"falta {name}"
        assert alias in commands, f"falta alias {alias}"


def test_get_status_structure(make_core):
    core, _ = make_core()
    status = core.get_status(0.0)
    assert status["state"] == "IDLE"
    assert status["gates"] == 8
    assert len(status["ttg_map"]) == 8
    assert status["profile"] == "dog_matrix.box_turtle.v1"


def test_dm_status_command(make_core):
    core, printer = make_core()
    gcmd = printer.gcode.run("DM_STATUS")
    assert any("Dog Matrix MMU" in response for response in gcmd.responses)


def test_dm_change_tool_success(make_core):
    core, printer = make_core(toolhead_present=True)
    gcmd = printer.gcode.run("DM_CHANGE", {"TOOL": 3})
    assert any("Toolchange OK" in response for response in gcmd.responses)
    assert core.current_tool == 3
    assert core.counters["toolchanges"] == 1


def test_dm_change_tool_out_of_range(make_core):
    core, printer = make_core()
    with pytest.raises(FakeGcodeError):
        printer.gcode.run("DM_CHANGE", {"TOOL": 99})


def test_dm_remap_ttg(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_REMAP_TTG", {"TOOL": 0, "GATE": 5})
    assert core.ttg_map[0] == 5


def test_dm_unload_resets_state(make_core):
    core, printer = make_core()
    core.current_gate = 2
    core.current_tool = 2
    printer.gcode.run("DM_UNLOAD")
    assert core.current_gate is None
    assert core.current_tool is None


def test_state_persisted_after_change(make_core):
    core, printer = make_core(toolhead_present=True)
    printer.gcode.run("DM_CHANGE", {"TOOL": 1})
    reloaded = core.persistence.load()
    assert reloaded["current_tool"] == 1


def test_dm_test_config_ok(make_core):
    core, printer = make_core()
    gcmd = printer.gcode.run("DM_TEST_CONFIG")
    assert any("Config OK" in response for response in gcmd.responses)


def test_dm_spoolman_disabled(make_core):
    core, printer = make_core()
    with pytest.raises(FakeGcodeError):
        printer.gcode.run("DM_SPOOLMAN")


def test_ready_and_shutdown_events(make_core):
    core, printer = make_core()
    printer.fire("klippy:ready")
    printer.fire("klippy:shutdown")
    assert core.persistence.load()["profile_id"] == "dog_matrix.box_turtle.v1"
