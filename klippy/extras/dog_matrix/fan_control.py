"""Control de ventiladores de la MMU con histeresis, dirigido por reactor.

Sustituye el control manual por conmutacion ON/OFF sin *chatter* alrededor de
dos umbrales (``on_temp`` / ``off_temp``). Fuente de temperatura inyectable
(sensor de entorno o de CPU del MCU).

Convenciones Klipper:
- No bloquea: ``tick(eventtime)`` se registra con ``reactor.register_timer`` y
  devuelve el proximo instante (o ``reactor.NEVER`` si no aplica).
- El actuador se inyecta como ``set_fan(power)`` (p. ej. ``[fan_generic]``),
  de modo que el modulo es testeable sin hardware.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

FORCED_OFF = 0
FORCED_AUTO = 1
FORCED_ON = 2


class FanController:
    """Conmutacion con histeresis (determinista y sin bloqueo)."""

    def __init__(
        self,
        set_fan: Callable[[float], None],
        on_temp: float = 45.0,
        off_temp: float = 40.0,
        poll_s: float = 1.0,
        forced: int = FORCED_AUTO,
        name: str = "mmu_fan",
    ) -> None:
        if on_temp < off_temp:
            raise ValueError("on_temp debe ser >= off_temp (histéresis)")
        self._set_fan = set_fan
        self.on_temp = float(on_temp)
        self.off_temp = float(off_temp)
        self.poll_s = max(0.1, float(poll_s))
        self.forced = int(forced)
        self.name = name
        self.state = False  # False = off, True = on
        self.last_temp = 0.0
        self._enabled = True

    # -- Configuracion ------------------------------------------------------
    def set_forced(self, forced: int) -> None:
        self.forced = int(forced)
        if self.forced == FORCED_ON and not self.state:
            self._apply(True)
        elif self.forced == FORCED_OFF and self.state:
            self._apply(False)

    def enable(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled and self.state:
            self._apply(False)

    # -- Evaluacion ---------------------------------------------------------
    def update(self, temp: float) -> bool:
        """Evalua el estado del ventilador para ``temp`` y lo aplica."""
        self.last_temp = float(temp)
        if not self._enabled:
            target = False
        elif self.forced == FORCED_ON:
            target = True
        elif self.forced == FORCED_OFF:
            target = False
        elif self.state:
            target = temp > self.off_temp  # apaga solo al bajar del umbral OFF
        else:
            target = temp >= self.on_temp  # enciende al superar el umbral ON
        if target != self.state:
            self._apply(target)
        return self.state

    def _apply(self, on: bool) -> None:
        self.state = bool(on)
        try:
            self._set_fan(1.0 if on else 0.0)
        except Exception:  # noqa: BLE001 - el actuador nunca debe romper el flujo
            pass

    # -- Reactor ------------------------------------------------------------
    def tick(self, eventtime: float) -> float:
        """Callback de timer; devuelve el proximo deadline o ``NEVER``."""
        return eventtime + self.poll_s

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "forced": self.forced,
            "on_temp": self.on_temp,
            "off_temp": self.off_temp,
            "last_temp": self.last_temp,
            "enabled": self._enabled,
        }


__all__ = ["FanController", "FORCED_OFF", "FORCED_AUTO", "FORCED_ON"]
