"""Pruebas del buffer de sincronizacion por realimentacion (sync_feedback)."""

from __future__ import annotations

import pytest

from dog_matrix.sync_feedback import (
    SF_COMPRESSED,
    SF_DISABLED,
    SF_EXPANDED,
    SF_NEUTRAL,
    SyncFeedback,
)


def _enabled(**overrides):
    values = {"sync_feedback_enabled": True}
    values.update(overrides)
    return SyncFeedback(config=values)


def test_disabled_returns_disabled():
    sf = SyncFeedback(config={})
    assert sf.evaluate(100.0, 100.0) == SF_DISABLED
    assert sf.speed_factor() == 1.0
    assert sf.get_status()["enabled"] is False


def test_explicit_tension_and_compression_flags():
    sf = _enabled()
    assert sf.evaluate(100.0, 100.0, compression=True) == SF_COMPRESSED
    assert sf.evaluate(100.0, 100.0, tension=True) == SF_EXPANDED


def test_ratio_above_and_below_thresholds():
    sf = _enabled(
        sync_feedback_compression_threshold=0.15,
        sync_feedback_tension_threshold=0.15,
    )
    assert sf.evaluate(100.0, 80.0) == SF_COMPRESSED  # ratio 0.20
    assert sf.evaluate(100.0, 120.0) == SF_EXPANDED  # ratio -0.20
    assert sf.evaluate(100.0, 90.0) == SF_NEUTRAL  # ratio 0.10
    assert sf.evaluate(100.0, 100.0) == SF_NEUTRAL


def test_speed_factor_matches_state():
    sf = _enabled()
    sf.evaluate(100.0, 80.0)
    assert sf.speed_factor() == 0.5
    sf.evaluate(100.0, 120.0)
    assert sf.speed_factor() == 1.5
    sf.evaluate(100.0, 100.0)
    assert sf.speed_factor() == 1.0


def test_autotune_converges_to_measured_factor():
    initial = 22.6789511
    factor = 1.05
    sf = _enabled(sync_feedback_rotation_distance=initial, sync_feedback_autotune=True)
    result = None
    for _ in range(20):
        result = sf.autotune_step(100.0, 100.0 * factor)
    assert result == pytest.approx(initial * factor, abs=1e-6)
    assert sf.rotation_distance == pytest.approx(initial * factor, abs=1e-6)
    assert sf.get_status()["autotune_active"] is False


def test_autotune_partial_returns_none_and_ignores_zero():
    sf = _enabled(sync_feedback_autotune=True)
    assert sf.autotune_step(0.0, 0.0) is None  # ignora solicitud <= 0
    assert sf.autotune_step(100.0, 105.0) is None  # aun no converge


def test_virtual_endstop_triggered():
    sf = _enabled()
    assert sf.virtual_endstop_triggered(10.0, 5.0) is True
    assert sf.virtual_endstop_triggered(5.0, 5.0) is True
    assert sf.virtual_endstop_triggered(4.9, 5.0) is False


def test_calibrate_psensor_validates_range():
    sf = _enabled()
    ok = sf.calibrate_psensor(0.0, 10.0)
    assert ok == {"min_mm": 0.0, "max_mm": 10.0, "span_mm": 10.0, "ok": True}
    bad = sf.calibrate_psensor(10.0, 5.0)
    assert bad["ok"] is False


def test_get_status_exposes_thresholds_and_samples():
    sf = _enabled()
    sf.evaluate(100.0, 80.0)
    status = sf.get_status()
    assert status["state"] == SF_COMPRESSED
    assert status["samples"] == 1
    assert status["speed_factor"] == 0.5
    assert status["thresholds"]["compression"] == 0.15
    assert status["thresholds"]["tension"] == 0.15


def test_isolates_exceptions_and_keeps_last_state():
    sf = _enabled()
    sf.evaluate(100.0, 80.0)
    # entrada no numerica: no debe propagar la excepcion
    assert sf.evaluate("x", "y") == SF_COMPRESSED
