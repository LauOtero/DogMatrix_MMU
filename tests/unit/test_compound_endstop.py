"""Pruebas del endstop compuesto (compound_endstop)."""

from __future__ import annotations

from dog_matrix.compound_endstop import CompoundEndstop, build_compound_endstop


def test_sensor_active_triggers():
    endstop = CompoundEndstop(sensor_reader=lambda: True)
    assert endstop.triggered() is True
    assert endstop.get_status()["triggers"] == 1


def test_sensor_inactive_without_encoder_does_not_trigger():
    endstop = CompoundEndstop(sensor_reader=lambda: False)
    assert endstop.triggered(measured_mm=100.0, expected_mm=10.0) is False


def test_encoder_threshold_triggers():
    endstop = CompoundEndstop(sensor_reader=lambda: False, encoder=object())
    assert endstop.triggered(measured_mm=9.9, expected_mm=10.0) is False
    assert endstop.triggered(measured_mm=10.0, expected_mm=10.0) is True
    assert endstop.triggered(measured_mm=12.0, expected_mm=10.0) is True


def test_encoder_with_zero_expected_does_not_trigger():
    endstop = CompoundEndstop(sensor_reader=lambda: False, encoder=object())
    assert endstop.triggered(measured_mm=500.0, expected_mm=0.0) is False


def test_sensor_exception_is_isolated():
    def boom() -> bool:
        raise RuntimeError("gpio")

    endstop = CompoundEndstop(sensor_reader=boom)
    assert endstop.triggered() is False


def test_sync_records_history_and_state():
    endstop = CompoundEndstop(sensor_reader=lambda: False, encoder=object())
    endstop.sync(measured_mm=10.0, expected_mm=10.0)
    endstop.sync(measured_mm=3.0, expected_mm=10.0)
    assert len(endstop.history) == 2
    assert endstop.history[0]["triggered"] is True
    assert endstop.history[1]["triggered"] is False
    assert endstop.get_status()["triggered"] is False


def test_reset_clears_state():
    endstop = CompoundEndstop(sensor_reader=lambda: True)
    endstop.triggered()
    endstop.reset()
    status = endstop.get_status()
    assert status["triggers"] == 0
    assert status["triggered"] is False
    assert endstop.history == []


def test_get_status_keys():
    endstop = CompoundEndstop(sensor_reader=lambda: True)
    endstop.triggered(measured_mm=5.0, expected_mm=4.0)
    status = endstop.get_status()
    assert set(status) == {"triggered", "sensor", "encoder_mm", "triggers"}
    assert status["sensor"] is True
    assert status["encoder_mm"] == 5.0


def test_build_factory_returns_instance():
    endstop = build_compound_endstop(sensor_reader=lambda: True)
    assert isinstance(endstop, CompoundEndstop)
