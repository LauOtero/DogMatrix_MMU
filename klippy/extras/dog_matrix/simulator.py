"""Simulador interactivo tipo ``make console`` (paridad Dev-Simulator).

Construye un ``DogMatrixCore`` sobre un *printer* en memoria (sin Klipper ni
hardware) y expone una consola de comandos ``DM_*``/``MMU_*`` para
experimentar, validar macros y depurar la FSM.

Uso:
    py -3 -m dog_matrix.simulator
    py -3 -m dog_matrix.simulator --command "DM_STATUS"

El simulador es autocontenido (no depende de los dobles de los tests) y no
bloquea: el tiempo del reactor se controla explicitamente con ``TICK``.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional


class _SimReactor:
    NEVER = 1.0e18

    def __init__(self) -> None:
        self.now = 0.0
        self.timers: Dict[int, Any] = {}
        self.callbacks: List[Callable[[float], None]] = []
        self._next_id = 0

    def monotonic(self) -> float:
        return self.now

    def register_timer(self, callback: Callable[[float], float], when: float) -> int:
        self._next_id += 1
        self.timers[self._next_id] = (callback, when)
        return self._next_id

    def update_timer(self, handle: int, when: float) -> None:
        if handle in self.timers:
            callback, _ = self.timers[handle]
            self.timers[handle] = (callback, when)

    def register_callback(self, callback: Callable[[float], None]) -> None:
        self.callbacks.append(callback)

    def advance(self, seconds: float) -> None:
        """Avanza el reloj y dispara callbacks y timers vencidos."""
        target = self.now + max(0.0, float(seconds))
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            try:
                callback(self.now)
            except Exception:  # noqa: BLE001
                continue
        due = [(handle, cb, when) for handle, (cb, when) in self.timers.items() if when <= target]
        for handle, callback, _when in sorted(due, key=lambda item: item[2]):
            try:
                nxt = callback(target if target <= self.NEVER else target)
            except Exception:  # noqa: BLE001
                nxt = self.NEVER
            if handle in self.timers:
                self.timers[handle] = (callback, nxt)
        self.now = target


class _SimGcode:
    def __init__(self) -> None:
        self.commands: Dict[str, Callable[[Any], None]] = {}
        self.scripts: List[str] = []
        self.responses: List[str] = []

    def register_command(self, name: str, handler: Callable[[Any], None], desc: str = "") -> None:
        self.commands[name] = handler

    def run_script_from_command(self, script: str) -> None:
        self.scripts.append(script)

    def respond_info(self, message: str) -> None:
        self.responses.append(message)


class _SimGcmd:
    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        self.params = params or {}
        self.responses: List[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def get_int(self, key: str, default: Optional[int] = 0) -> Optional[int]:
        value = self.params.get(key, default)
        return None if value is None else int(value)

    def get_float(self, key: str, default: float = 0.0, **kwargs: Any) -> Any:
        value = self.params.get(key, default)
        return None if value is None else float(value)

    def respond_info(self, message: str) -> None:
        self.responses.append(message)

    def error(self, message: str) -> None:
        raise _SimError(message)


class _SimError(Exception):
    """Equivalente a ``gcmd.error`` en el simulador."""


class _SimPrinter:
    def __init__(self) -> None:
        self.gcode = _SimGcode()
        self.objects: Dict[str, Any] = {"gcode": self.gcode}
        self.events: Dict[str, List[Callable[[], None]]] = {}
        self.reactor = _SimReactor()

    def lookup_object(self, name: str, default: Any = None) -> Any:
        return self.objects.get(name, default)

    def load_object(self, config: Any, name: str) -> Any:
        obj = self.objects.get(name)
        if obj is None:
            raise KeyError(name)
        return obj

    def add_object(self, name: str, obj: Any) -> None:
        self.objects[name] = obj

    def get_reactor(self) -> _SimReactor:
        return self.reactor

    def send_event(self, name: str, **kwargs: Any) -> None:
        return None

    def register_event_handler(self, event: str, callback: Callable[[], None]) -> None:
        self.events.setdefault(event, []).append(callback)

    def fire(self, event: str) -> None:
        for callback in self.events.get(event, []):
            callback()


class _SimConfig:
    def __init__(self, printer: _SimPrinter, values: Dict[str, Any]) -> None:
        self._printer = printer
        self._values = values

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
        return "dog_matrix"

    def get_printer(self) -> _SimPrinter:
        return self._printer


def _parse_line(line: str) -> tuple:
    parts = line.strip().split()
    if not parts:
        return "", {}
    name = parts[0].upper()
    params: Dict[str, Any] = {}
    for token in parts[1:]:
        key, _, value = token.partition("=")
        if not _:
            continue
        params[key.strip().upper()] = value.strip()
    return name, params


class Simulator:
    """Consola interactiva sobre un ``DogMatrixCore`` simulado."""

    def __init__(self, profile: str = "box_turtle", **overrides: Any) -> None:
        from .core import DogMatrixCore

        workdir = tempfile.mkdtemp(prefix="dog_matrix_sim_")
        values: Dict[str, Any] = {
            "profile": profile,
            "state_store": os.path.join(workdir, "state.json"),
            "log_path": os.path.join(workdir, "dog_matrix.jsonl"),
            "evidence_dir": os.path.join(workdir, "evidence"),
            "log_level": "warning",
        }
        values.update(overrides)
        self.printer = _SimPrinter()
        self.config = _SimConfig(self.printer, values)
        self.core = DogMatrixCore(self.config)
        self.printer.objects["dog_matrix"] = self.core
        self.printer.fire("klippy:ready")

    # -- Comandos -----------------------------------------------------------
    def execute(self, line: str) -> List[str]:
        """Ejecuta una linea de comandos y devuelve las respuestas."""
        name, params = _parse_line(line)
        if not name:
            return []
        if name == "TICK":
            seconds = float(params.get("SECONDS", 1.0))
            self.printer.reactor.advance(seconds)
            return [f"t={self.printer.reactor.monotonic():.3f}"]
        if name == "DUMP":
            return [f"{key}={value}" for key, value in sorted(self.dump_state().items())]
        if name == "SCRIPT":
            return [f"SCRIPT requiere --script (uso no interactivo)"]
        if name == "HELP":
            return [f"{cmd}" for cmd in sorted(self.printer.gcode.commands)]
        command = self.printer.gcode.commands.get(name)
        if command is None:
            return [f"ERROR: comando desconocido: {name}"]
        gcmd = _SimGcmd(params)
        try:
            command(gcmd)
        except _SimError as exc:
            return [f"ERROR: {exc}"]
        except Exception as exc:  # noqa: BLE001 - consola defensiva
            return [f"ERROR: {exc}"]
        return list(gcmd.responses)

    def run_script(self, lines: List[str], stop_on_error: bool = True) -> List[str]:
        """Ejecuta una lista de lineas (script) y devuelve todas las respuestas.

        Con ``stop_on_error`` se detiene en el primer ``ERROR:``.
        """
        output: List[str] = []
        for raw in lines:
            line = str(raw).strip()
            if not line or line.startswith("#"):
                continue
            responses = self.execute(line)
            output.extend(responses)
            if stop_on_error and any(item.startswith("ERROR:") for item in responses):
                break
        return output

    def dump_state(self) -> Dict[str, Any]:
        """Vuelca el estado del contrato ``printer.mmu`` (para diagnostico)."""
        try:
            return dict(self.core.get_status(self.printer.reactor.monotonic()))
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def run_console(self, stdin: Any = None, stdout: Any = None) -> None:
        """Bucle interactivo de consola (lee lineas hasta EOF o ``exit``)."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        stdout.write("Dog Matrix MMU simulator. Escribe HELP, DUMP o exit.\n")
        for raw in stdin:
            line = raw.strip()
            if line.lower() in ("exit", "quit"):
                break
            for response in self.execute(line):
                stdout.write(response + "\n")
            stdout.flush()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Simulador de Dog Matrix MMU")
    parser.add_argument("--profile", default="box_turtle", help="Perfil a cargar")
    parser.add_argument("--command", action="append", default=[], help="Comando a ejecutar")
    parser.add_argument("--script", default=None, help="Fichero con comandos (uno por linea)")
    parser.add_argument("--json", action="store_true", help="Salida JSON del estado final")
    args = parser.parse_args(argv)
    simulator = Simulator(profile=args.profile)
    output: List[str] = []
    if args.script:
        try:
            with open(args.script, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError as exc:
            print(f"No se pudo leer el script: {exc}")
            return 2
        output.extend(simulator.run_script(lines))
    for command in args.command:
        output.extend(simulator.execute(command))
    if args.json:
        import json

        print(json.dumps(simulator.dump_state(), default=str, indent=2))
        return 0
    if output:
        for response in output:
            print(response)
        return 0
    simulator.run_console()
    return 0


if __name__ == "__main__":  # pragma: no cover - entrypoint manual
    raise SystemExit(main())


__all__ = ["Simulator", "main"]
