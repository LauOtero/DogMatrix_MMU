"""Pruebas de integracion de la Fase 1 (contrato, comandos, autoload, fan)."""

from __future__ import annotations

from tests.fixtures.fakes import FakeGcodeError
import pytest


def test_contract_objects_registered(make_core):
    core, printer = make_core()
    assert printer.objects.get("mmu") is core
    assert "mmu_machine" in printer.objects
    machine = printer.objects["mmu_machine"].get_status(0.0)
    assert machine["num_gates"] == core.profile.gates


def test_get_status_contract_keys(make_core):
    core, _ = make_core()
    status = core.get_status(0.0)
    for key in ("print_state", "action", "filament_pos", "sensors", "fan",
                "autoload", "counters_store", "bypass", "sync_feedback_state"):
        assert key in status


def test_dm_select_gate_and_event(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_SELECT", {"GATE": 3})
    assert core.current_gate == 3
    assert any(name == "mmu:gate_selected" for name, _ in printer.sent_events)


def test_dm_select_bypass_requires_selector(make_core):
    core, printer = make_core(profile="ercf")
    printer.gcode.run("DM_SELECT", {"BYPASS": 1})
    assert core.bypass_active is True
    assert core.current_gate == -2


def test_dm_check_gate_all(make_core):
    core, printer = make_core()
    cmd = printer.gcode.run("DM_CHECK_GATE", {"ALL": 1})
    assert all(status == "available" for status in core.gate_status)
    assert any("CHECK_GATE" in r for r in cmd.responses)


def test_dm_sensors_enable_disable(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_SENSORS", {"SENSOR": "mmu_exit_0", "ENABLE": 0})
    assert core.get_status(0.0)["sensors"]["mmu_exit_0"] is False


def test_dm_motors_off_on(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_MOTORS", {"ACTION": "OFF"})
    assert core.mmu_enabled is False
    assert any(name == "mmu:disabled" for name, _ in printer.sent_events)
    printer.gcode.run("DM_MOTORS", {"ACTION": "ON"})
    assert core.mmu_enabled is True


def test_dm_reset_requires_confirm(make_core):
    core, printer = make_core()
    with pytest.raises(FakeGcodeError):
        printer.gcode.run("DM_RESET", {})
    printer.gcode.run("DM_RESET", {"CONFIRM": 1})
    assert core.current_gate is None


def test_dm_update_height(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_UPDATE_HEIGHT", {"HEIGHT": 12.5})
    assert core.print_height == 12.5


def test_dm_help_lists_commands(make_core):
    _, printer = make_core()
    cmd = printer.gcode.run("DM_HELP", {})
    assert any("DM_STATUS" in r for r in cmd.responses)


def test_dm_stats_counters(make_core):
    core, printer = make_core()
    printer.gcode.run("DM_STATS", {"COUNTER": "blade", "LIMIT": 2, "WARNING": "cambiar", "PAUSE": 1})
    printer.gcode.run("DM_STATS", {"COUNTER": "blade", "INCR": 1})
    printer.gcode.run("DM_STATS", {"COUNTER": "blade", "INCR": 1})
    assert core.counter_store.get("blade").value == 2
    assert core.counter_store.get("blade").warned is True
    cmd = printer.gcode.run("DM_STATS", {"SHOWCOUNTS": 1})
    assert any("blade" in r for r in cmd.responses)


def test_autoload_wiring_and_preload(make_core):
    core, printer = make_core(enable_autoload=True)
    printer.fire("klippy:ready")
    assert core.autoload is not None
    printer.gcode.run("DM_PRELOAD", {"GATE": 0})
    assert core.autoload.controller.state == "feeding"


def test_fan_control_command(make_core):
    core, printer = make_core(enable_fan_control=True, fan_on_temp=40, fan_off_temp=30)
    printer.fire("klippy:ready")
    assert core.fan is not None
    printer.gcode.run("DM_FAN", {"FAN_FORCED": 2})
    assert core.fan.state is True
    printer.gcode.run("DM_FAN", {"FAN_FORCED": 0})
    assert core.fan.state is False


def test_next_endless_gate(make_core):
    core, _ = make_core(endless_spool_groups=[[0, 1]])
    core.gate_status[1] = "available"
    assert core.next_endless_gate(0) == 1
    core.gate_status[1] = "empty"
    assert core.next_endless_gate(0) is None
