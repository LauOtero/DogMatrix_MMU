"""Pruebas de las secuencias componibles de carga/descarga (F-35)."""

from __future__ import annotations

from dog_matrix.sequences import (
    Sequence,
    SequenceRegistry,
    build_default_load_sequence,
    build_default_unload_sequence,
)


def test_default_load_skips_missing_handlers():
    seq = build_default_load_sequence()
    assert seq.run({}) is True
    assert seq.completed
    assert all(result.skipped for result in seq.results)


def test_default_load_runs_provided_handlers():
    calls = []
    seq = build_default_load_sequence()
    ctx = {name: (lambda n=name: calls.append(n) or True) for name in
           ["pre_load", "home", "select", "load", "verify", "form_tip", "purge", "commit"]}
    assert seq.run(ctx)
    assert calls == ["pre_load", "home", "select", "load", "verify", "form_tip", "purge", "commit"]


def test_sequence_stops_on_failure():
    seq = Sequence("test")
    seq.add("a", lambda ctx: True)
    seq.add("b", lambda ctx: False)
    seq.add("c", lambda ctx: True)
    assert seq.run({}) is False
    assert seq.failed_step == "b"
    assert seq.results[-1].name == "b"


def test_sequence_isolates_exceptions():
    seq = Sequence("test")
    def boom(ctx):
        raise RuntimeError("x")
    seq.add("boom", boom)
    assert seq.run({}) is False
    assert "x" in seq.results[-1].message


def test_sequence_replace_and_insert():
    seq = build_default_unload_sequence()
    assert seq.replace("unload", lambda ctx: True)
    assert not seq.replace("missing", lambda ctx: True)
    seq.insert(0, "custom", lambda ctx: True)
    assert seq.steps[0][0] == "custom"


def test_registry():
    registry = SequenceRegistry()
    seq = build_default_load_sequence("load")
    registry.register(seq)
    assert registry.get("load") is seq
    assert "load" in registry.names()
    assert "sequences" in registry.get_status()
