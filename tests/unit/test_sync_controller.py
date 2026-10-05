"""Pruebas del controlador de sincronizacion (sync_controller)."""

from __future__ import annotations

import pytest

from dog_matrix.sync_controller import (
    SYNC_OFF,
    SYNC_PROPORTIONAL,
    SYNC_TWO_LEVEL,
    SyncController,
    build_sync_controller,
)


def test_off_mode_is_neutral():
    sc = SyncController(config={"sync_mode": SYNC_OFF})
    assert sc.update(100.0, 10.0) == 1.0
    assert sc.update(100.0, 200.0) == 1.0
    assert sc.get_status()["mode"] == SYNC_OFF


def test_default_mode_is_off():
    sc = SyncController()
    assert sc.update(50.0, 0.0) == 1.0
    assert sc.get_status()["mode"] == SYNC_OFF


def test_proportional_corrects_both_directions():
    sc = SyncController(
        config={"sync_mode": SYNC_PROPORTIONAL, "sync_proportional_gain": 0.1}
    )
    assert sc.update(100.0, 90.0) == pytest.approx(1.01)  # falta material -> acelera
    assert sc.update(100.0, 110.0) == pytest.approx(0.99)  # sobra material -> frena
    assert sc.update(100.0, 100.0) == pytest.approx(1.0)


def test_proportional_clamped_to_range():
    sc = SyncController(
        config={"sync_mode": SYNC_PROPORTIONAL, "sync_proportional_gain": 0.5}
    )
    assert sc.update(100.0, 1000.0) == 0.5  # error negativo grande -> clamp bajo
    assert sc.update(100.0, -1000.0) == 1.5  # error positivo grande -> clamp alto


def test_two_level_discrete_correction():
    sc = SyncController(
        config={
            "sync_mode": SYNC_TWO_LEVEL,
            "sync_compression_threshold": 1.0,
            "sync_tension_threshold": 1.0,
        }
    )
    assert sc.update(100.0, 90.0) == 0.5  # comprimido
    assert sc.update(100.0, 110.0) == 1.5  # expandido
    assert sc.update(100.0, 100.0) == 1.0  # neutro
    assert sc.update(100.0, 99.0) == 1.0  # justo en el umbral -> neutro


def test_ignores_non_positive_request():
    sc = SyncController(config={"sync_mode": SYNC_PROPORTIONAL})
    assert sc.update(0.0, 0.0) == 1.0
    assert sc.update(-5.0, 0.0) == 1.0
    assert sc.get_status()["updates"] == 0  # no cuenta peticiones invalidas


def test_reset_clears_state():
    sc = SyncController(config={"sync_mode": SYNC_TWO_LEVEL})
    sc.update(100.0, 10.0)
    assert sc.get_status()["last_factor"] == 0.5
    sc.reset()
    status = sc.get_status()
    assert status["last_factor"] == 1.0
    assert status["updates"] == 0
    assert status["mode"] == SYNC_TWO_LEVEL  # el modo se conserva


def test_get_status_exposes_configuration_and_counters():
    sc = SyncController(
        config={
            "sync_mode": SYNC_PROPORTIONAL,
            "sync_proportional_gain": 0.2,
            "sync_tension_threshold": 2.0,
            "sync_compression_threshold": 3.0,
        }
    )
    sc.update(100.0, 50.0)
    status = sc.get_status()
    assert status["mode"] == SYNC_PROPORTIONAL
    assert status["updates"] == 1
    assert status["proportional_gain"] == 0.2
    assert status["tension_threshold"] == 2.0
    assert status["compression_threshold"] == 3.0
    assert status["last_factor"] > 0.0


def test_isolates_exceptions_and_keeps_last_factor():
    sc = SyncController(config={"sync_mode": SYNC_TWO_LEVEL})
    sc.update(100.0, 10.0)  # 0.5
    assert sc.update("x", "y") == 0.5  # entrada no numerica no propaga


def test_build_sync_controller_reads_config_and_feedback():
    feedback = object()
    sc = build_sync_controller(
        config={"sync_mode": SYNC_PROPORTIONAL}, feedback=feedback
    )
    assert isinstance(sc, SyncController)
    assert sc.feedback is feedback
    assert sc.get_status()["mode"] == SYNC_PROPORTIONAL
