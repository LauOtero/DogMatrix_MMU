"""Control de indicadores LED direccionables para senalizacion de estado.

Mapea el estado de gates y de la FSM a colores/animaciones y los vuelca sobre
objetos NeoPixel de Klipper (informe 6.18). La tasa de refresco se controla
mediante ``get_status``/``update``.

Requisitos: refresco >= 20 Hz para animaciones fluidas, bajo impacto en bus MCU.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

# Estados de gate reconocidos.
GATE_UNKNOWN = "unknown"
GATE_AVAILABLE = "available"
GATE_EMPTY = "empty"
GATE_ERROR = "error"

# Paleta (R, G, B) por estado de gate.
GATE_COLORS: Dict[str, Tuple[int, int, int]] = {
    GATE_UNKNOWN: (32, 32, 32),
    GATE_AVAILABLE: (0, 128, 0),
    GATE_EMPTY: (0, 0, 0),
    GATE_ERROR: (200, 0, 0),
}

# Paleta por estado de la FSM.
STATE_COLORS: Dict[str, Tuple[int, int, int]] = {
    "IDLE": (0, 0, 64),
    "LOAD": (0, 96, 96),
    "UNLOAD": (96, 64, 0),
    "SELECT": (64, 0, 96),
    "PURGE": (96, 96, 0),
    "COMPLETED": (0, 128, 0),
    "FAILED": (200, 0, 0),
    "RECOVERING": (128, 96, 0),
}

REFRESH_HZ = 20.0


class LEDSystem:
    """Gestiona la cadena LED del MMU."""

    def __init__(self, printer: Any, config: Any, profile: Any = None) -> None:
        self.printer = printer
        self.config = config
        self.count = 0
        if profile is not None:
            led_cfg = profile.hardware.get("led", {}) if profile.hardware else {}
            self.count = int(led_cfg.get("count", 0))
        self.colors: Dict[int, Tuple[int, int, int]] = {}
        self.animation: Optional[str] = None
        self.animation_state: Optional[str] = None
        self._phase = 0
        self._last_update = 0.0
        self._neopixel = self._lookup_neopixel()

    def _lookup_neopixel(self) -> Any:
        if self.printer is None:
            return None
        for name in ("neopixel dog_matrix", "led dog_matrix"):
            try:
                obj = self.printer.lookup_object(name, None)
                if obj is not None:
                    return obj
            except Exception:  # noqa: BLE001
                continue
        return None

    # -- API publica --------------------------------------------------------
    def set_gate_status_color(self, gate: int, status: str) -> None:
        self.colors[gate] = GATE_COLORS.get(status, GATE_COLORS[GATE_UNKNOWN])

    def set_filament_color(self, gate: int, rgb_hex: str) -> None:
        self.colors[gate] = self._hex_to_rgb(rgb_hex)

    def set_system_state_animation(self, state: str, animation_type: str = "solid") -> None:
        self.animation_state = state
        self.animation = animation_type
        base = STATE_COLORS.get(state, (0, 0, 64))
        for index in range(self.count):
            self.colors[index] = base

    def clear_all(self) -> None:
        self.colors.clear()
        self.animation = None
        for index in range(self.count):
            self.colors[index] = (0, 0, 0)
        self._flush()

    def update(self, eventtime: float) -> float:
        """Refresca la cadena LED. Devuelve el instante del proximo refresco."""
        now = eventtime if eventtime else time.monotonic()
        period = 1.0 / REFRESH_HZ
        if (now - self._last_update) >= period:
            self._last_update = now
            self._phase = (self._phase + 1) % self.count if self.count else 0
            self._flush()
        return now + period

    def get_status(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "colors": {index: "#%02x%02x%02x" % rgb for index, rgb in self.colors.items()},
            "animation": self.animation,
            "state": self.animation_state,
        }

    # -- Utilidades ---------------------------------------------------------
    def _flush(self) -> None:
        if self._neopixel is None:
            return
        try:
            self._neopixel.led_helper.set_color(self._phase, self.colors.get(self._phase, (0, 0, 0)))
        except Exception:  # noqa: BLE001 - hardware no disponible
            return

    @staticmethod
    def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
        token = value.strip().lstrip("#")
        if len(token) != 6:
            return (0, 0, 0)
        try:
            return (int(token[0:2], 16), int(token[2:4], 16), int(token[4:6], 16))
        except ValueError:
            return (0, 0, 0)


__all__ = ["LEDSystem", "GATE_COLORS", "STATE_COLORS", "REFRESH_HZ"]
