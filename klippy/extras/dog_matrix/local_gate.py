"""Modo *local gate* para Dog Matrix MMU (paridad Happy Hare).

Permite consumir filamento desde un gate por *bypass*/local sin usar el MMU,
util cuando el filamento se alimenta a mano. Solo se mantiene el estado de que
gates son locales y cual esta activo; la logica de movimiento es responsabilidad
de otros modulos.

Diseno: autocontenido (no importa Klipper), deterministico y sin bloqueo.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


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


def _as_gate_list(raw: Any) -> List[int]:
    """Normaliza una lista de gates a enteros, ignorando entradas invalidas."""
    if not isinstance(raw, (list, tuple, set)):
        return []
    gates: List[int] = []
    for value in raw:
        try:
            gate = int(value)
        except (TypeError, ValueError):
            continue
        if gate >= 0 and gate not in gates:
            gates.append(gate)
    return gates


class LocalGate:
    """Estado del modo local gate (bypass sin MMU)."""

    def __init__(self, config: Any = None) -> None:
        self.enabled: bool = bool(_cfg_get(config, "local_gate_enabled", False))
        self.gates: List[int] = _as_gate_list(_cfg_get(config, "local_gate_gates", []))
        self.active_gate: Optional[int] = None

    def enable(self) -> None:
        """Habilita el modo local gate."""
        self.enabled = True

    def disable(self) -> None:
        """Deshabilita el modo local gate y limpia el gate activo."""
        self.enabled = False
        self.active_gate = None

    def is_enabled(self) -> bool:
        return self.enabled

    def is_local(self, gate: int) -> bool:
        """True si ``gate`` esta declarado como local."""
        try:
            return int(gate) in self.gates
        except (TypeError, ValueError):
            return False

    def select(self, gate: int) -> bool:
        """Activa el modo local para ``gate``; False si no esta habilitado."""
        if not self.enabled:
            return False
        try:
            self.active_gate = int(gate)
        except (TypeError, ValueError):
            return False
        return True

    def reset(self) -> None:
        """Limpia el gate activo sin cambiar el estado de habilitacion."""
        self.active_gate = None

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "active_gate": self.active_gate,
            "gates": list(self.gates),
        }


def build_local_gate(config: Any = None) -> LocalGate:
    """Construye un ``LocalGate`` a partir de la configuracion."""
    return LocalGate(config)


__all__ = ["LocalGate", "build_local_gate"]
