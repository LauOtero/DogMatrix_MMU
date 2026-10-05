"""Pruebas de la formacion de punta y corte (tip forming)."""

from __future__ import annotations

from dog_matrix.tip_forming import (
    STATE_IDLE,
    TipFormer,
)


def test_run_completes_and_emits_scripts():
    former = TipFormer()
    result = former.run()
    assert result.success
    assert former.state == STATE_IDLE
    assert any(script.startswith("G1 E") for script in former.scripts)
    # ramming (1) + cooling (3) + skinnydip (1) = 5 movimientos
    assert len([s for s in former.scripts if s.startswith("G1 E")]) == 5


def test_step_sequence_manual():
    former = TipFormer()
    former.start()
    assert former.state == "ramming"
    former.step_ramming()
    assert former.state == "cooling"
    for index in range(3):
        former.step_cooling(index)
    assert former.state == "skinnydip"
    result = former.step_skinnydip()
    assert result.success


def test_cutter_servo_invoked():
    calls = []
    former = TipFormer(
        config={"tip_use_cutter": True, "cutter_servo": "svc"},
        servo=lambda angle: calls.append(angle),
    )
    # Al usar servo inyectado, no se emite SET_SERVO pero si se invoca.
    former.run()
    assert calls == [90.0]
    assert not any("SET_SERVO" in script for script in former.scripts)


def test_cutter_emits_servo_when_no_callback():
    former = TipFormer(config={"tip_use_cutter": True, "cutter_servo": "svc"})
    former.run()
    assert any(script.startswith("SET_SERVO") for script in former.scripts)


def test_abort_resets_state():
    former = TipFormer()
    former.start()
    result = former.abort("test")
    assert result.success is False
    assert former.state == STATE_IDLE
    assert former.errors == 1


def test_reentrant_start_rejected():
    former = TipFormer()
    former.start()
    result = former.start()
    assert result.success is False
    assert "progreso" in result.message
