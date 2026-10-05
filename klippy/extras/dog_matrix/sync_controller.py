"""Sincronizacion del motor de gear con el movimiento del extrusor.

Equivalente al ``mmu_sync_controller`` de Happy Hare: a partir de la peticion
del extrusor y de la medida realimentada por el buffer calcula un factor de
velocidad (>0) para el motor de gear. Se admiten tres modos: ``off`` (sin
correccion), ``proportional`` (correccion continua con ganancia) y
``two_level`` (correccion discreta por umbrales de tension/compresion).

Determinismo estricto: el factor es una funcion pura de las entradas y la
configuracion; no hay ``sleep``, aleatoriedad ni estado oculto. Las
excepciones se aislan para no romper el flujo del llamante.
"""

from __future__ import annotations

from typing import Any, Dict

SYNC_OFF = "off"
SYNC_PROPORTIONAL = "proportional"
SYNC_TWO_LEVEL = "two_level"

DEFAULT_PROPORTIONAL_GAIN = 0.1
DEFAULT_TENSION_THRESHOLD = 1.0
DEFAULT_COMPRESSION_THRESHOLD = 1.0

MIN_FACTOR = 0.5
MAX_FACTOR = 1.5


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


def _clamp(value: float, low: float = MIN_FACTOR, high: float = MAX_FACTOR) -> float:
    """Limita ``value`` al rango [low, high]."""
    return max(low, min(high, value))


class SyncController:
    """Calcula el factor de velocidad del gear segun la realimentacion."""

    def __init__(self, sync_feedback: Any = None, config: Any = None) -> None:
        self.feedback = sync_feedback
        self.mode = str(_cfg_get(config, "sync_mode", SYNC_OFF))
        self.proportional_gain = float(
            _cfg_get(config, "sync_proportional_gain", DEFAULT_PROPORTIONAL_GAIN)
        )
        self.tension_threshold = float(
            _cfg_get(config, "sync_tension_threshold", DEFAULT_TENSION_THRESHOLD)
        )
        self.compression_threshold = float(
            _cfg_get(config, "sync_compression_threshold", DEFAULT_COMPRESSION_THRESHOLD)
        )
        self.last_factor = 1.0
        self.updates = 0

    # -- Evaluacion ---------------------------------------------------------
    def update(self, requested_mm: float, measured_mm: float) -> float:
        """Devuelve el factor de velocidad del gear para esta muestra (>0)."""
        try:
            requested = float(requested_mm)
            measured = float(measured_mm)
            if requested <= 0.0:
                return 1.0
            factor = self._compute(requested, measured)
            self.last_factor = factor
            self.updates += 1
        except Exception:  # noqa: BLE001 - nunca romper el flujo del llamante
            return self.last_factor
        return self.last_factor

    def _compute(self, requested: float, measured: float) -> float:
        if self.mode == SYNC_PROPORTIONAL:
            error = (requested - measured) / requested
            return _clamp(1.0 + self.proportional_gain * error)
        if self.mode == SYNC_TWO_LEVEL:
            if measured < requested - self.compression_threshold:
                return MIN_FACTOR
            if measured > requested + self.tension_threshold:
                return MAX_FACTOR
            return 1.0
        return 1.0

    # -- Estado -------------------------------------------------------------
    def reset(self) -> None:
        """Reinicia contadores y el ultimo factor a neutro."""
        self.last_factor = 1.0
        self.updates = 0

    def get_status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "last_factor": self.last_factor,
            "updates": self.updates,
            "proportional_gain": self.proportional_gain,
            "tension_threshold": self.tension_threshold,
            "compression_threshold": self.compression_threshold,
        }


def build_sync_controller(config: Any = None, feedback: Any = None) -> SyncController:
    """Fabrica un :class:`SyncController` a partir de configuracion/feedback."""
    return SyncController(sync_feedback=feedback, config=config)


__all__ = [
    "SyncController",
    "build_sync_controller",
    "SYNC_OFF",
    "SYNC_PROPORTIONAL",
    "SYNC_TWO_LEVEL",
]
