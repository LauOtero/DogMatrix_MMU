"""Botones fisicos de carga/descarga/eyeccion para Dog Matrix MMU.

Soporta botones GPIO conectados al panel MMU que permiten expulsar o cargar
filamento de forma fisica. Se integra con las **librerias propias de Klipper**:
``printer.load_object(config, 'buttons')`` +
``register_debounce_button`` (antirrebote por hardware/software), y difiere la
accion al reactor (``register_callback``) para no bloquear el hilo principal.

La logica de borde (``handle_edge``) es pura y determinista, de modo que puede
probarse sin hardware.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

BUTTON_ACTION_LOAD = "load"
BUTTON_ACTION_UNLOAD = "unload"
BUTTON_ACTION_EJECT = "eject"


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
class EjectionButton:
    """Representa un boton de eyeccion fisico."""

    name: str
    pin: Optional[str] = None
    action: str = BUTTON_ACTION_LOAD
    gate: Optional[int] = None
    pressed: bool = False
    last_press: float = 0.0
    debounce_ms: float = 50.0
    stable: bool = False
    candidate: bool = False
    candidate_since: float = 0.0
    presses: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "pin": self.pin,
            "action": self.action,
            "gate": self.gate,
            "pressed": self.stable,
            "last_press": self.last_press,
            "presses": self.presses,
        }


class EjectionButtons:
    """Gestiona botones fisicos de carga/descarga/eyeccion."""

    def __init__(
        self,
        config: Any = None,
        profile: Any = None,
        on_action: Optional[Callable[[str, str, Optional[int]], None]] = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self._on_action = on_action
        self.buttons: Dict[str, EjectionButton] = {}
        self.handles: List[Any] = []
        self._init_buttons(config)

    # -- Configuracion ------------------------------------------------------
    def _init_buttons(self, config: Any) -> None:
        gates = int(_cfg_get(config, "ejection_buttons_gates", 0) or 0)
        if gates > 0:
            for gate in range(gates):
                self.register_button(f"eject_{gate}", action=BUTTON_ACTION_EJECT, gate=gate)
        else:
            # Botones de panel genericos (load/unload/eject) usados en muchos disenos.
            self.register_button("eject", action=BUTTON_ACTION_EJECT)
            self.register_button("load", action=BUTTON_ACTION_LOAD)
            self.register_button("unload", action=BUTTON_ACTION_UNLOAD)

    def register_button(
        self,
        name: str,
        pin: Optional[str] = None,
        action: str = BUTTON_ACTION_LOAD,
        gate: Optional[int] = None,
    ) -> None:
        """Registra un boton (con pin GPIO opcional)."""
        self.buttons[name] = EjectionButton(name=name, pin=pin, action=action, gate=gate)

    # -- Integracion Klipper ------------------------------------------------
    def wire(
        self,
        printer: Any,
        config: Any,
        pin_map: Optional[Dict[str, str]] = None,
    ) -> List[Any]:
        """Registra los botones con ``[buttons]`` de Klipper.

        ``pin_map`` mapea nombre de boton -> pin. Si se omite, se usan los pines
        declarados en cada boton. El handler del boton difiere la accion al
        reactor (no bloquea el hilo principal).
        """
        try:
            buttons = printer.load_object(config, "buttons")
            reactor = printer.get_reactor()
        except Exception:  # noqa: BLE001 - entorno sin botones
            return []

        mapping = pin_map or {name: btn.pin for name, btn in self.buttons.items() if btn.pin}

        def _make_handler(name: str):
            def _handler(eventtime: float, state: bool) -> None:
                if not state:
                    return
                try:
                    reactor.register_callback(lambda et=eventtime: self.handle_edge(name, True, et))
                except Exception:  # noqa: BLE001 - reactor no disponible
                    self.handle_edge(name, True, eventtime)

            return _handler

        for name, pin in mapping.items():
            if not pin:
                continue
            try:
                handle = buttons.register_debounce_button(pin, _make_handler(name))
                self.handles.append((name, pin, handle))
            except Exception:  # noqa: BLE001 - un boton invalido no rompe el resto
                continue
        return self.handles

    # -- Logica de borde ----------------------------------------------------
    def handle_edge(self, name: str, pressed: bool, eventtime: float) -> Optional[str]:
        """Procesa un flanco de boton con antirrebote; dispara la accion.

        Devuelve el nombre de la accion disparada o ``None`` si se ignora.
        """
        button = self.buttons.get(name)
        if button is None or not pressed:
            return None
        debounce_s = button.debounce_ms / 1000.0
        if button.stable and (eventtime - button.last_press) < debounce_s:
            return None
        button.stable = True
        button.pressed = True
        button.last_press = float(eventtime)
        button.presses += 1
        if self._on_action is not None:
            try:
                self._on_action(name, button.action, button.gate)
            except Exception:  # noqa: BLE001 - aislar fallos del consumidor
                pass
        return button.action

    # -- Simulacion / estado -----------------------------------------------
    def trigger(self, name: str, eventtime: Optional[float] = None) -> Optional[str]:
        """Simula la presion de un boton (para tests)."""
        when = time.monotonic() if eventtime is None else float(eventtime)
        return self.handle_edge(name, True, when)

    def poll(self) -> Dict[str, Any]:
        """Devuelve el estado de todos los botones."""
        return {name: button.as_dict() for name, button in self.buttons.items()}

    def get_status(self) -> Dict[str, Any]:
        return {
            "buttons": {name: button.as_dict() for name, button in self.buttons.items()},
            "wired": len(self.handles),
        }


__all__ = [
    "EjectionButtons", "EjectionButton",
    "BUTTON_ACTION_LOAD", "BUTTON_ACTION_UNLOAD", "BUTTON_ACTION_EJECT",
]
