"""Gestion ambiental: calefactor de secado, ventilador y ciclos de secado.

Controla el calefactor de secado de filamento (PWM), el ventilador del
enclosure y ejecuta ciclos de secado temporizados dirigidos por el reactor de
Klipper (sin bloqueo). Las lecturas de temperatura/humedad y los actuadores se
inyectan, de modo que el modulo es determinista y testeable sin hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


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
class EnvironmentStatus:
    """Estado del sistema ambiental."""

    enabled: bool = False
    enclosure_fan_pwm: float = 0.0  # 0.0 - 1.0
    dryer_power: float = 0.0  # 0.0 - 1.0
    dryer_target_temp: float = 55.0  # C
    actual_temp: float = 25.0  # C
    actual_humidity: float = 50.0  # %RH
    drying: bool = False
    drying_remaining_s: float = 0.0
    filament_dry_time: int = 0  # minutos desde ultimo secado
    errors: int = 0

    @property
    def temperature(self) -> float:
        """Alias de ``actual_temp`` (compatibilidad con consumidores)."""
        return self.actual_temp

    @property
    def humidity(self) -> float:
        return self.actual_humidity

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "enclosure_fan_pwm": round(self.enclosure_fan_pwm, 3),
            "dryer_power": round(self.dryer_power, 3),
            "dryer_target_temp": self.dryer_target_temp,
            "actual_temp": self.actual_temp,
            "actual_humidity": self.actual_humidity,
            "drying": self.drying,
            "drying_remaining_s": round(self.drying_remaining_s, 1),
            "filament_dry_time": self.filament_dry_time,
            "errors": self.errors,
        }


class EnvironmentManager:
    """Gestor de control ambiental y secado de filamento."""

    def __init__(
        self,
        config: Any = None,
        profile: Any = None,
        set_heater: Optional[Callable[[float], None]] = None,
        set_fan: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self._set_heater_hw = set_heater
        self._set_fan_hw = set_fan
        self._clock = clock
        self.status = EnvironmentStatus()
        self.status.enabled = bool(_cfg_get(config, "enable_environment", False))
        self.status.dryer_target_temp = float(_cfg_get(config, "dryer_target_temp", 55.0))
        self.target_temp: float = self.status.dryer_target_temp
        self.hysteresis: float = float(_cfg_get(config, "dryer_hysteresis", 2.0))
        self.default_dry_s: float = float(_cfg_get(config, "dryer_default_duration_s", 3600.0))
        self.dryer_heater_pin: Optional[str] = _cfg_get(config, "dryer_heater_pin", None)
        self.enclosure_fan_pin: Optional[str] = _cfg_get(config, "enclosure_fan_pin", None)
        self._dry_deadline: float = 0.0

    # -- Tiempo -------------------------------------------------------------
    def _now(self) -> float:
        if self._clock is not None:
            try:
                return float(self._clock())
            except Exception:  # noqa: BLE001
                pass
        import time

        return time.monotonic()

    # -- Actuadores ---------------------------------------------------------
    def set_dryer_power(self, power: float) -> None:
        """Fija la potencia del calefactor de secado (0.0-1.0)."""
        if not self.status.enabled:
            return
        self.status.dryer_power = min(1.0, max(0.0, float(power)))
        self._apply_heater()

    def set_enclosure_fan_pwm(self, pwm: float) -> None:
        """Fija el PWM del ventilador del enclosure (0.0-1.0)."""
        if not self.status.enabled:
            return
        self.status.enclosure_fan_pwm = min(1.0, max(0.0, float(pwm)))
        self._apply_fan()

    def _apply_heater(self) -> None:
        if self._set_heater_hw is None:
            return
        try:
            self._set_heater_hw(self.status.dryer_power)
        except Exception:  # noqa: BLE001 - hardware opcional
            self.status.errors += 1

    def _apply_fan(self) -> None:
        if self._set_fan_hw is None:
            return
        try:
            self._set_fan_hw(self.status.enclosure_fan_pwm)
        except Exception:  # noqa: BLE001
            self.status.errors += 1

    # -- Ciclos de secado ---------------------------------------------------
    def start_drying(
        self, target_temp: Optional[float] = None, duration_s: Optional[float] = None
    ) -> bool:
        """Inicia un ciclo de secado (devuelve False si esta deshabilitado)."""
        if not self.status.enabled:
            return False
        if target_temp is not None:
            self.status.dryer_target_temp = float(target_temp)
        duration = self.default_dry_s if duration_s is None else max(0.0, float(duration_s))
        self.status.drying = True
        self._dry_deadline = self._now() + duration
        self.status.drying_remaining_s = duration
        self._control()
        return True

    def stop_drying(self) -> None:
        """Detiene el ciclo de secado y apaga el calefactor."""
        self.status.drying = False
        self._dry_deadline = 0.0
        self.status.drying_remaining_s = 0.0
        self.set_dryer_power(0.0)

    def _control(self) -> None:
        """Control bang-bang con histeresis (determinista, sin bloqueo)."""
        if not self.status.drying:
            return
        target = self.status.dryer_target_temp
        if self.status.actual_temp >= target:
            self.set_dryer_power(0.0)
        elif self.status.actual_temp < target - self.hysteresis:
            self.set_dryer_power(1.0)

    def tick(self, eventtime: float) -> float:
        """Callback de reactor: controla el secado y devuelve el proximo deadline."""
        if not self.status.drying:
            return eventtime + 1.0
        remaining = self._dry_deadline - eventtime
        self.status.drying_remaining_s = max(0.0, remaining)
        if remaining <= 0:
            self.stop_drying()
            self.status.filament_dry_time = 0
            return eventtime + 1.0
        self._control()
        return eventtime + 1.0

    def next_deadline(self) -> Optional[float]:
        return self._dry_deadline or None

    # -- Lecturas -----------------------------------------------------------
    def update_readings(self, temp: float, humidity: float) -> None:
        """Actualiza lecturas de temperatura y humedad."""
        self.status.actual_temp = float(temp)
        self.status.actual_humidity = float(humidity)
        if humidity > 70:
            self.status.filament_dry_time = min(120, self.status.filament_dry_time + 1)
        elif humidity < 40 and self.status.filament_dry_time > 0:
            self.status.filament_dry_time = max(0, self.status.filament_dry_time - 1)

    def get_status(self) -> EnvironmentStatus:
        """Obtiene estado completo."""
        return self.status


__all__ = ["EnvironmentManager", "EnvironmentStatus"]
