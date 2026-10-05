"""Pruebas de la maquina de estados (FSM)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dog_matrix.state_machine import (
    ERR_INVALID_GATE,
    ERR_LOAD_FAILED,
    MMUState,
    StateMachine,
    TransitionError,
)


class _FakeMotion:
    def __init__(self, load_ok=True, unload_ok=True):
        self.load_ok = load_ok
        self.unload_ok = unload_ok
        self.calls = []

    def prepare(self):
        self.calls.append("prepare")

    def load_filament(self, distance, speed):
        self.calls.append("load")
        return self.load_ok

    def unload_filament(self, distance, speed):
        self.calls.append("unload")
        return self.unload_ok

    def purge(self, length):
        self.calls.append("purge")
        return True


class _FakeSelector:
    def __init__(self):
        self.gates = []

    def select_gate(self, gate):
        self.gates.append(gate)
        return True

    def home(self):
        return True


class _FakeSensors:
    def __init__(self, present=True):
        self.present = present

    def is_present(self, name):
        return self.present


class _FakePersistence:
    def __init__(self):
        self.saved = None

    def save(self, data):
        self.saved = data


class _FakeCore:
    def __init__(self, gates=4, load_ok=True):
        self.profile = SimpleNamespace(gates=gates, limits={"max_distance_mm": 1000, "max_load_speed_mm_s": 80, "max_unload_speed_mm_s": 80})
        self.motion = _FakeMotion(load_ok=load_ok)
        self.selector = _FakeSelector()
        self.sensors = _FakeSensors()
        self.diagnostics = SimpleNamespace(log_event=lambda *a, **k: None)
        self.persistence = _FakePersistence()
        self.current_gate = None
        self.current_tool = None
        self.enable_purge = False
        self.auto_recover = False
        self.events = []

    def snapshot_state(self):
        return {"current_gate": self.current_gate, "current_tool": self.current_tool}


def test_valid_transition():
    machine = StateMachine(_FakeCore())
    machine.transition(MMUState.REQUESTED, "op1")
    assert machine.get_state() == MMUState.REQUESTED.value


def test_invalid_transition_raises():
    machine = StateMachine(_FakeCore())
    with pytest.raises(TransitionError):
        machine.transition(MMUState.LOAD)


def test_toolchange_success():
    core = _FakeCore()
    machine = StateMachine(core)
    result = machine.execute_toolchange(2, 1)
    assert result.success
    assert result.state == MMUState.COMPLETED.value
    assert core.current_gate == 2
    assert core.persistence.saved is not None
    assert core.selector.gates == [2]


def test_toolchange_load_failure():
    core = _FakeCore(load_ok=False)
    machine = StateMachine(core)
    result = machine.execute_toolchange(1, 0)
    assert not result.success
    assert result.error_code == ERR_LOAD_FAILED
    assert result.state == MMUState.FAILED.value


def test_toolchange_invalid_gate():
    machine = StateMachine(_FakeCore(gates=2))
    result = machine.execute_toolchange(5, 0)
    assert not result.success
    assert result.error_code == ERR_INVALID_GATE


def test_toolchange_skips_unload_when_no_filament():
    core = _FakeCore()
    machine = StateMachine(core)
    machine.execute_toolchange(1, 0)
    assert "unload" not in core.motion.calls


def test_toolchange_unloads_when_filament_loaded():
    core = _FakeCore()
    core.current_gate = 0
    core.current_tool = 0
    machine = StateMachine(core)
    machine.execute_toolchange(1, 1)
    assert "unload" in core.motion.calls
