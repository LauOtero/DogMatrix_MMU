"""Monitorizacion del extrusor principal (posicion y consumo).

Equivalente al ``mmu_extruder_monitor`` de Happy Hare: registra la posicion
del extrusor, calcula el movimiento entre muestras y detecta situaciones
anomalas mientras se solicita extrusion:

- ``slipping``: se pide movimiento pero la posicion apenas cambia.
- ``runout``: la posicion no avanza de forma repetida pese a la peticion.
- ``ok``: el extrusor responde (o no se ha solicitado movimiento).

Determinismo estricto: el estado es una funcion pura de (estado, entradas);
no hay ``sleep``, aleatoriedad ni E/S. Las excepciones se aislan para no
propagarlas desde callbacks opcionales.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

EXTRUDER_OK = "ok"
EXTRUDER_RUNOUT = "runout"
EXTRUDER_SLIPPING = "slipping"

DEFAULT_MIN_MOVE_MM = 0.5
DEFAULT_RUNOUT_STALLS = 2


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


class ExtruderMonitor:
    """Vigila la posicion del extrusor y clasifica su estado."""

    def __init__(self, printer: Any = None, config: Any = None) -> None:
        self.printer = printer
        self.enabled = bool(_cfg_get(config, "extruder_monitor_enabled", False))
        self.min_move_mm = float(
            _cfg_get(config, "extruder_monitor_min_move_mm", DEFAULT_MIN_MOVE_MM)
        )
        self.runout_stalls = DEFAULT_RUNOUT_STALLS
        self.last_position_mm: Optional[float] = None
        self.moves = 0
        self.state = EXTRUDER_OK
        self._last_position: Optional[float] = None
        self._stall_count = 0

    # -- Evaluacion ---------------------------------------------------------
    def update(self, extruder_position_mm: float, expected_mm: float = 0.0) -> str:
        """Registra una muestra y devuelve el estado (``EXTRUDER_*``)."""
        try:
            if not self.enabled:
                self.state = EXTRUDER_OK
                return self.state
            position = float(extruder_position_mm)
            expected = float(expected_mm)
            self.moves += 1
            if self._last_position is None:
                self._register(position)
                self.state = EXTRUDER_OK
                return self.state
            movement = abs(position - self._last_position)
            self._register(position)
            if expected > 0.0 and movement < self.min_move_mm:
                self._stall_count += 1
                if self._stall_count >= self.runout_stalls:
                    self.state = EXTRUDER_RUNOUT
                else:
                    self.state = EXTRUDER_SLIPPING
            else:
                self._stall_count = 0
                self.state = EXTRUDER_OK
        except Exception:  # noqa: BLE001 - nunca romper el flujo del llamante
            return self.state
        return self.state

    def _register(self, position: float) -> None:
        self._last_position = position
        self.last_position_mm = position

    # -- Estado -------------------------------------------------------------
    def reset(self) -> None:
        """Descarta el historial de posicion y vuelve al estado neutro."""
        self.last_position_mm = None
        self._last_position = None
        self.moves = 0
        self.state = EXTRUDER_OK
        self._stall_count = 0

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "state": self.state,
            "last_position_mm": self.last_position_mm,
            "moves": self.moves,
            "min_move_mm": self.min_move_mm,
            "stall_count": self._stall_count,
        }


def build_extruder_monitor(printer: Any = None, config: Any = None) -> ExtruderMonitor:
    """Fabrica un :class:`ExtruderMonitor` a partir de printer/configuracion."""
    return ExtruderMonitor(printer=printer, config=config)


__all__ = [
    "ExtruderMonitor",
    "build_extruder_monitor",
    "EXTRUDER_OK",
    "EXTRUDER_RUNOUT",
    "EXTRUDER_SLIPPING",
]
