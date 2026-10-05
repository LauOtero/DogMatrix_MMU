"""Calibracion real con medicion por encoder (paridad Happy Hare Calibration-*).

Cada rutina mueve una distancia conocida y compara el movimiento *medido* por
el encoder con el *solicitado* para derivar el factor de correccion de
``rotation_distance`` / ``bowden_length``. Los resultados se persisten por gate
mediante el callback inyectado ``on_apply``.

Determinismo y resiliencia: sin bloqueo (el movimiento lo emite ``Motion`` via
trapq/G-code), sin aleatoriedad, y toda desviacion superior al 20 % se rechaza
para no corromper la configuracion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

MAX_DEVIATION = 0.20  # 20 % de desviacion maxima admisible


@dataclass
class CalibrationResult:
    """Resultado de una rutina de calibracion."""

    success: bool
    kind: str
    gate: Optional[int] = None
    requested_mm: float = 0.0
    measured_mm: float = 0.0
    factor: float = 1.0
    message: str = ""
    error_code: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "kind": self.kind,
            "gate": self.gate,
            "requested_mm": round(self.requested_mm, 4),
            "measured_mm": round(self.measured_mm, 4),
            "factor": round(self.factor, 6),
            "message": self.message,
            "error_code": self.error_code,
        }


class Calibrator:
    """Rutinas de calibracion con medicion real (encoder)."""

    def __init__(
        self,
        encoder: Any,
        motion: Any,
        profile: Any = None,
        on_apply: Optional[Callable[[str, CalibrationResult], None]] = None,
    ) -> None:
        self.encoder = encoder
        self.motion = motion
        self.profile = profile
        self.on_apply = on_apply
        # Correcciones acumuladas por gate y por magnitud.
        self.rotation_distance: Dict[int, float] = {}
        self.bowden_length: Dict[int, float] = {}
        self.encoder_resolution: float = 0.0

    # -- Medicion base ------------------------------------------------------
    def _measure(self, requested_mm: float, speed_mm_s: float) -> float:
        """Ejecuta un movimiento y devuelve la distancia medida por el encoder."""
        start = self.encoder.read_position()
        self.motion.load_filament(abs(requested_mm), speed_mm_s)
        end = self.encoder.read_position()
        return abs(end - start)

    @staticmethod
    def _within_tolerance(measured: float, requested: float) -> bool:
        if requested <= 0:
            return False
        return abs(measured - requested) / requested <= MAX_DEVIATION

    # -- Rutinas ------------------------------------------------------------
    def calibrate_gear(
        self, gate: int, distance_mm: float = 100.0, speed_mm_s: float = 30.0
    ) -> CalibrationResult:
        """Deriva el factor de ``rotation_distance`` comparando medido vs pedido."""
        requested = abs(float(distance_mm))
        measured = self._measure(requested, speed_mm_s)
        factor = measured / requested if requested > 0 else 1.0
        if not self._within_tolerance(measured, requested):
            result = CalibrationResult(
                False, "gear", gate, requested, measured, factor,
                f"desviacion > {int(MAX_DEVIATION * 100)}% (medido {measured:.2f} vs {requested:.2f} mm)",
                "ERR_CALIBRATION_DEVIATION",
            )
        else:
            self.rotation_distance[gate] = factor
            result = CalibrationResult(
                True, "gear", gate, requested, measured, factor,
                f"factor rotation_distance={factor:.4f}",
            )
        self._apply("gear", result)
        return result

    def calibrate_encoder(
        self, distance_mm: float, raw_counts: Optional[int] = None
    ) -> CalibrationResult:
        """Calibra la resolucion del encoder (mm por pulso)."""
        requested = abs(float(distance_mm))
        counts = int(raw_counts) if raw_counts is not None else int(self.encoder.get_raw_counts())
        if counts <= 0:
            result = CalibrationResult(
                False, "encoder", None, requested, 0.0, 0.0,
                "sin pulsos de encoder (no se puede calibrar)", "ERR_ENCODER_NO_COUNTS",
            )
            self._apply("encoder", result)
            return result
        resolution = requested / counts
        self.encoder_resolution = resolution
        if hasattr(self.encoder, "resolution_mm"):
            try:
                self.encoder.resolution_mm = resolution
            except Exception:  # noqa: BLE001 - encoder opcional
                pass
        result = CalibrationResult(
            True, "encoder", None, requested, requested, resolution,
            f"resolucion={resolution:.5f} mm/pulso ({counts} pulsos)",
        )
        self._apply("encoder", result)
        return result

    def calibrate_bowden(self, gate: int, distance_mm: float = 600.0) -> CalibrationResult:
        """Deriva la longitud de bowden midiendo el movimiento hasta el toolhead."""
        requested = abs(float(distance_mm))
        measured = self._measure(requested, 60.0)
        if measured <= 0:
            result = CalibrationResult(
                False, "bowden", gate, requested, measured, 0.0,
                "sin movimiento medido", "ERR_CALIBRATION_NO_MOVEMENT",
            )
        else:
            self.bowden_length[gate] = round(measured, 3)
            result = CalibrationResult(
                True, "bowden", gate, requested, measured, measured / requested if requested else 1.0,
                f"bowden_length={measured:.1f} mm",
            )
        self._apply("bowden", result)
        return result

    def calibrate_toolhead(self, gate: int, distance_mm: float = 80.0) -> CalibrationResult:
        """Calibra la distancia hub -> toolhead."""
        requested = abs(float(distance_mm))
        measured = self._measure(requested, 20.0)
        ok = self._within_tolerance(measured, requested) and measured > 0
        result = CalibrationResult(
            ok, "toolhead", gate, requested, measured, measured / requested if requested else 1.0,
            "distancia toolhead medida" if ok else "desviacion de toolhead fuera de tolerancia",
            None if ok else "ERR_CALIBRATION_DEVIATION",
        )
        self._apply("toolhead", result)
        return result

    def calibrate_gates(self, gates: Optional[List[int]] = None) -> List[CalibrationResult]:
        """Calibra todos los gates indicados (o todos los del perfil)."""
        if gates is None:
            gates = list(range(getattr(self.profile, "gates", 0) or 0))
        return [self.calibrate_gear(int(gate)) for gate in gates]

    # -- Aplicacion ---------------------------------------------------------
    def _apply(self, kind: str, result: CalibrationResult) -> None:
        if self.on_apply is None:
            return
        try:
            self.on_apply(kind, result)
        except Exception:  # noqa: BLE001 - la persistencia nunca debe romper
            pass

    def get_status(self) -> Dict[str, Any]:
        return {
            "rotation_distance": {str(k): v for k, v in self.rotation_distance.items()},
            "bowden_length": {str(k): v for k, v in self.bowden_length.items()},
            "encoder_resolution": self.encoder_resolution,
            "max_deviation": MAX_DEVIATION,
        }


__all__ = ["Calibrator", "CalibrationResult", "MAX_DEVIATION"]
