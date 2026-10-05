"""Pruebas del control de ventiladores con histeresis (fan_control)."""

from __future__ import annotations

import pytest

from dog_matrix.fan_control import FORCED_AUTO, FORCED_OFF, FORCED_ON, FanController


def _make(on=45.0, off=40.0, forced=FORCED_AUTO):
    applied = []
    fan = FanController(set_fan=applied.append, on_temp=on, off_temp=off, forced=forced)
    return fan, applied


def test_on_off_hysteresis():
    fan, applied = _make()
    fan.update(30.0)
    assert fan.state is False and applied == []
    fan.update(46.0)  # supera ON -> enciende
    assert fan.state is True and applied[-1] == 1.0
    fan.update(42.0)  # entre OFF y ON -> permanece ON (histeresis)
    assert fan.state is True
    fan.update(39.0)  # por debajo de OFF -> apaga
    assert fan.state is False and applied[-1] == 0.0


def test_forced_modes():
    fan, applied = _make(forced=FORCED_ON)
    fan.update(0.0)
    assert fan.state is True
    fan.set_forced(FORCED_OFF)
    assert fan.state is False and applied[-1] == 0.0


def test_disable_turns_off_and_tick_returns_deadline():
    fan, _ = _make(forced=FORCED_ON)
    fan.update(100.0)
    fan.enable(False)
    assert fan.state is False
    assert fan.tick(10.0) == 10.0 + fan.poll_s


def test_invalid_hysteresis_raises():
    with pytest.raises(ValueError):
        FanController(set_fan=lambda _p: None, on_temp=30.0, off_temp=40.0)


def test_actuator_failure_does_not_propagate():
    def boom(_p):
        raise RuntimeError("gpio")

    fan = FanController(set_fan=boom, on_temp=20.0, off_temp=10.0)
    assert fan.update(50.0) is True  # no lanza
