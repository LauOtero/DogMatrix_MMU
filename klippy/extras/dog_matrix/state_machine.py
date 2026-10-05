"""Maquina de estados finita (FSM) para toolchange, calibracion y recuperacion.

Implementa transiciones formales y auditables (patron FSM + Command). Cada
operacion fisica se identifica con un ``operation_id`` unico y registra el
ultimo paso confirmado, de modo que la operacion sea idempotente y segura ante
reenvios (informe 6.2, 13.5).

Requisitos: transicion < 1 ms, timeout configurable por fase.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set


class MMUState(str, Enum):
    IDLE = "IDLE"
    REQUESTED = "REQUESTED"
    VALIDATING = "VALIDATING"
    PREPARE = "PREPARE"
    UNLOAD = "UNLOAD"
    SELECT = "SELECT"
    LOAD = "LOAD"
    VERIFY = "VERIFY"
    PURGE = "PURGE"
    COMMIT = "COMMIT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RECOVERING = "RECOVERING"
    UNKNOWN = "UNKNOWN"


class TransitionError(Exception):
    """Transicion de estado no permitida."""


class PhaseTimeout(Exception):
    """Una fase excedio su timeout configurado."""


@dataclass
class ToolchangeResult:
    """Resultado de una operacion de cambio de herramienta."""

    success: bool
    state: str
    operation_id: str
    gate: int
    tool: int
    duration_ms: float = 0.0
    error_code: Optional[str] = None
    message: str = ""
    last_confirmed_step: str = MMUState.IDLE.value

    def as_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "state": self.state,
            "operation_id": self.operation_id,
            "gate": self.gate,
            "tool": self.tool,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "message": self.message,
            "last_confirmed_step": self.last_confirmed_step,
        }


# Catalogo de codigos de error (seccion 6.14 del informe).
ERR_INVALID_GATE = "ERR_INVALID_GATE"
ERR_INVALID_TOOL = "ERR_INVALID_TOOL"
ERR_UNLOAD_FAILED = "ERR_UNLOAD_FAILED"
ERR_SELECT_FAILED = "ERR_SELECT_FAILED"
ERR_LOAD_FAILED = "ERR_LOAD_FAILED"
ERR_VERIFY_FAILED = "ERR_VERIFY_FAILED"
ERR_PURGE_FAILED = "ERR_PURGE_FAILED"
ERR_PHASE_TIMEOUT = "ERR_PHASE_TIMEOUT"
ERR_INTERNAL = "ERR_INTERNAL"


class StateMachine:
    """FSM de toolchange. Orquesta los componentes via ``core``."""

    VALID_TRANSITIONS: Dict[MMUState, Set[MMUState]] = {
        MMUState.IDLE: {MMUState.REQUESTED, MMUState.RECOVERING, MMUState.UNKNOWN},
        MMUState.REQUESTED: {MMUState.VALIDATING, MMUState.FAILED, MMUState.IDLE},
        MMUState.VALIDATING: {MMUState.PREPARE, MMUState.FAILED},
        MMUState.PREPARE: {MMUState.UNLOAD, MMUState.SELECT, MMUState.FAILED},
        MMUState.UNLOAD: {MMUState.SELECT, MMUState.FAILED},
        MMUState.SELECT: {MMUState.LOAD, MMUState.FAILED},
        MMUState.LOAD: {MMUState.VERIFY, MMUState.FAILED},
        MMUState.VERIFY: {MMUState.PURGE, MMUState.COMMIT, MMUState.FAILED},
        MMUState.PURGE: {MMUState.COMMIT, MMUState.FAILED},
        MMUState.COMMIT: {MMUState.COMPLETED, MMUState.FAILED},
        MMUState.COMPLETED: {MMUState.IDLE, MMUState.REQUESTED},
        MMUState.FAILED: {MMUState.RECOVERING, MMUState.IDLE, MMUState.REQUESTED},
        MMUState.RECOVERING: {MMUState.IDLE, MMUState.COMPLETED, MMUState.FAILED},
        MMUState.UNKNOWN: {MMUState.IDLE, MMUState.RECOVERING},
    }

    DEFAULT_PHASE_TIMEOUTS: Dict[MMUState, float] = {
        MMUState.VALIDATING: 0.5,
        MMUState.PREPARE: 2.0,
        MMUState.UNLOAD: 20.0,
        MMUState.SELECT: 15.0,
        MMUState.LOAD: 20.0,
        MMUState.VERIFY: 5.0,
        MMUState.PURGE: 30.0,
        MMUState.COMMIT: 2.0,
    }

    def __init__(self, core: Any) -> None:
        self.core = core
        self.state: MMUState = MMUState.IDLE
        self.operation_id: Optional[str] = None
        self.attempt: int = 0
        self.last_confirmed_step: str = MMUState.IDLE.value
        self.last_error: Optional[str] = None
        self._history: list = []
        self.phase_timeouts = dict(self.DEFAULT_PHASE_TIMEOUTS)

    # -- Transiciones -------------------------------------------------------
    def transition(self, new_state: MMUState, operation_id: Optional[str] = None) -> None:
        """Ejecuta una transicion validada. Lanza ``TransitionError`` si no procede."""
        if new_state not in self.VALID_TRANSITIONS.get(self.state, set()):
            raise TransitionError(f"transicion invalida: {self.state.value} -> {new_state.value}")
        previous = self.state
        self.state = new_state
        if operation_id is not None:
            self.operation_id = operation_id
        self._history.append((time.time(), previous.value, new_state.value))
        self._emit("debug", "state_transition", from_state=previous.value, to_state=new_state.value)

    def get_state(self) -> str:
        return self.state.value

    def abort(self, reason: str) -> None:
        """Aborta la operacion y deja la FSM en FAILED."""
        self.last_error = reason
        self._emit("error", "operation_aborted", reason=reason)
        if MMUState.FAILED in self.VALID_TRANSITIONS.get(self.state, set()):
            self.transition(MMUState.FAILED)
        else:
            self.state = MMUState.FAILED

    # -- Operacion ----------------------------------------------------------
    def execute_toolchange(self, gate: int, tool: int) -> ToolchangeResult:
        """Ejecuta un cambio de herramienta completo de forma idempotente."""
        operation_id = f"tc-{uuid.uuid4().hex[:12]}"
        self.attempt += 1
        started = time.monotonic()
        self.last_error = None
        result = ToolchangeResult(
            success=False,
            state=self.state.value,
            operation_id=operation_id,
            gate=gate,
            tool=tool,
            last_confirmed_step=self.last_confirmed_step,
        )
        try:
            self.transition(MMUState.REQUESTED, operation_id)
            self._phase(MMUState.VALIDATING, lambda: self._validate(gate, tool), result)
            self._phase(MMUState.PREPARE, lambda: self._prepare(), result)
            if self._has_filament_loaded():
                self._phase(MMUState.UNLOAD, lambda: self._unload(), result)
            self._phase(MMUState.SELECT, lambda: self._select(gate), result)
            self._phase(MMUState.LOAD, lambda: self._load(), result)
            self._phase(MMUState.VERIFY, lambda: self._verify(), result)
            if self._should_purge():
                self._phase(MMUState.PURGE, lambda: self._purge(), result)
            self._phase(MMUState.COMMIT, lambda: self._commit(gate, tool), result)

            self.transition(MMUState.COMPLETED)
            result.success = True
            result.state = MMUState.COMPLETED.value
            result.last_confirmed_step = self.last_confirmed_step
        except _PhaseFailure as failure:
            result.error_code = failure.code
            result.message = failure.message
            result.last_confirmed_step = self.last_confirmed_step
            self.abort(f"{failure.code}: {failure.message}")
            result.state = MMUState.FAILED.value
        except TransitionError as exc:  # pragma: no cover - defensivo
            result.error_code = ERR_INTERNAL
            result.message = str(exc)
            self.state = MMUState.FAILED
        finally:
            result.duration_ms = round((time.monotonic() - started) * 1000.0, 3)
            self._emit(
                "info" if result.success else "error",
                "toolchange_finished",
                operation_id=operation_id,
                gate=gate,
                tool=tool,
                success=result.success,
                error_code=result.error_code,
                duration_ms=result.duration_ms,
            )
        return result

    # -- Fases --------------------------------------------------------------
    def _phase(self, state: MMUState, func: Callable[[], None], result: ToolchangeResult) -> None:
        self.transition(state)
        started = time.monotonic()
        func()
        elapsed = time.monotonic() - started
        timeout = self.phase_timeouts.get(state, 0.0)
        if timeout and elapsed > timeout:
            raise _PhaseFailure(ERR_PHASE_TIMEOUT, f"fase {state.value} excedio {timeout}s")
        self.last_confirmed_step = state.value
        self._persist(result)

    def _validate(self, gate: int, tool: int) -> None:
        profile = getattr(self.core, "profile", None)
        max_gates = profile.gates if profile is not None else 1
        if not isinstance(gate, int) or not 0 <= gate < max_gates:
            raise _PhaseFailure(ERR_INVALID_GATE, f"gate {gate} fuera de rango 0..{max_gates - 1}")
        if not isinstance(tool, int) or tool < 0:
            raise _PhaseFailure(ERR_INVALID_TOOL, f"tool {tool} invalido")

    def _prepare(self) -> None:
        motion = getattr(self.core, "motion", None)
        if motion is not None:
            motion.prepare()

    def _has_filament_loaded(self) -> bool:
        selector = getattr(self.core, "selector", None)
        current = getattr(self.core, "current_gate", None)
        return current is not None and selector is not None

    def _unload(self) -> None:
        motion = getattr(self.core, "motion", None)
        profile = getattr(self.core, "profile", None)
        if motion is None:
            return
        limits = profile.limits if profile is not None else {}
        distance = float(limits.get("max_distance_mm", 1000))
        speed = float(limits.get("max_unload_speed_mm_s", 80))
        if not motion.unload_filament(distance, speed):
            raise _PhaseFailure(ERR_UNLOAD_FAILED, "fallo al descargar filamento")

    def _select(self, gate: int) -> None:
        selector = getattr(self.core, "selector", None)
        if selector is None:
            return
        if not selector.select_gate(gate):
            raise _PhaseFailure(ERR_SELECT_FAILED, f"no se pudo seleccionar gate {gate}")

    def _load(self) -> None:
        motion = getattr(self.core, "motion", None)
        profile = getattr(self.core, "profile", None)
        if motion is None:
            return
        limits = profile.limits if profile is not None else {}
        distance = float(limits.get("max_distance_mm", 1000))
        speed = float(limits.get("max_load_speed_mm_s", 80))
        if not motion.load_filament(distance, speed):
            raise _PhaseFailure(ERR_LOAD_FAILED, "fallo al cargar filamento")

    def _verify(self) -> None:
        sensors = getattr(self.core, "sensors", None)
        if sensors is None:
            return
        if not sensors.is_present("toolhead"):
            raise _PhaseFailure(ERR_VERIFY_FAILED, "filamento no detectado en toolhead")

    def _should_purge(self) -> bool:
        return bool(getattr(self.core, "enable_purge", False))

    def _purge(self) -> None:
        motion = getattr(self.core, "motion", None)
        profile = getattr(self.core, "profile", None)
        if motion is None:
            return
        purge_length = float((profile.limits if profile else {}).get("bowden_length_mm", 25)) * 0.04
        if not motion.purge(max(1.0, purge_length)):
            raise _PhaseFailure(ERR_PURGE_FAILED, "fallo en purga")

    def _commit(self, gate: int, tool: int) -> None:
        self.core.current_gate = gate
        self.core.current_tool = tool
        persist = getattr(self.core, "persistence", None)
        if persist is not None:
            try:
                persist.save(self.core.snapshot_state())
            except Exception as exc:  # noqa: BLE001 - persistencia no bloqueante
                self._emit("warning", "persist_failed", error=str(exc))

    # -- Utilidades ---------------------------------------------------------
    def _persist(self, result: ToolchangeResult) -> None:
        self._emit(
            "debug",
            "phase_confirmed",
            operation_id=result.operation_id,
            step=self.last_confirmed_step,
        )

    def _emit(self, level: str, event: str, **kwargs: Any) -> None:
        diagnostics = getattr(self.core, "diagnostics", None)
        if diagnostics is not None:
            diagnostics.log_event(level, "state_machine", event, **kwargs)


class _PhaseFailure(Exception):
    """Fallo interno de una fase con codigo de error asociado."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


__all__ = [
    "MMUState",
    "StateMachine",
    "ToolchangeResult",
    "TransitionError",
    "PhaseTimeout",
    "ERR_INVALID_GATE",
    "ERR_INVALID_TOOL",
    "ERR_UNLOAD_FAILED",
    "ERR_SELECT_FAILED",
    "ERR_LOAD_FAILED",
    "ERR_VERIFY_FAILED",
    "ERR_PURGE_FAILED",
    "ERR_PHASE_TIMEOUT",
    "ERR_INTERNAL",
]
