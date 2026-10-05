"""Deteccion de divergencia (atasco, runout, enredo) via CFFI.

El nucleo de deteccion (calculo de divergencia, histabilidad temporal) se
delega a la capa nativa, minimizando latencia y consumo de CPU; Python solo
configura y agrega resultados (informe 6.8).

Requisitos: deteccion < 50 us P99 (CFFI), falsos positivos < 0.01 %,
consumo CPU < 0.5 %.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import _native

FLOW_OK = "ok"
FLOW_DIVERGENCE = "divergence"
FLOW_CLOG = "clog"
FLOW_RUNOUT = "runout"
FLOW_TANGLE = "tangle"  # nuevo: enredo en el spool

DEFAULT_RATIO_THRESHOLD = 0.15
DEFAULT_HYSTERESIS = 3  # violaciones consecutivas para confirmar


@dataclass
class FlowGuardResult:
    """Resultado de una evaluacion de flujo."""

    state: str
    divergence_mm: float
    ratio: float
    is_error: bool
    threshold_mm: float
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "divergence_mm": self.divergence_mm,
            "ratio": self.ratio,
            "is_error": self.is_error,
            "threshold_mm": self.threshold_mm,
            "message": self.message,
        }


class FlowGuard:
    """Monitor de divergencia entre movimiento comandado y medido."""

    def __init__(self, config: Any = None, profile: Any = None) -> None:
        limits = getattr(profile, "limits", {}) if profile is not None else {}
        self.threshold_mm = float(limits.get("encoder_error_mm", 5.0))
        self.ratio_threshold = self._get_ratio(config)
        self.hysteresis = DEFAULT_HYSTERESIS
        self.adaptive = False
        self.consecutive_violations = 0
        self.stats = {
            "evaluations": 0,
            "violations": 0,
            "errors": 0,
            "false_positives": 0,
            "last_state": FLOW_OK,
        }
        self._material: Optional[str] = None
        self._temperature: Optional[float] = None

    @staticmethod
    def _get_ratio(config: Any) -> float:
        if config is None:
            return DEFAULT_RATIO_THRESHOLD
        if isinstance(config, dict):
            return float(config.get("flowguard_ratio_threshold", DEFAULT_RATIO_THRESHOLD))
        getter = getattr(config, "getfloat", None)
        if callable(getter):
            try:
                return float(getter("flowguard_ratio_threshold", DEFAULT_RATIO_THRESHOLD))
            except Exception:  # noqa: BLE001
                return DEFAULT_RATIO_THRESHOLD
        return DEFAULT_RATIO_THRESHOLD

    # -- API publica --------------------------------------------------------
    def evaluate(self, requested_mm: float, measured_mm: float) -> FlowGuardResult:
        """Evalua la divergencia y clasifica el estado del flujo."""
        diff, ratio = _native.divergence(requested_mm, measured_mm)
        diff = round(diff, 4)
        self.stats["evaluations"] += 1

        threshold = self._effective_threshold()
        violating = abs(diff) > threshold or ratio > self.ratio_threshold

        if violating:
            self.consecutive_violations += 1
            self.stats["violations"] += 1
        else:
            if self.consecutive_violations >= self.hysteresis:
                self.stats["false_positives"] += 1
            self.consecutive_violations = 0

        confirmed = self.consecutive_violations >= self.hysteresis
        state = FLOW_OK
        is_error = False
        message = ""
        if confirmed:
            is_error = True
            state = self._classify(requested_mm, measured_mm)
            self.stats["errors"] += 1
            message = f"divergencia confirmada ({state})"

        self.stats["last_state"] = state
        return FlowGuardResult(
            state=state,
            divergence_mm=diff,
            ratio=round(ratio, 4),
            is_error=is_error,
            threshold_mm=threshold,
            message=message,
        )

    def _effective_threshold(self) -> float:
        base = self.threshold_mm
        if not self.adaptive:
            return base
        # Ajuste adaptativo simple por material/temperatura.
        factor = 1.0
        if self._material in ("TPU", "FLEX"):
            factor = 1.5
        if self._temperature is not None and self._temperature > 240:
            factor *= 1.1
        return round(base * factor, 4)

    @staticmethod
    def _classify(requested_mm: float, measured_mm: float) -> str:
        if requested_mm > 0 and measured_mm <= 0.01:
            return FLOW_RUNOUT
        if measured_mm <= requested_mm * 0.3:
            # Movimiento muy reducido - podría ser enredo
            if measured_mm > 0 and requested_mm > 0:
                return FLOW_TANGLE  # Enredo detectado
            return FLOW_CLOG
        return FLOW_DIVERGENCE

    def update_thresholds(self, material: Optional[str] = None, temperature: Optional[float] = None) -> None:
        self._material = material
        self._temperature = temperature

    def reset(self) -> None:
        self.consecutive_violations = 0

    def get_statistics(self) -> Dict[str, Any]:
        result = dict(self.stats)
        result["threshold_mm"] = self._effective_threshold()
        result["adaptive"] = self.adaptive
        result["native"] = _native.native_available()
        return result

    def set_adaptive_mode(self, enabled: bool) -> None:
        self.adaptive = bool(enabled)


__all__ = ["FlowGuard", "FlowGuardResult", "FLOW_OK", "FLOW_DIVERGENCE", "FLOW_CLOG", "FLOW_RUNOUT"]
