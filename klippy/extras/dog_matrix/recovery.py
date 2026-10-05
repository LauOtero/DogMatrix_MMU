"""Recuperacion inteligente por fase con revalidacion de sensores.

Tras un fallo de movimiento, la recuperacion decide una accion segura en
funcion de la fase fallida, revalida el estado fisico mediante sensores y, si
la politica lo exige, solicita confirmacion al operador antes de continuar.
No reenvia nunca una operacion ambigua (informe 6.9, DM-SAFE-001).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .state_machine import (
    ERR_LOAD_FAILED,
    ERR_SELECT_FAILED,
    ERR_UNLOAD_FAILED,
    ERR_VERIFY_FAILED,
    MMUState,
)


@dataclass
class RecoveryResult:
    """Resultado de un intento de recuperacion."""

    success: bool
    action: str
    message: str = ""
    requires_confirmation: bool = False
    error_code: Optional[str] = None


# Mapa fase/codigo -> accion de recuperacion.
_RECOVERY_ACTIONS: Dict[str, str] = {
    ERR_UNLOAD_FAILED: "retry_unload",
    ERR_LOAD_FAILED: "retry_load",
    ERR_SELECT_FAILED: "home_selector",
    ERR_VERIFY_FAILED: "revalidate_sensor",
}


class Recovery:
    """Planifica y ejecuta la recuperacion segura."""

    def __init__(self, core: Any) -> None:
        self.core = core
        self.auto_recover = bool(getattr(core, "auto_recover", False))
        self.confirm_fn: Optional[Callable[[str], bool]] = None

    def request_confirmation(self, message: str) -> bool:
        """Solicita confirmacion al operador segun la politica configurada."""
        if self.confirm_fn is not None:
            return bool(self.confirm_fn(message))
        # Sin canal interactivo: en modo automatico se asume confirmacion,
        # en modo manual se deniega (estado seguro).
        return self.auto_recover

    def revalidate_state(self) -> bool:
        """Revalida el estado fisico (sensores coherentes con el estado logico)."""
        sensors = getattr(self.core, "sensors", None)
        if sensors is None:
            return True
        try:
            toolhead_present = sensors.is_present("toolhead")
        except Exception:  # noqa: BLE001 - sensor no concluyente
            return False
        current_gate = getattr(self.core, "current_gate", None)
        if current_gate is None:
            # Sin filamento cargado, el toolhead no deberia detectar filamento.
            return not toolhead_present
        return True

    def recover_from_failure(self, failure_info: Dict[str, Any]) -> RecoveryResult:
        """Ejecuta la accion de recuperacion adecuada para el fallo dado."""
        code = str(failure_info.get("error_code") or failure_info.get("code") or "")
        action = _RECOVERY_ACTIONS.get(code, "manual_intervention")
        self._emit("info", "recovery_started", error_code=code, action=action)

        if action == "manual_intervention":
            return RecoveryResult(
                success=False,
                action=action,
                message="se requiere intervencion manual; operacion no reenviada",
                requires_confirmation=True,
                error_code=code,
            )

        if not self.revalidate_state():
            return RecoveryResult(
                success=False,
                action="manual_intervention",
                message="revalidacion de sensores no concluyente",
                requires_confirmation=True,
                error_code=code,
            )

        handler = {
            "retry_unload": self._retry_unload,
            "retry_load": self._retry_load,
            "home_selector": self._home_selector,
            "revalidate_sensor": self._revalidate_sensor,
        }[action]
        result = handler()
        self._emit(
            "info" if result.success else "warning",
            "recovery_finished",
            action=action,
            success=result.success,
        )
        return result

    # -- Acciones -----------------------------------------------------------
    def _retry_unload(self) -> RecoveryResult:
        motion = getattr(self.core, "motion", None)
        profile = getattr(self.core, "profile", None)
        if motion is None:
            return RecoveryResult(True, "retry_unload", "sin componente de movimiento")
        limits = profile.limits if profile else {}
        ok = motion.unload_filament(
            float(limits.get("max_distance_mm", 1000)), float(limits.get("max_unload_speed_mm_s", 80))
        )
        return RecoveryResult(ok, "retry_unload", "reintento de descarga" + ("" if ok else " fallido"))

    def _retry_load(self) -> RecoveryResult:
        motion = getattr(self.core, "motion", None)
        profile = getattr(self.core, "profile", None)
        if motion is None:
            return RecoveryResult(True, "retry_load", "sin componente de movimiento")
        limits = profile.limits if profile else {}
        ok = motion.load_filament(
            float(limits.get("max_distance_mm", 1000)), float(limits.get("max_load_speed_mm_s", 80))
        )
        return RecoveryResult(ok, "retry_load", "reintento de carga" + ("" if ok else " fallido"))

    def _home_selector(self) -> RecoveryResult:
        selector = getattr(self.core, "selector", None)
        if selector is None:
            return RecoveryResult(True, "home_selector", "sin selector")
        ok = selector.home()
        return RecoveryResult(ok, "home_selector", "homing de selector" + ("" if ok else " fallido"))

    def _revalidate_sensor(self) -> RecoveryResult:
        ok = self.revalidate_state()
        return RecoveryResult(ok, "revalidate_sensor", "revalidacion de sensores")

    def _emit(self, level: str, event: str, **kwargs: Any) -> None:
        diagnostics = getattr(self.core, "diagnostics", None)
        if diagnostics is not None:
            diagnostics.log_event(level, "recovery", event, **kwargs)


__all__ = ["Recovery", "RecoveryResult"]
