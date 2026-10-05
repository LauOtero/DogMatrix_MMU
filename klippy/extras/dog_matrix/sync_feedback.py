"""Buffer de sincronizacion por realimentacion (sync-feedback buffer).

Equivalente a la Feature-Sync-Feedback-Buffer de Happy Hare: clasifica el
estado del buffer de tension/compresion (neutro, comprimido, expandido),
permite autotune de la ``rotation_distance`` y expone un *endstop virtual*
para el homing.

Determinismo estricto: todas las transiciones son funciones puras de
(estado, entradas); no hay ``sleep``, aleatoriedad ni estado oculto. Las
excepciones se aislan para no propagarlas desde callbacks opcionales.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

SF_DISABLED = "disabled"
SF_NEUTRAL = "neutral"
SF_COMPRESSED = "compressed"
SF_EXPANDED = "expanded"

DEFAULT_TENSION_THRESHOLD = 0.15
DEFAULT_COMPRESSION_THRESHOLD = 0.15
DEFAULT_ROTATION_DISTANCE = 22.6789511
DEFAULT_AUTOTUNE_SAMPLES = 20


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
class SyncFeedbackStatus:
    """Estado instantaneo del buffer de sincronizacion."""

    state: str
    position_mm: float
    velocity_mm_s: float
    compression_ratio: float
    rotation_distance: float
    autotune_active: bool
    autotune_samples: int
    samples: int
    last_error_mm: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "position_mm": self.position_mm,
            "velocity_mm_s": self.velocity_mm_s,
            "compression_ratio": self.compression_ratio,
            "rotation_distance": self.rotation_distance,
            "autotune_active": self.autotune_active,
            "autotune_samples": self.autotune_samples,
            "samples": self.samples,
            "last_error_mm": self.last_error_mm,
        }


class SyncFeedback:
    """Monitor del buffer de tension/compresion y endstop virtual."""

    def __init__(self, config: Any = None, profile: Any = None) -> None:
        self.profile = profile
        self.enabled = bool(_cfg_get(config, "sync_feedback_enabled", False))
        self.tension_threshold = float(
            _cfg_get(config, "sync_feedback_tension_threshold", DEFAULT_TENSION_THRESHOLD)
        )
        self.compression_threshold = float(
            _cfg_get(config, "sync_feedback_compression_threshold", DEFAULT_COMPRESSION_THRESHOLD)
        )
        self.rotation_distance = float(
            _cfg_get(config, "sync_feedback_rotation_distance", DEFAULT_ROTATION_DISTANCE)
        )
        self.autotune_samples_target = int(
            _cfg_get(config, "sync_feedback_autotune_samples", DEFAULT_AUTOTUNE_SAMPLES)
        )
        self.state = SF_NEUTRAL if self.enabled else SF_DISABLED
        self.position_mm = 0.0
        self.velocity_mm_s = 0.0
        self.compression_ratio = 0.0
        self.last_error_mm = 0.0
        self.samples = 0
        self._autotune_active = bool(_cfg_get(config, "sync_feedback_autotune", False))
        self._autotune_samples = 0
        self._autotune_ratios: List[float] = []
        # Buffer analogico (ADC-compat).
        self.adc_compressed = float(_cfg_get(config, "sync_feedback_adc_compressed", 0.35))
        self.adc_expanded = float(_cfg_get(config, "sync_feedback_adc_expanded", 0.65))
        self.last_adc = 0.0

    # -- Evaluacion ---------------------------------------------------------
    def evaluate(
        self,
        requested_mm: float,
        measured_mm: float,
        tension: bool = False,
        compression: bool = False,
    ) -> str:
        """Actualiza el estado del buffer y devuelve el estado (``SF_*``)."""
        try:
            self.samples += 1
            self.position_mm = float(measured_mm)
            self.last_error_mm = float(requested_mm) - float(measured_mm)
            if not self.enabled:
                state = SF_DISABLED
            elif compression:
                state = SF_COMPRESSED
            elif tension:
                state = SF_EXPANDED
            elif requested_mm > 0:
                ratio = (requested_mm - measured_mm) / requested_mm
                self.compression_ratio = ratio
                if ratio >= self.compression_threshold:
                    state = SF_COMPRESSED
                elif ratio <= -self.tension_threshold:
                    state = SF_EXPANDED
                else:
                    state = SF_NEUTRAL
            else:
                state = SF_NEUTRAL
            self.state = state
        except Exception:  # noqa: BLE001 - nunca romper el flujo del llamante
            return self.state
        return self.state

    def speed_factor(self) -> float:
        """Factor de velocidad sugerido segun el estado del buffer."""
        if self.state == SF_COMPRESSED:
            return 0.5
        if self.state == SF_EXPANDED:
            return 1.5
        return 1.0

    # -- Autotune -----------------------------------------------------------
    def start_autotune(self) -> None:
        self._autotune_active = True
        self._autotune_samples = 0
        self._autotune_ratios = []

    def stop_autotune(self) -> None:
        self._autotune_active = False

    def autotune_step(self, requested_mm: float, measured_mm: float) -> Optional[float]:
        """Acumula muestras y ajusta la ``rotation_distance`` al converger.

        Ignora solicitudes <= 0 (evita divisiones por cero). Devuelve la nueva
        rotation_distance cuando completa el objetivo de muestras; si no, None.
        """
        try:
            if not self._autotune_active or requested_mm <= 0:
                return None
            self._autotune_ratios.append(measured_mm / requested_mm)
            self._autotune_samples = len(self._autotune_ratios)
            if self._autotune_samples >= self.autotune_samples_target:
                factor = sum(self._autotune_ratios) / len(self._autotune_ratios)
                self.rotation_distance *= factor
                self._autotune_active = False
                return self.rotation_distance
        except Exception:  # noqa: BLE001 - autocorreccion opcional
            return None
        return None

    # -- Calibracion y endstop ---------------------------------------------
    def calibrate_psensor(self, min_mm: float, max_mm: float) -> Dict[str, Any]:
        """Valida y describe el rango del sensor de posicion."""
        min_mm = float(min_mm)
        max_mm = float(max_mm)
        return {
            "min_mm": min_mm,
            "max_mm": max_mm,
            "span_mm": max_mm - min_mm,
            "ok": max_mm > min_mm,
        }

    def virtual_endstop_triggered(self, measured_mm: float, threshold_mm: float) -> bool:
        """Endstop virtual de homing: True si se alcanza el umbral."""
        return measured_mm >= threshold_mm

    # -- Buffer analogico (ADC) --------------------------------------------
    def evaluate_adc(self, adc_value: float, requested_mm: float = 0.0) -> str:
        """Clasifica el buffer a partir de una lectura ADC (0.0-1.0).

        Paridad Happy Hare ADC-compat buffer: por encima de ``adc_expanded`` se
        considera expandido (tension) y por debajo de ``adc_compressed``,
        comprimido; en medio, neutro. Determinista y con excepciones aisladas.
        """
        try:
            self.last_adc = float(adc_value)
            if not self.enabled:
                return self.state
            if self.last_adc <= self.adc_compressed:
                state = SF_COMPRESSED
            elif self.last_adc >= self.adc_expanded:
                state = SF_EXPANDED
            else:
                state = SF_NEUTRAL
            self.state = state
        except Exception:  # noqa: BLE001
            return self.state
        return self.state

    def get_status(self) -> Dict[str, Any]:
        data = self._status().as_dict()
        data["enabled"] = self.enabled
        data["speed_factor"] = self.speed_factor()
        data["thresholds"] = {
            "tension": self.tension_threshold,
            "compression": self.compression_threshold,
        }
        data["adc"] = {
            "value": self.last_adc,
            "compressed": self.adc_compressed,
            "expanded": self.adc_expanded,
        }
        return data

    # -- Estado -------------------------------------------------------------
    def _status(self) -> SyncFeedbackStatus:
        return SyncFeedbackStatus(
            state=self.state,
            position_mm=self.position_mm,
            velocity_mm_s=self.velocity_mm_s,
            compression_ratio=self.compression_ratio,
            rotation_distance=self.rotation_distance,
            autotune_active=self._autotune_active,
            autotune_samples=self._autotune_samples,
            samples=self.samples,
            last_error_mm=self.last_error_mm,
        )


__all__ = ["SyncFeedback", "SyncFeedbackStatus", "SF_DISABLED", "SF_NEUTRAL",
           "SF_COMPRESSED", "SF_EXPANDED"]
