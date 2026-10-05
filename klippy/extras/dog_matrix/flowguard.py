"""Deteccion de divergencia (atasco, runout, enredo) via CFFI.

El nucleo de deteccion (calculo de divergencia, histabilidad temporal) se
delega a la capa nativa, minimizando latencia y consumo de CPU; Python solo
configura y agrega resultados (informe 6.8).

Requisitos: deteccion < 50 us P99 (CFFI), falsos positivos < 0.01 %,
consumo CPU < 0.5 %.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import _native

FLOW_OK = "ok"
FLOW_DIVERGENCE = "divergence"
FLOW_CLOG = "clog"
FLOW_RUNOUT = "runout"
FLOW_TANGLE = "tangle"  # enredo en la bobina (movimiento muy reducido pero no nulo)

DEFAULT_RATIO_THRESHOLD = 0.15
DEFAULT_HYSTERESIS = 3  # violaciones consecutivas para confirmar
TANGLE_RATIO = 0.3  # por debajo de este ratio de movimiento se considera enredo
CLOG_RATIO = 0.5  # por debajo de este ratio (y por encima del de enredo) se considera atasco

# Modos de integracion con el encoder (paridad Happy Hare flowguard_encoder_mode).
ENCODER_MODE_OFF = "off"          # deshabilitado
ENCODER_MODE_AUTO = "auto"        # clasifica runout/tangle/clog/divergence
ENCODER_MODE_FLOWGUARD = "flowguard"  # solo divergencia de flujo (clog/divergence)
ENCODER_MODE_TANGLE = "tangle"    # solo proteccion de enredo


def _cfg_get(config: Any, key: str, default: Any) -> Any:
    """Lee un valor de un ConfigWrapper o de un dict de forma segura."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            return default
    return default


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
            "tangle_preventions": 0,
            "last_state": FLOW_OK,
        }
        self._material: Optional[str] = None
        self._temperature: Optional[float] = None
        # Multiplicadores de prevencion activa aplicados al detectar un enredo.
        self.speed_factor = 1.0
        self.gear_current_factor = 1.0
        # Modos de encoder y limites absolutos (mm) configurables.
        self.encoder_mode = str(_cfg_get(config, "flowguard_encoder_mode", ENCODER_MODE_AUTO)).lower()
        self.max_relief_mm = float(_cfg_get(config, "flowguard_max_relief", 0.0))
        self.max_clog_mm = float(_cfg_get(config, "flowguard_max_clog", 0.0))
        self.max_tangle_mm = float(_cfg_get(config, "flowguard_max_tangle", 0.0))
        self.relief_mm = 0.0

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
            candidate = self._classify(requested_mm, measured_mm)
            if self._allowed(candidate, abs(diff)):
                is_error = True
                state = candidate
                self.stats["errors"] += 1
                if state == FLOW_TANGLE:
                    self._trigger_tangle_prevention()
                    self.stats["tangle_preventions"] += 1
                    if self.max_relief_mm > 0:
                        self.relief_mm = min(
                            self.max_relief_mm, max(0.0, requested_mm - measured_mm)
                        )
                message = f"divergencia confirmada ({state})"
            elif self.consecutive_violations >= self.hysteresis:
                self.stats["false_positives"] += 1

        self.stats["last_state"] = state
        return FlowGuardResult(
            state=state,
            divergence_mm=diff,
            ratio=round(ratio, 4),
            is_error=is_error,
            threshold_mm=threshold,
            message=message,
        )

    def _allowed(self, state: str, diff_mm: float) -> bool:
        """Decide si un estado confirmado debe tratarse como error segun el modo."""
        mode = self.encoder_mode
        if mode == ENCODER_MODE_OFF:
            return False
        if mode == ENCODER_MODE_TANGLE and state != FLOW_TANGLE:
            return False
        if mode == ENCODER_MODE_FLOWGUARD and state == FLOW_TANGLE:
            return False
        # Limites absolutos opcionales (mm de divergencia minima).
        if state == FLOW_CLOG and self.max_clog_mm > 0 and diff_mm < self.max_clog_mm:
            return False
        if state == FLOW_TANGLE and self.max_tangle_mm > 0 and diff_mm < self.max_tangle_mm:
            return False
        return True

    def evaluate_encoder(self, requested_mm: float, measured_mm: float) -> FlowGuardResult:
        """Alias semantico de ``evaluate`` para el camino encoder->FlowGuard."""
        return self.evaluate(requested_mm, measured_mm)

    def set_encoder_mode(self, mode: str) -> None:
        self.encoder_mode = str(mode).lower()

    def request_relief(self) -> float:
        """Devuelve (y consume) la longitud de alivio pendiente tras un enredo."""
        relief = self.relief_mm
        self.relief_mm = 0.0
        return relief

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
        """Clasifica la divergencia confirmada.

        - ``runout``: no hay movimiento medido.
        - ``tangle``: movimiento muy reducido pero no nulo (enredo/tension).
        - ``clog``: deficit severo de movimiento (atasco).
        - ``divergence``: desviacion moderada.
        """
        if requested_mm > 0 and measured_mm <= 0.01:
            return FLOW_RUNOUT
        if requested_mm > 0 and measured_mm <= requested_mm * TANGLE_RATIO:
            return FLOW_TANGLE
        if requested_mm > 0 and measured_mm <= requested_mm * CLOG_RATIO:
            return FLOW_CLOG
        return FLOW_DIVERGENCE

    def _trigger_tangle_prevention(self) -> None:
        """Aplica la prevencion activa al detectar un enredo.

        Reduce el factor de velocidad de extrusion y aumenta el factor de
        corriente del motor de arrastre. El core/consumidores leen
        ``speed_factor`` y ``gear_current_factor`` para trasladarlo al hardware.
        """
        self.speed_factor = max(0.3, self.speed_factor * 0.7)
        self.gear_current_factor = min(1.5, self.gear_current_factor * 1.15)
        logging.getLogger("klippy.dog_matrix.flowguard").warning(
            "tangle prevention: speed_factor=%.2f gear_current_factor=%.2f",
            self.speed_factor,
            self.gear_current_factor,
        )

    def reset_prevention(self) -> None:
        """Restablece los multiplicadores de prevencion a su valor nominal."""
        self.speed_factor = 1.0
        self.gear_current_factor = 1.0

    def update_thresholds(self, material: Optional[str] = None,
                          temperature: Optional[float] = None) -> None:
        self._material = material
        self._temperature = temperature

    def reset(self) -> None:
        self.consecutive_violations = 0

    def get_statistics(self) -> Dict[str, Any]:
        result = dict(self.stats)
        result["threshold_mm"] = self._effective_threshold()
        result["adaptive"] = self.adaptive
        result["speed_factor"] = self.speed_factor
        result["gear_current_factor"] = self.gear_current_factor
        result["encoder_mode"] = self.encoder_mode
        result["relief_mm"] = self.relief_mm
        result["native"] = _native.native_available()
        return result

    def set_adaptive_mode(self, enabled: bool) -> None:
        self.adaptive = bool(enabled)


__all__ = ["FlowGuard", "FlowGuardResult", "FLOW_OK", "FLOW_DIVERGENCE",
           "FLOW_CLOG", "FLOW_RUNOUT", "FLOW_TANGLE",
           "ENCODER_MODE_OFF", "ENCODER_MODE_AUTO", "ENCODER_MODE_FLOWGUARD",
           "ENCODER_MODE_TANGLE"]
