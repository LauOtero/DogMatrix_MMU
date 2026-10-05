"""Medicion del movimiento real del filamento (hibrido Python/CFFI).

Las operaciones criticas (filtrado IIR, calculo de velocidad) usan la capa
nativa para determinismo, exponiendo una interfaz Python estable (informe 6.7).

Requisitos: precision +-0.1 mm, latencia de lectura < 100 us (CFFI),
jitter < 10 us, resolucion configurable hasta 0.01 mm.
"""

from __future__ import annotations

import time
from typing import Any

from . import _native

DEFAULT_RESOLUTION_MM = 0.45
DEFAULT_FILTER_ALPHA = 0.35


class Encoder:
    """Encoder incremental de filamento."""

    def __init__(self, printer: Any, config: Any) -> None:
        self.printer = printer
        self.config = config
        self.resolution_mm = self._get_float(config, "encoder_resolution", DEFAULT_RESOLUTION_MM)
        self.filter_alpha = self._get_float(config, "encoder_filter_alpha", DEFAULT_FILTER_ALPHA)
        self._raw_counts = 0
        self._filtered_position = 0.0
        self._last_time = time.monotonic()
        self._last_position = 0.0
        self._velocity = 0.0
        self.reads = 0

    @staticmethod
    def _get_float(config: Any, key: str, default: float) -> float:
        if config is None:
            return default
        if isinstance(config, dict):
            return float(config.get(key, default))
        getter = getattr(config, "getfloat", None)
        if callable(getter):
            try:
                return float(getter(key, default))
            except Exception:  # noqa: BLE001
                return default
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                return float(getter(key, default))
            except Exception:  # noqa: BLE001
                return default
        return default

    # -- Simulacion ---------------------------------------------------------
    def set_simulated_position(self, position_mm: float) -> None:
        """Fija la posicion medida (modo simulacion / test)."""
        self._filtered_position = float(position_mm)
        self._raw_counts = int(round(position_mm / self.resolution_mm))

    def add_counts(self, counts: int) -> None:
        self._raw_counts += int(counts)

    # -- API publica --------------------------------------------------------
    def read_position(self) -> float:
        """Posicion filtrada en mm."""
        self.reads += 1
        instant = self._raw_counts * self.resolution_mm
        self._filtered_position = _native.iir_step(self._filtered_position, instant, self.filter_alpha)
        now = time.monotonic()
        delta_t = now - self._last_time
        if delta_t > 0:
            self._velocity = _native.iir_step(
                self._velocity, (self._filtered_position - self._last_position) / delta_t, self.filter_alpha
            )
        self._last_time = now
        self._last_position = self._filtered_position
        return round(self._filtered_position, 4)

    def read_velocity(self) -> float:
        """Velocidad filtrada en mm/s."""
        self.read_position()
        return round(self._velocity, 4)

    def reset(self) -> None:
        self._raw_counts = 0
        self._filtered_position = 0.0
        self._last_position = 0.0
        self._velocity = 0.0
        self._last_time = time.monotonic()

    def get_error_mm(self, requested_mm: float) -> float:
        """Diferencia entre movimiento solicitado y medido (mm)."""
        return round(requested_mm - self.read_position(), 4)

    def get_raw_counts(self) -> int:
        return self._raw_counts

    def set_filter_alpha(self, alpha: float) -> None:
        self.filter_alpha = min(1.0, max(0.0, float(alpha)))

    def get_status(self) -> dict:
        return {
            "position_mm": round(self._filtered_position, 4),
            "velocity_mm_s": round(self._velocity, 4),
            "raw_counts": self._raw_counts,
            "resolution_mm": self.resolution_mm,
            "filter_alpha": self.filter_alpha,
            "native": _native.native_available(),
        }


__all__ = ["Encoder", "DEFAULT_RESOLUTION_MM", "DEFAULT_FILTER_ALPHA"]
