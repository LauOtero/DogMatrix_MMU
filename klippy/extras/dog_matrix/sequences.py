"""Secuencias de carga/descarga componibles (paridad Custom-Load-Unload-Sequences).

Permite construir el ciclo de carga y descarga como una **secuencia de pasos
nombrados** (``_MMU_STEP_*``) que pueden sobreescribirse o extenderse sin tocar
la FSM. Cada paso es una funcion ``handler(ctx) -> bool`` pura y determinista;
la ausencia de un handler en el contexto marca el paso como omitido (no rompe).

Compatibilidad: si un perfil declara ``gcode_load_sequence``/``gcode_unload_sequence``
se anaden como pasos personalizados al final de la secuencia por defecto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Nombres de paso canonicos.
STEP_HOME = "home"
STEP_SELECT = "select"
STEP_PRE_LOAD = "pre_load"
STEP_LOAD = "load"
STEP_VERIFY = "verify"
STEP_FORM_TIP = "form_tip"
STEP_PURGE = "purge"
STEP_COMMIT = "commit"
STEP_UNLOAD = "unload"
STEP_POST_UNLOAD = "post_unload"

#: Centinela devuelto por un handler para indicar que el paso se omite.
SKIP = object()


@dataclass
class StepResult:
    """Resultado de un paso de secuencia."""

    name: str
    success: bool
    skipped: bool = False
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "skipped": self.skipped,
            "message": self.message,
        }


class Sequence:
    """Secuencia ordenada de pasos nombrados."""

    def __init__(self, name: str, steps: Optional[List[Any]] = None) -> None:
        self.name = name
        self.steps: List[Any] = list(steps or [])
        self.results: List[StepResult] = []
        self.completed = False
        self.failed_step: Optional[str] = None

    def add(self, name: str, handler: Callable[[Dict[str, Any]], bool]) -> "Sequence":
        """Anade un paso al final de la secuencia."""
        self.steps.append((name, handler))
        return self

    def insert(self, index: int, name: str, handler: Callable[[Dict[str, Any]], bool]) -> "Sequence":
        self.steps.insert(index, (name, handler))
        return self

    def replace(self, name: str, handler: Callable[[Dict[str, Any]], bool]) -> bool:
        """Sustituye el handler de un paso existente (extension de usuario)."""
        for index, (step_name, _) in enumerate(self.steps):
            if step_name == name:
                self.steps[index] = (name, handler)
                return True
        return False

    def run(self, ctx: Dict[str, Any]) -> bool:
        """Ejecuta los pasos; se detiene en el primer fallo."""
        self.results = []
        self.completed = False
        self.failed_step = None
        for name, handler in self.steps:
            if handler is None:
                self.results.append(StepResult(name, True, skipped=True))
                continue
            try:
                outcome = handler(ctx)
            except Exception as exc:  # noqa: BLE001 - un paso nunca debe propagar
                self.results.append(StepResult(name, False, message=str(exc)))
                self.failed_step = name
                return False
            if outcome is SKIP:
                self.results.append(StepResult(name, True, skipped=True))
                continue
            success = bool(outcome)
            self.results.append(StepResult(name, success))
            if not success:
                self.failed_step = name
                return False
        self.completed = True
        return True

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "completed": self.completed,
            "failed_step": self.failed_step,
            "steps": [result.as_dict() for result in self.results],
        }


def _opt(ctx: Dict[str, Any], key: str) -> Optional[Callable[..., bool]]:
    handler = ctx.get(key)
    return handler if callable(handler) else None


def _call(name: str):
    def _handler(ctx: Dict[str, Any]) -> Any:
        handler = _opt(ctx, name)
        if handler is None:
            return SKIP  # omitido: no hay handler en el contexto
        return bool(handler())

    return _handler


def build_default_load_sequence(name: str = "load") -> Sequence:
    """Secuencia de carga por defecto (pre_gate -> toolhead)."""
    seq = Sequence(name)
    seq.add(STEP_PRE_LOAD, _call(STEP_PRE_LOAD))
    seq.add(STEP_HOME, _call(STEP_HOME))
    seq.add(STEP_SELECT, _call(STEP_SELECT))
    seq.add(STEP_LOAD, _call(STEP_LOAD))
    seq.add(STEP_VERIFY, _call(STEP_VERIFY))
    seq.add(STEP_FORM_TIP, _call(STEP_FORM_TIP))
    seq.add(STEP_PURGE, _call(STEP_PURGE))
    seq.add(STEP_COMMIT, _call(STEP_COMMIT))
    return seq


def build_default_unload_sequence(name: str = "unload") -> Sequence:
    """Secuencia de descarga por defecto (toolhead -> gate)."""
    seq = Sequence(name)
    seq.add(STEP_FORM_TIP, _call(STEP_FORM_TIP))
    seq.add(STEP_UNLOAD, _call(STEP_UNLOAD))
    seq.add(STEP_POST_UNLOAD, _call(STEP_POST_UNLOAD))
    seq.add(STEP_COMMIT, _call(STEP_COMMIT))
    return seq


class SequenceRegistry:
    """Registro de secuencias personalizadas (por nombre)."""

    def __init__(self) -> None:
        self._sequences: Dict[str, Sequence] = {}

    def register(self, sequence: Sequence) -> None:
        self._sequences[sequence.name] = sequence

    def get(self, name: str) -> Optional[Sequence]:
        return self._sequences.get(name)

    def names(self) -> List[str]:
        return sorted(self._sequences)

    def get_status(self) -> Dict[str, Any]:
        return {
            "sequences": {
                name: [step for step, _ in sequence.steps]
                for name, sequence in sorted(self._sequences.items())
            }
        }


__all__ = [
    "Sequence", "StepResult", "SequenceRegistry", "SKIP",
    "build_default_load_sequence", "build_default_unload_sequence",
    "STEP_HOME", "STEP_SELECT", "STEP_PRE_LOAD", "STEP_LOAD", "STEP_VERIFY",
    "STEP_FORM_TIP", "STEP_PURGE", "STEP_COMMIT", "STEP_UNLOAD", "STEP_POST_UNLOAD",
]
