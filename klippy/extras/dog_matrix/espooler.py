"""Control de eSpooler DC con PWM, curva de velocidad y bursts temporizados.

Soporta motor DC (p. ej. DC20 de Box Turtle) con control PWM/digital, escala y
exponente no lineal, direccion y *bursts* de duracion acotada gestionados por el
reactor de Klipper (sin bloqueo). El actuador de hardware se inyecta
(``set_pwm``/``set_dir``) para permitir pruebas sin GPIO; si no se inyecta, los
comandos G-code se emiten via ``emit``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

ESPOOLER_DEFAULT_PWM_FREQ = 1000  # Hz
ESPOOLER_DEFAULT_SCALE = 1.0
ESPOOLER_DEFAULT_EXPONENT = 1.0
ESPOOLER_DEFAULT_BURST_S = 2.0


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


class ESpoolerStatus:
    """Estado del eSpooler."""

    def __init__(self) -> None:
        self.enabled: bool = False
        self.direction: str = "stop"  # forward, reverse, stop
        self.pwm: float = 0.0  # 0.0 - 1.0
        self.speed: float = 0.0  # valor escalado (curva)
        self.gate: Optional[int] = None
        self.burst_until: float = 0.0
        self.errors: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "direction": self.direction,
            "pwm": round(self.pwm, 3),
            "speed": round(self.speed, 3),
            "gate": self.gate,
            "burst_until": self.burst_until,
            "errors": self.errors,
        }


class ESpooler:
    """Controlador de eSpooler DC con PWM y bursts por reactor."""

    def __init__(
        self,
        config: Any = None,
        profile: Any = None,
        set_pwm: Optional[Callable[[float], None]] = None,
        set_dir: Optional[Callable[[bool], None]] = None,
        emit: Optional[Callable[[str], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self._set_pwm_hw = set_pwm
        self._set_dir_hw = set_dir
        self._emit_fn = emit
        self._clock = clock
        self.status = ESpoolerStatus()
        self.pwm_pin: Optional[str] = _cfg_get(config, "espooler_pwm_pin", None)
        self.direction_pin: Optional[str] = _cfg_get(config, "espooler_dir_pin", None)
        self.scale: float = float(_cfg_get(config, "espooler_scale", ESPOOLER_DEFAULT_SCALE))
        self.exponent: float = float(_cfg_get(config, "espooler_exponent", ESPOOLER_DEFAULT_EXPONENT))
        self.enabled: bool = bool(_cfg_get(config, "enable_espooler", False))
        self.status.enabled = self.enabled

    # -- Curva --------------------------------------------------------------
    def _curve(self, pwm: float) -> float:
        raw = min(1.0, max(0.0, float(pwm)))
        return max(0.0, (raw ** self.exponent) * self.scale)

    def _now(self) -> float:
        if self._clock is not None:
            try:
                return float(self._clock())
            except Exception:  # noqa: BLE001
                pass
        import time

        return time.monotonic()

    # -- API publica --------------------------------------------------------
    def set_gate(self, gate: int) -> None:
        """Establecer el gate activo."""
        self.status.gate = int(gate)

    def forward(self, speed: Optional[float] = None) -> None:
        """Mover eSpooler hacia adelante (rebobinar)."""
        self._drive("forward", 0.5 if speed is None else speed)

    def reverse(self, speed: Optional[float] = None) -> None:
        """Mover eSpooler hacia atras (asistir carga)."""
        self._drive("reverse", 0.5 if speed is None else speed)

    def _drive(self, direction: str, pwm: float) -> None:
        if not self.enabled:
            return
        self.status.direction = direction
        self.status.pwm = min(1.0, max(0.0, float(pwm)))
        self.status.speed = self._curve(self.status.pwm)
        self._apply_hardware()

    def stop(self) -> None:
        """Detener eSpooler."""
        self.status.direction = "stop"
        self.status.pwm = 0.0
        self.status.speed = 0.0
        self.status.burst_until = 0.0
        self._apply_hardware()

    def burst(self, duration_s: float = ESPOOLER_DEFAULT_BURST_S, speed: float = 0.5) -> float:
        """Arranca un burst temporal (devuelve el instante de fin)."""
        self._drive("forward", speed)
        end = self._now() + max(0.0, float(duration_s))
        self.status.burst_until = end
        return end

    def tick(self, eventtime: float) -> float:
        """Callback de reactor: detiene el burst al vencer. Devuelve deadline."""
        if self.status.burst_until and eventtime >= self.status.burst_until:
            self.stop()
            return eventtime + 1.0
        if self.status.burst_until:
            return self.status.burst_until
        return eventtime + 1.0

    def next_deadline(self) -> Optional[float]:
        return self.status.burst_until or None

    # -- Hardware -----------------------------------------------------------
    def _apply_hardware(self) -> None:
        direction_forward = self.status.direction == "forward"
        try:
            if self._set_dir_hw is not None:
                self._set_dir_hw(direction_forward)
            if self._set_pwm_hw is not None:
                self._set_pwm_hw(self.status.speed if self.status.direction != "stop" else 0.0)
                return
        except Exception:  # noqa: BLE001 - hardware opcional
            self.status.errors += 1
            return
        # Fallback: emitir G-code de actuador si hay pines declarados.
        if self._emit_fn is not None and self.pwm_pin:
            power = self.status.speed if self.status.direction != "stop" else 0.0
            self._emit_fn(f"SET_PIN PIN={self.pwm_pin} VALUE={power:.3f}")

    def is_enabled(self) -> bool:
        return self.enabled and self.status.direction != "stop"

    def get_status(self) -> Dict[str, Any]:
        return {
            **self.status.as_dict(),
            "scale": self.scale,
            "exponent": self.exponent,
            "pwm_pin": self.pwm_pin,
        }


__all__ = ["ESpooler", "ESpoolerStatus", "ESPOOLER_DEFAULT_PWM_FREQ",
           "ESPOOLER_DEFAULT_SCALE", "ESPOOLER_DEFAULT_EXPONENT"]
