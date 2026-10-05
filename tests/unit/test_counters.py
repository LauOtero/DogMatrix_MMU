"""Pruebas de los contadores de mantenimiento/consumo."""

from __future__ import annotations

from dog_matrix.counters import CounterStore


def test_define_incr_limit_and_event_once():
    events = []
    store = CounterStore(on_event=events.append)
    store.define("blade", limit=3, warning="cambiar cuchilla", pause=True)
    assert store.incr("blade").value == 1
    assert store.incr("blade").value == 2
    event = store.incr("blade")
    assert event.triggered is True and event.pause is True
    # Solo dispara una vez aunque siga incrementando.
    assert store.incr("blade").triggered is False
    assert len(events) == 1
    assert events[0].name == "blade" and events[0].warning == "cambiar cuchilla"


def test_reset_and_delete():
    store = CounterStore()
    store.define("x", limit=5)
    store.incr("x", 4)
    assert store.get("x").value == 4
    assert store.reset("x") is True
    assert store.get("x").value == 0
    assert store.delete("x") is True
    assert store.get("x") is None


def test_no_limit_never_triggers():
    store = CounterStore()
    store.define("y")
    for _ in range(1000):
        assert store.incr("y").triggered is False


def test_serialization_roundtrip():
    store = CounterStore()
    store.define("z", limit=10, warning="w")
    store.incr("z", 3)
    payload = store.as_dict()
    other = CounterStore()
    other.load(payload)
    assert other.get("z").value == 3
    assert other.get("z").limit == 10
    assert other.get("z").warning == "w"
