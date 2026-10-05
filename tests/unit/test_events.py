"""Pruebas del contrato de estado y eventos compatibles con Happy Hare."""

from __future__ import annotations

from dog_matrix import events as ev


class _FakePrinter:
    def __init__(self) -> None:
        self.sent = []

    def send_event(self, name, **kwargs):
        self.sent.append((name, kwargs))


class _FakeCore:
    class _SM:
        def get_state(self):
            return "IDLE"

    def __init__(self) -> None:
        self.state_machine = self._SM()
        self.profile = type("P", (), {"gates": 4, "profile_id": "dog_matrix.box_turtle.v1", "selector_type": "virtual"})()
        self.current_gate = 2
        self.current_tool = 1
        self.ttg_map = [0, 1, 2, 3]
        self.gate_status = ["available", "available", "unknown", "empty"]
        self.gate_filament = [
            {"material": "PLA", "color": "#111111", "spool_id": "s0"},
            {"material": "PLA", "color": "#222222", "spool_id": "s1"},
            {"material": "PETG", "color": "#333333", "spool_id": "s2"},
            {"material": "", "color": "", "spool_id": ""},
        ]
        self.counters = {"toolchanges": 5}
        self.endless_spool_groups = [[0, 1], [2, 3]]
        self.enable_endless_spool = True
        self.controller = None
        self.sync_feedback_state = "neutral"

    def sensor_flags(self):
        return {"encoder": True}


def test_event_emitter_calls_printer_and_local_subscribers():
    printer = _FakePrinter()
    emitter = ev.EventEmitter(printer)
    seen = []
    emitter.subscribe(ev.EVENT_GATE_SELECTED, lambda **kw: seen.append(kw))
    emitter.emit(ev.EVENT_GATE_SELECTED, gate=3, previous_gate=1)
    assert printer.sent == [("mmu:gate_selected", {"gate": 3, "previous_gate": 1})]
    assert seen == [{"gate": 3, "previous_gate": 1}]


def test_event_emitter_isolates_failures():
    class _Boom:
        def send_event(self, *a, **k):
            raise RuntimeError("bus caido")

    emitter = ev.EventEmitter(_Boom())

    def bad(**kwargs):
        raise RuntimeError("subscriber")

    emitter.subscribe(ev.EVENT_ENABLED, bad)
    emitter.emit(ev.EVENT_ENABLED)  # no debe lanzar


def test_build_mmu_state_contract_keys():
    state = ev.build_mmu_state(_FakeCore(), 0.0)
    for key in ("enabled", "num_gates", "print_state", "tool", "gate", "filament_pos",
                "ttg_map", "gate_status", "active_filament", "sensors"):
        assert key in state
    assert state["tool"] == 1
    assert state["gate"] == 2
    assert state["active_filament"]["color"] == "#333333"
    assert state["num_gates"] == 4


def test_build_mmu_machine_single_unit_fallback():
    machine = ev.build_mmu_machine(_FakeCore())
    assert machine["num_units"] == 1
    assert machine["units"][0]["num_gates"] == 4
