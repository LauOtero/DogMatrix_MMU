"""Pruebas de FlowGuard (deteccion de divergencia)."""

from __future__ import annotations

from types import SimpleNamespace

from dog_matrix.flowguard import FLOW_CLOG, FLOW_OK, FLOW_RUNOUT, FlowGuard


def _guard(threshold=5.0):
    profile = SimpleNamespace(limits={"encoder_error_mm": threshold})
    return FlowGuard(profile=profile)


def test_ok_when_no_divergence():
    guard = _guard()
    result = guard.evaluate(100.0, 100.0)
    assert result.state == FLOW_OK
    assert not result.is_error


def test_requires_hysteresis_before_error():
    guard = _guard()
    first = guard.evaluate(100.0, 50.0)
    assert not first.is_error  # primera violacion no confirma
    for _ in range(2):
        last = guard.evaluate(100.0, 50.0)
    assert last.is_error
    assert last.state == FLOW_CLOG


def test_runout_classification():
    guard = _guard()
    result = None
    for _ in range(3):
        result = guard.evaluate(100.0, 0.0)
    assert result.is_error
    assert result.state == FLOW_RUNOUT


def test_reset_clears_counter():
    guard = _guard()
    guard.evaluate(100.0, 50.0)
    guard.evaluate(100.0, 50.0)
    guard.reset()
    result = guard.evaluate(100.0, 50.0)
    assert not result.is_error


def test_adaptive_mode_raises_threshold():
    guard = _guard(threshold=5.0)
    guard.set_adaptive_mode(True)
    guard.update_thresholds(material="TPU", temperature=250)
    stats = guard.get_statistics()
    assert stats["threshold_mm"] > 5.0
    assert stats["adaptive"] is True


def test_statistics_accumulate():
    guard = _guard()
    for _ in range(3):
        guard.evaluate(100.0, 0.0)
    stats = guard.get_statistics()
    assert stats["evaluations"] == 3
    assert stats["errors"] >= 1
