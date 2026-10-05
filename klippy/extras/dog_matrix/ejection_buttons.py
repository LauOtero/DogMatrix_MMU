"""Botones de eyección físicos para Dog Matrix MMU.

Soporte para botones GPIO conectados al panel MMU que permiten
expulsar filamento de forma física, compatible con el sistema
de Happy Hare que tiene botones de eyección dedicados.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from dataclasses import dataclass


BUTTON_ACTION_LOAD = "load"
BUTTON_ACTION_UNLOAD = "unload"
BUTTON_ACTION_EJECT = "eject"


@dataclass
class EjectionButton:
    """Representa un botón de eyección físico."""
    name: str
    pin: Optional[str] = None  # GPIO pin number
    action: str = BUTTON_ACTION_LOAD  # load, unload, eject
    pressed: bool = False
    last_press: float = 0.0
    debounce_ms: float = 50.0
    stable: bool = False


class EjectionButtons:
    """Gestiona botones de eyección físicos para MMU."""

    def __init__(self, config: Any = None, profile: Any = None) -> None:
        self.config = config
        self.profile = profile
        self.buttons: Dict[str, EjectionButton] = {}
        self._init_buttons()

    def _init_buttons(self) -> None:
        """Inicializar botones desde la configuración."""
        # Leer configuración de botones del perfil YAML
        # Por ahora, crear botones por defecto si hay configuración
        if self.profile:
            # Intentar leer botones configurados
            pass

        # Botón de eyección por defecto (usado en muchos diseños MMU)
        self.buttons["eject"] = EjectionButton(
            name="eject",
            action=BUTTON_ACTION_EJECT,
        )
        self.buttons["load"] = EjectionButton(
            name="load",
            action=BUTTON_ACTION_LOAD,
        )
        self.buttons["unload"] = EjectionButton(
            name="unload",
            action=BUTTON_ACTION_UNLOAD,
        )

    def register_button(self, name: str, pin: Optional[str] = None, action: str = BUTTON_ACTION_LOAD) -> None:
        """Registrar un nuevo botón."""
        self.buttons[name] = EjectionButton(name=name, pin=pin, action=action)

    def poll(self) -> Dict[str, Any]:
        """Leer estado de todos los botones."""
        result = {}
        for name, button in self.buttons.items():
            # En implementación real, leería el estado GPIO aquí
            # Usando la capa CFFI para debounce hardware
            button.stable = button.pressed  # Simulado por ahora
            result[name] = {
                "name": button.name,
                "action": button.action,
                "pressed": button.stable,
                "last_press": button.last_press,
            }
        return result

    def trigger(self, name: str) -> None:
        """Simular presión de botón (para tests)."""
        if name in self.buttons:
            button = self.buttons[name]
            button.pressed = True
            button.last_press = time.monotonic()
            # En implementación real, esto desencadenaría la acción G-code

    def get_status(self) -> Dict[str, Any]:
        """Obtener estado de todos los botones."""
        return {name: button.as_dict() if hasattr(button, 'as_dict') 
                else {"name": button.name, "action": button.action, "pressed": button.pressed}
                for name, button in self.buttons.items()}


__all__ = ["EjectionButtons", "EjectionButton", BUTTON_ACTION_LOAD, BUTTON_ACTION_UNLOAD, BUTTON_ACTION_EJECT]