"""Gestión ambiental: ventiladores, secado de filamento y monitoreo.

Controla ventiladores de enclosure, calefactores de secado y monitorea
temperatura y humedad para optimizar la impresión de filamentos
higróscopicos (TPU, PETG, nailon, etc.).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from dataclasses import dataclass


@dataclass
class EnvironmentStatus:
    """Estado del sistema ambiental."""
    enabled: bool = False
    enclosure_fan_pwm: float = 0.0  # 0.0 - 1.0
    dryer_power: float = 0.0  # 0.0 - 1.0 (calefactor de secado)
    dryer_target_temp: float = 55.0  # °C
    actual_temp: float = 25.0  # °C actual del enclosure
    actual_humidity: float = 50.0  # %RH actual
    filament_dry_time: int = 0  # minutos desde último secado
    errors: int = 0


class EnvironmentManager:
    """Gestora de control ambiental para impresión de filamentos."""

    def __init__(self, config: Any = None, profile: Any = None) -> None:
        self.config = config
        self.profile = profile
        self.status = EnvironmentStatus()
        self.dryer_heater_pin: Optional[str] = None
        self.enclosure_fan_pin: Optional[str] = None
        self._init_pins()
        # Cargar configuración
        self.enabled: bool = bool(
            getattr(config, "get", lambda k, d: d)(
                config, "enable_environment", False
            )
        ) if config else False
        self.target_temp: float = float(
            getattr(config, "get", lambda k, d: d)(
                config, "dryer_target_temp", 55.0
            )
        ) if config else 55.0

    def _init_pins(self) -> None:
        """Inicializar pines GPIO desde la configuración."""
        self.dryer_heater_pin = (
            getattr(self.profile, "dryer_heater_pin", None) if self.profile else None
        )
        self.enclosure_fan_pin = (
            getattr(self.profile, "enclosure_fan_pin", None) if self.profile else None
        )

    def set_dryer_power(self, power: float) -> None:
        """Set dryer (filament heater) power 0.0-1.0."""
        if not self.status.enabled:
            return
        self.status.dryer_power = min(1.0, max(0.0, power))
        self._set_hardware_dryer()

    def set_enclosure_fan_pwm(self, pwm: float) -> None:
        """Set enclosure fan PWM 0.0-1.0."""
        if not self.status.enabled:
            return
        self.status.enclosure_fan_pwm = min(1.0, max(0.0, pwm))
        self._set_hardware_fan()

    def _set_hardware_dryer(self) -> None:
        """Aplicar power al heater de secado (GPIO)."""
        # Escribir valor al pin GPIO del heater
        try:
            if self.dryer_heater_pin:
                # Implementación real escribiría al pin
                pass
        except Exception:  # noqa: BLE001 - hardware opcional
            pass

    def _set_hardware_fan(self) -> None:
        """Aplicar PWM al ventilador de enclosure."""
        try:
            if self.enclosure_fan_pin:
                # Implementación real escribiría PWM al pin
                pass
        except Exception:  # noqa: BLE001 - hardware opcional
            pass

    def update_readings(self, temp: float, humidity: float) -> None:
        """Actualizar lecturas de temperatura y humedad."""
        self.status.actual_temp = temp
        self.status.actual_humidity = humidity
        # Lógica simple: si la humedad es alta, aumentar tiempo de secado
        if humidity > 70:
            self.status.filament_dry_time = min(120, self.status.filament_dry_time + 1)
        elif humidity < 40 and self.status.filament_dry_time > 0:
            self.status.filament_dry_time = max(0, self.status.filament_dry_time - 1)

    def get_status(self) -> EnvironmentStatus:
        """Obtener estado completo."""
        return self.status


__all__ = ["EnvironmentManager", "EnvironmentStatus"]