"""Control de eSpooler DC para rebobinado motorizado.

Soporte para motor DC20 (como en Box Turtle) con control PWM/digital,
escala y exponente de velocidad no lineal. Funciones principales:
- Rebobinar filamento al descargar
- Asistir el movimiento al cargar
- Aliviar fricción durante impresión

Integración con MmuEnvironmentManager para coordinar con secado de filamento.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from . import _native


ESPOOLER_DEFAULT_PWM_FREQ = 1000  # Hz
ESPOOLER_DEFAULT_SCALE = 1.0
ESPOOLER_DEFAULT_EXPONENT = 1.0


class ESpoolerStatus:
    """Estado del eSpooler."""

    def __init__(self) -> None:
        self.enabled: bool = False
        self.direction: str = "stop"  # forward, reverse, stop
        self.pwm: float = 0.0  # 0.0 - 1.0
        self.speed: float = 0.0  # mm/s (calculada con escala/exponente)
        self.gate: Optional[int] = None
        self.last_change: float = time.time()
        self.errors: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "direction": self.direction,
            "pwm": round(self.pwm, 3),
            "speed": round(self.speed, 3),
            "gate": self.gate,
            "last_change": self.last_change,
            "errors": self.errors,
        }


class ESpooler:
    """Controlador de eSpooler DC."""

    def __init__(self, config: Any, profile: Any = None) -> None:
        self.config = config
        self.profile = profile
        self.status = ESpoolerStatus()
        self.pwm_pin: Optional[str] = None
        self.direction_pin: Optional[str] = None
        self._init_pins()
        self.scale: float = float(
            getattr(config, "get", lambda k, d: d)(
                config, "espools_scale", ESPOOLER_DEFAULT_SCALE
            )
        ) if config else ESPOOLER_DEFAULT_SCALE
        self.exponent: float = float(
            getattr(config, "get", lambda k, d: d)(
                config, "espools_exponent", ESPOOLER_DEFAULT_EXPONENT
            )
        ) if config else ESPOOLER_DEFAULT_EXPONENT
        self.enabled: bool = bool(
            getattr(config, "get", lambda k, d: d)(
                config, "enable_espooler", False
            )
        ) if config else False

    def _init_pins(self) -> None:
        """Inicializar pines GPIO desde la configuración."""
        # En implementación real, leería los pines del perfil YAML
        # Por ahora, los pines se configurarían externamente
        self.pwm_pin = getattr(self.profile, "espooler_pwm_pin", None) if self.profile else None
        self.direction_pin = getattr(self.profile, "espooler_dir_pin", None) if self.profile else None

    def set_gate(self, gate: int) -> None:
        """Establecer el gate activo."""
        self.status.gate = gate
        self.status.last_change = time.time()

    def forward(self, speed: Optional[float] = None) -> None:
        """Mover eSpooler hacia adelante (rebobinar)."""
        if not self.enabled:
            return
        self.status.direction = "forward"
        if speed is not None:
            self.status.pwm = min(1.0, max(0.0, speed))
        else:
            self.status.pwm = min(1.0, max(0.0, 0.5))
        # Aplicar escala y exponente
        raw = self.status.pwm
        self.status.speed = max(0.0, raw ** self.exponent * self.scale)
        self._set_hardware()

    def reverse(self, speed: Optional[float] = None) -> None:
        """Mover eSpooler hacia atrás (ayudar carga)."""
        if not self.enabled:
            return
        self.status.direction = "reverse"
        if speed is not None:
            self.status.pwm = min(1.0, max(0.0, speed))
        else:
            self.status.pwm = min(1.0, max(0.0, 0.5))
        raw = self.status.pwm
        self.status.speed = max(0.0, raw ** self.exponent * self.scale)
        self._set_hardware()

    def stop(self) -> None:
        """Detener eSpooler."""
        self.status.direction = "stop"
        self.status.pwm = 0.0
        self.status.speed = 0.0
        self._set_hardware()

    def _set_hardware(self) -> None:
        """Aplicar estado a hardware GPIO (implementación nativa CFFI)."""
        # En implementación real, escribiría a los pines GPIO
        # Usando la capa nativa _native para control preciso
        try:
            if self.pwm_pin and self.direction_pin:
                # Escribir valores a pines
                pass  # Placeholder para implementación real
        except Exception:  # noqa: BLE001 - hardware opcional
            pass

    def is_enabled(self) -> bool:
        """Verificar si el eSpooler está habilitado."""
        return self.enabled and self.status.direction != "stop"

    def get_status(self) -> Dict[str, Any]:
        """Obtener estado completo del eSpooler."""
        return self.status.as_dict()


__all__ = ["ESpooler", "ESpoolerStatus"]