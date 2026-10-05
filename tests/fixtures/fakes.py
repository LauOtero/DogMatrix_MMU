"""Dobles de prueba (fakes) para los modulos de Klipper y Moonraker.

Permiten ejecutar la logica de Dog Matrix sin Klipper ni hardware real.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class FakeGcodeError(Exception):
    """Equivalente a gcmd.error() de Klipper."""


class FakeGcmd:
    """Comando G-code simulado."""

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        self.params = params or {}
        self.responses: List[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def get_int(self, key: str, default: Optional[int] = 0) -> Optional[int]:
        value = self.params.get(key, default)
        if value is None:
            return None
        return int(value)

    def get_float(self, key: str, default: float = 0.0, **kwargs: Any) -> float:
        value = self.params.get(key, default)
        return float(value)

    def respond_info(self, message: str) -> None:
        self.responses.append(message)

    def error(self, message: str) -> None:
        raise FakeGcodeError(message)


class FakeGcode:
    """Registro de comandos y respuestas de G-code."""

    def __init__(self) -> None:
        self.commands: Dict[str, Callable[[FakeGcmd], None]] = {}
        self.scripts: List[str] = []
        self.responses: List[str] = []

    def register_command(self, name: str, handler: Callable[[FakeGcmd], None], desc: str = "") -> None:
        self.commands[name] = handler

    def run_script_from_command(self, script: str) -> None:
        self.scripts.append(script)

    def respond_info(self, message: str) -> None:
        self.responses.append(message)

    def run(self, name: str, params: Optional[Dict[str, Any]] = None) -> FakeGcmd:
        command = FakeGcmd(params)
        self.commands[name](command)
        return command


class FakePrinter:
    """Printer de Klipper simulado."""

    def __init__(self) -> None:
        self.gcode = FakeGcode()
        self.objects: Dict[str, Any] = {"gcode": self.gcode}
        self.events: Dict[str, List[Callable[[], None]]] = {}

    def lookup_object(self, name: str, default: Any = None) -> Any:
        return self.objects.get(name, default)

    def register_event_handler(self, event: str, callback: Callable[[], None]) -> None:
        self.events.setdefault(event, []).append(callback)

    def fire(self, event: str) -> None:
        for callback in self.events.get(event, []):
            callback()


class FakeConfig:
    """ConfigWrapper de Klipper simulado."""

    def __init__(
        self,
        printer: Optional[FakePrinter] = None,
        values: Optional[Dict[str, Any]] = None,
        name: str = "dog_matrix",
    ) -> None:
        self._printer = printer if printer is not None else FakePrinter()
        self._values = values or {}
        self._name = name

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def getint(self, key: str, default: int = 0, **kwargs: Any) -> int:
        return int(self._values.get(key, default))

    def getfloat(self, key: str, default: float = 0.0, **kwargs: Any) -> float:
        return float(self._values.get(key, default))

    def getboolean(self, key: str, default: bool = False) -> bool:
        value = self._values.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def get_name(self) -> str:
        return self._name

    def get_printer(self) -> FakePrinter:
        return self._printer


__all__ = ["FakeGcodeError", "FakeGcmd", "FakeGcode", "FakePrinter", "FakeConfig"]
