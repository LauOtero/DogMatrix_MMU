"""Pruebas del modo local gate (bypass sin MMU)."""

from __future__ import annotations

from dog_matrix.local_gate import LocalGate, build_local_gate
from tests.fixtures.fakes import FakeConfig


def test_build_local_gate_from_dict():
    gate = build_local_gate({"local_gate_enabled": True, "local_gate_gates": [0, 2]})
    assert gate.is_enabled()
    assert gate.gates == [0, 2]
    assert gate.is_local(0)
    assert gate.is_local(2)
    assert not gate.is_local(1)


def test_local_gate_disabled_by_default():
    gate = LocalGate()
    assert not gate.is_enabled()
    assert gate.gates == []
    assert not gate.select(0)
    assert gate.get_status()["active_gate"] is None


def test_enable_disable_and_reset():
    gate = LocalGate({"local_gate_enabled": False})
    gate.enable()
    assert gate.is_enabled()
    assert gate.select(1)
    assert gate.active_gate == 1
    gate.reset()
    assert gate.active_gate is None
    assert gate.is_enabled()
    gate.disable()
    assert not gate.is_enabled()
    assert gate.active_gate is None


def test_select_valid_and_invalid():
    gate = LocalGate({"local_gate_enabled": True, "local_gate_gates": [3]})
    assert gate.select(3)
    assert gate.active_gate == 3
    assert gate.select(9)
    assert gate.active_gate == 9
    gate.disable()
    assert not gate.select(3)


def test_get_status():
    gate = build_local_gate({"local_gate_enabled": True, "local_gate_gates": [1]})
    gate.select(1)
    assert gate.get_status() == {"enabled": True, "active_gate": 1, "gates": [1]}


def test_gates_normalization_ignores_invalid():
    gate = build_local_gate({"local_gate_gates": [0, "2", -1, "x", 0]})
    assert gate.gates == [0, 2]


def test_config_wrapper_source():
    config = FakeConfig(values={"local_gate_enabled": True, "local_gate_gates": [4, 5]})
    gate = LocalGate(config)
    assert gate.is_enabled()
    assert gate.is_local(5)
    assert gate.select(5)
