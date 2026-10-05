"""Pruebas del monitor del extrusor (extruder_monitor)."""

from __future__ import annotations

from dog_matrix.extruder_monitor import (
    EXTRUDER_OK,
    EXTRUDER_RUNOUT,
    EXTRUDER_SLIPPING,
    ExtruderMonitor,
    build_extruder_monitor,
)


def _enabled(**overrides):
    values = {"extruder_monitor_enabled": True}
    values.update(overrides)
    return ExtruderMonitor(config=values)


def test_disabled_is_always_ok():
    em = ExtruderMonitor(config={})  # enabled=False por defecto
    assert em.update(10.0, expected_mm=5.0) == EXTRUDER_OK
    assert em.update(10.0, expected_mm=5.0) == EXTRUDER_OK
    status = em.get_status()
    assert status["enabled"] is False
    assert status["state"] == EXTRUDER_OK


def test_ok_when_extruder_moves():
    em = _enabled(extruder_monitor_min_move_mm=0.5)
    assert em.update(10.0, expected_mm=5.0) == EXTRUDER_OK  # baseline
    assert em.update(11.0, expected_mm=5.0) == EXTRUDER_OK  # 1.0 mm de avance
    assert em.get_status()["moves"] == 2


def test_single_stall_is_slipping():
    em = _enabled(extruder_monitor_min_move_mm=0.5)
    em.update(10.0, expected_mm=5.0)  # baseline
    assert em.update(10.1, expected_mm=5.0) == EXTRUDER_SLIPPING
    assert em.get_status()["stall_count"] == 1


def test_repeated_stalls_become_runout():
    em = _enabled(extruder_monitor_min_move_mm=0.5)
    em.update(10.0, expected_mm=5.0)  # baseline
    assert em.update(10.1, expected_mm=5.0) == EXTRUDER_SLIPPING
    assert em.update(10.2, expected_mm=5.0) == EXTRUDER_RUNOUT


def test_movement_resets_stall_counter():
    em = _enabled(extruder_monitor_min_move_mm=0.5)
    em.update(10.0, expected_mm=5.0)  # baseline
    em.update(10.1, expected_mm=5.0)  # slipping
    assert em.update(20.0, expected_mm=5.0) == EXTRUDER_OK  # avance real
    assert em.get_status()["stall_count"] == 0


def test_no_expected_move_is_ok():
    em = _enabled(extruder_monitor_min_move_mm=0.5)
    em.update(10.0)  # baseline sin peticion
    assert em.update(10.0) == EXTRUDER_OK  # sin movimiento pedido no es fallo


def test_reset_clears_history():
    em = _enabled()
    em.update(10.0, expected_mm=5.0)
    em.update(10.1, expected_mm=5.0)
    em.reset()
    status = em.get_status()
    assert status["state"] == EXTRUDER_OK
    assert status["moves"] == 0
    assert status["last_position_mm"] is None
    assert status["stall_count"] == 0


def test_get_status_exposes_configuration_and_position():
    em = _enabled(extruder_monitor_min_move_mm=1.5)
    em.update(42.0, expected_mm=5.0)
    status = em.get_status()
    assert status["enabled"] is True
    assert status["last_position_mm"] == 42.0
    assert status["min_move_mm"] == 1.5
    assert status["moves"] == 1


def test_isolates_exceptions_and_keeps_state():
    em = _enabled()
    em.update(10.0, expected_mm=5.0)
    em.update(10.1, expected_mm=5.0)  # slipping
    assert em.update("x", "y") == EXTRUDER_SLIPPING  # no propaga


def test_build_extruder_monitor_reads_config_and_printer():
    printer = object()
    em = build_extruder_monitor(printer=printer, config={"extruder_monitor_enabled": True})
    assert isinstance(em, ExtruderMonitor)
    assert em.printer is printer
    assert em.get_status()["enabled"] is True
