"""Control de LEDs direccionables con efectos, segmentos y animaciones.

Mapea el estado de gates y de la FSM a colores/animaciones y los vuelca sobre
objetos NeoPixel de Klipper. Soporta efectos (solid, blink, breathing, rainbow)
y segmentos por gate, refrescados por el reactor a >= 20 Hz.

Diseno: el calculo de color es puro y determinista (indice + tick); el volcado
a hardware se aisla para que su ausencia nunca rompa el flujo.
"""

from __future__ import annotations

import colorsys
import time
from typing import Any, Dict, List, Optional, Tuple

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

# Efectos disponibles.
EFFECT_SOLID = "solid"
EFFECT_BLINK = "blink"
EFFECT_BREATHING = "breathing"
EFFECT_RAINBOW = "rainbow"
EFFECT_OFF = "off"

REFRESH_HZ = 20.0


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


class LEDSystem:
    """Gestiona la cadena LED del MMU con efectos y segmentos."""

    def __init__(self, printer: Any, config: Any = None, profile: Any = None) -> None:
        self.printer = printer
        self.config = config
        self.count = 0
        if profile is not None:
            led_cfg = profile.hardware.get("led", {}) if profile.hardware else {}
            self.count = int(led_cfg.get("count", 0))
        self.count = int(_cfg_get(config, "led_count", self.count))
        self.colors: Dict[int, Tuple[int, int, int]] = {}
        self.animation: Optional[str] = None
        self.animation_state: Optional[str] = None
        self.effect: str = EFFECT_SOLID
        self.effect_color: Tuple[int, int, int] = (0, 0, 64)
        self._base_colors: Dict[int, Tuple[int, int, int]] = {}
        self._tick = 0
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

    # -- Colores base -------------------------------------------------------
    def set_gate_status_color(self, gate: int, status: str) -> None:
        self._base_colors[gate] = GATE_COLORS.get(status, GATE_COLORS[GATE_UNKNOWN])

    def set_filament_color(self, gate: int, rgb_hex: str) -> None:
        self._base_colors[gate] = self._hex_to_rgb(rgb_hex)

    def set_led(self, index: int, rgb: Tuple[int, int, int]) -> None:
        """Fija el color directo de un LED concreto."""
        self._base_colors[index] = tuple(rgb)  # type: ignore[assignment]

    def set_segment(self, start: int, count: int, rgb: Tuple[int, int, int]) -> None:
        """Colorea un segmento contiguo de LEDs."""
        for index in range(start, start + count):
            self._base_colors[index] = tuple(rgb)  # type: ignore[assignment]

    # -- Efectos ------------------------------------------------------------
    def set_system_state_animation(self, state: str, animation_type: str = EFFECT_SOLID) -> None:
        self.animation_state = state
        base = STATE_COLORS.get(state, (0, 0, 64))
        self.effect = animation_type
        self.effect_color = base
        for index in range(self.count):
            self._base_colors[index] = base

    def set_effect(self, effect: str, rgb: Optional[Tuple[int, int, int]] = None) -> None:
        """Activa un efecto global (solid/blink/breathing/rainbow/off)."""
        self.effect = str(effect).lower()
        if rgb is not None:
            self.effect_color = tuple(rgb)  # type: ignore[assignment]

    def clear_all(self) -> None:
        self.colors.clear()
        self._base_colors.clear()
        self.animation = None
        self.effect = EFFECT_OFF
        for index in range(self.count):
            self.colors[index] = (0, 0, 0)
        self._flush()

    # -- Animacion ----------------------------------------------------------
    def frame_color(self, index: int, tick: int) -> Tuple[int, int, int]:
        """Color deterministico de un LED para un tick dado (funcion pura)."""
        effect = self.effect
        if effect == EFFECT_OFF:
            return (0, 0, 0)
        if effect == EFFECT_RAINBOW:
            hue = ((tick * 3) + (index * 360 / max(1, self.count))) % 360 / 360.0
            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            return (int(r * 255), int(g * 255), int(b * 255))
        base = self._base_colors.get(index, self.colors.get(index, self.effect_color))
        if effect == EFFECT_BLINK:
            return base if (tick // 5) % 2 == 0 else (0, 0, 0)
        if effect == EFFECT_BREATHING:
            scale = 0.15 + 0.85 * (abs((tick % 40) - 20) / 20.0)
            return (int(base[0] * scale), int(base[1] * scale), int(base[2] * scale))
        return base

    def update(self, eventtime: float) -> float:
        """Refresca la cadena LED. Devuelve el instante del proximo refresco."""
        now = eventtime if eventtime else time.monotonic()
        period = 1.0 / REFRESH_HZ
        if (now - self._last_update) >= period:
            self._last_update = now
            self._tick += 1
            for index in range(self.count):
                self.colors[index] = self.frame_color(index, self._tick)
            self._flush()
        return now + period

    # -- Estado -------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "colors": {index: "#%02x%02x%02x" % rgb for index, rgb in self.colors.items()},
            "animation": self.animation,
            "effect": self.effect,
            "state": self.animation_state,
        }

    # -- Utilidades ---------------------------------------------------------
    def _flush(self) -> None:
        if self._neopixel is None:
            return
        led_helper = getattr(self._neopixel, "led_helper", None)
        if led_helper is None:
            return
        try:
            for index in range(self.count):
                led_helper.set_color(index, self.colors.get(index, (0, 0, 0)))
            show = getattr(led_helper, "show", None)
            if callable(show):
                show()
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


__all__ = ["LEDSystem", "GATE_COLORS", "STATE_COLORS", "REFRESH_HZ",
           "EFFECT_SOLID", "EFFECT_BLINK", "EFFECT_BREATHING", "EFFECT_RAINBOW",
           "EFFECT_OFF"]
