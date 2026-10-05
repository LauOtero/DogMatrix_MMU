"""Pruebas de calibracion real, endstop virtual y modos de FlowGuard."""

from __future__ import annotations

from types import SimpleNamespace

from dog_matrix.calibration import MAX_DEVIATION, Calibrator
from dog_matrix.encoder import Encoder
from dog_matrix.flowguard import (
    ENCODER_MODE_FLOWGUARD,
    ENCODER_MODE_OFF,
    ENCODER_MODE_TANGLE,
    FLOW_CLOG,
    FLOW_OK,
    FLOW_RUNOUT,
    FLOW_TANGLE,
    FlowGuard,
)


class _FakeMotion:
    """Motion que aplica una relacion medida/solicitada al encoder."""

    def __init__(self, encoder: Encoder, ratio: float = 1.0) -> None:
        self.encoder = encoder
        self.ratio = float(ratio)
        self.calls = 0

    def load_filament(self, distance_mm: float, speed_mm_s: float) -> bool:
        self.calls += 1
        start = self.encoder.read_position()
        self.encoder.set_simulated_position(start + abs(distance_mm) * self.ratio)
        return True


def _encoder() -> Encoder:
    return Encoder(None, {"encoder_filter_alpha": 1.0, "encoder_resolution": 0.5})


# --- Encoder: endstop virtual y correccion de bowden ----------------------
def test_encoder_virtual_endstop():
    encoder = _encoder()
    encoder.endstop_threshold_mm = 50.0
    encoder.set_simulated_position(10.0)
    assert encoder.virtual_endstop_triggered() is False
    encoder.set_simulated_position(60.0)
    assert encoder.virtual_endstop_triggered() is True


def test_encoder_move_validation():
    encoder = _encoder()
    encoder.expect_move()
    encoder.set_simulated_position(98.0)
    assert encoder.check_move(100.0) == 98.0
    assert encoder.move_validation(100.0, tolerance_mm=3.0) is True
    assert encoder.move_validation(100.0, tolerance_mm=1.0) is False


def test_encoder_bowden_correction():
    encoder = _encoder()
    encoder.set_bowden_correction(1.05)
    assert abs(encoder.apply_bowden_correction(100.0) - 105.0) < 1e-6
    encoder.set_bowden_correction(-1.0)
    assert encoder.bowden_correction > 0  # clampeado


def test_encoder_derive_resolution():
    encoder = _encoder()
    assert encoder.derive_resolution(100.0, 200) == 0.5
    assert encoder.derive_resolution(0.0, 200) is None


# --- Calibracion -----------------------------------------------------------
def test_calibrate_gear_ok_persists():
    encoder = _encoder()
    applied = []
    calib = Calibrator(encoder, _FakeMotion(encoder, ratio=0.98), on_apply=lambda k, r: applied.append((k, r)))
    result = calib.calibrate_gear(2, distance_mm=100.0)
    assert result.success
    assert abs(result.factor - 0.98) < 1e-6
    assert calib.rotation_distance[2] == result.factor
    assert applied and applied[0][0] == "gear"


def test_calibrate_gear_rejects_large_deviation():
    encoder = _encoder()
    calib = Calibrator(encoder, _FakeMotion(encoder, ratio=0.5))
    result = calib.calibrate_gear(0, distance_mm=100.0)
    assert not result.success
    assert result.error_code == "ERR_CALIBRATION_DEVIATION"
    assert result.factor == 0.5
    assert 0.5 < 1.0 - MAX_DEVIATION  # sanity del limite


def test_calibrate_encoder_sets_resolution():
    encoder = _encoder()
    calib = Calibrator(encoder, _FakeMotion(encoder))
    result = calib.calibrate_encoder(100.0, raw_counts=250)
    assert result.success
    assert abs(encoder.resolution_mm - 0.4) < 1e-9


def test_calibrate_encoder_without_counts_fails():
    encoder = _encoder()
    calib = Calibrator(encoder, _FakeMotion(encoder))
    result = calib.calibrate_encoder(100.0, raw_counts=0)
    assert not result.success
    assert result.error_code == "ERR_ENCODER_NO_COUNTS"


def test_calibrate_gates_all():
    encoder = _encoder()
    profile = SimpleNamespace(gates=4)
    calib = Calibrator(encoder, _FakeMotion(encoder, ratio=1.0), profile=profile)
    results = calib.calibrate_gates()
    assert len(results) == 4
    assert all(r.success for r in results)


# --- FlowGuard modos de encoder -------------------------------------------
def _guard(**limits):
    profile = SimpleNamespace(limits={"encoder_error_mm": 5.0})
    return FlowGuard(config=limits or None, profile=profile)


def _confirm(guard, requested, measured, n=3):
    result = None
    for _ in range(n):
        result = guard.evaluate(requested, measured)
    return result


def test_flowguard_mode_off_never_errors():
    guard = _guard(flowguard_encoder_mode=ENCODER_MODE_OFF)
    assert _confirm(guard, 100.0, 0.0).is_error is False


def test_flowguard_mode_tangle_only():
    guard = _guard(flowguard_encoder_mode=ENCODER_MODE_TANGLE)
    assert _confirm(guard, 100.0, 0.0).state == FLOW_OK  # runout no aplica
    guard2 = _guard(flowguard_encoder_mode=ENCODER_MODE_TANGLE)
    result = _confirm(guard2, 100.0, 20.0)
    assert result.state == FLOW_TANGLE
    assert result.is_error


def test_flowguard_mode_flowguard_excludes_tangle():
    guard = _guard(flowguard_encoder_mode=ENCODER_MODE_FLOWGUARD)
    result = _confirm(guard, 100.0, 20.0)  # tangle -> excluido
    assert result.state == FLOW_OK
    guard2 = _guard(flowguard_encoder_mode=ENCODER_MODE_FLOWGUARD)
    assert _confirm(guard2, 100.0, 40.0).state == FLOW_CLOG


def test_flowguard_max_clog_absolute_limit():
    guard = _guard(flowguard_max_clog=60.0)
    assert _confirm(guard, 100.0, 50.0).state == FLOW_OK  # diff=50 < 60
    guard2 = _guard(flowguard_max_clog=60.0)
    assert _confirm(guard2, 100.0, 35.0).state == FLOW_CLOG  # diff=65 >= 60

def test_flowguard_relief_after_tangle():
    guard = _guard(flowguard_max_relief=5.0)
    _confirm(guard, 100.0, 20.0)
    assert guard.request_relief() == 5.0
    assert guard.request_relief() == 0.0
