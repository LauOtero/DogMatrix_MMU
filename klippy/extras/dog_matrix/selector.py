"""Selector de gates con patron Strategy (lineal, rotativo, virtual).

La estrategia concreta se elige a partir de la topologia del perfil. Toda
estrategia cumple el mismo contrato, de modo que el resto del sistema ignora
el hardware subyacente (informe 6.5).

Precision objetivo: +-0.1 mm (lineal), +-0.5 grados (rotativo).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .motion import Motion


class SelectorError(Exception):
    """Fallo del selector de gates."""


class _SelectorStrategy:
    """Contrato comun de las estrategias de seleccion."""

    def __init__(self, motion: Motion, config: Dict[str, Any]) -> None:
        self.motion = motion
        self.config = config
        self.position: float = 0.0
        self.current_gate: Optional[int] = None

    def home(self) -> bool:
        self.position = 0.0
        self.current_gate = None
        return True

    def select_gate(self, gate: int) -> bool:
        raise NotImplementedError

    def get_position(self) -> float:
        return self.position

    def is_at_gate(self, gate: int) -> bool:
        return self.current_gate == gate

    def _target_position(self, gate: int) -> float:
        return float(gate)


class LinearSelector(_SelectorStrategy):
    """Selector lineal: posicion = gate * paso entre gates."""

    def select_gate(self, gate: int) -> bool:
        pitch = float(self.config.get("gate_pitch_mm", 22.5))
        self.position = self._target_position(gate) * pitch
        self.current_gate = gate
        return True


class RotarySelector(_SelectorStrategy):
    """Selector rotativo: posicion = gate * (360 / num_gates) grados."""

    def __init__(self, motion: Motion, config: Dict[str, Any], gates: int) -> None:
        super().__init__(motion, config)
        self.gates = max(1, int(gates))

    def select_gate(self, gate: int) -> bool:
        if not 0 <= gate < self.gates:
            return False
        self.position = (360.0 / self.gates) * gate
        self.current_gate = gate
        return True


class VirtualSelector(_SelectorStrategy):
    """Selector virtual: sin movimiento fisico (gear-per-gate / modular)."""

    def select_gate(self, gate: int) -> bool:
        self.current_gate = gate
        self.position = float(gate)
        return True


class Selector:
    """Fachada del selector. Selecciona la estrategia segun el perfil."""

    def __init__(
        self,
        printer: Any,
        config: Any,
        motion: Optional[Motion] = None,
        profile: Any = None,
    ) -> None:
        self.printer = printer
        self.config = config
        self.motion = motion
        self.topology_type = "gear_per_gate"
        self.selector_type = "virtual"
        self.gates = 1
        self._strategy: _SelectorStrategy = VirtualSelector(self.motion, {})
        self._load_profile(config, profile)

    def _load_profile(self, config: Any, profile: Any = None) -> None:
        """Lee topologia del perfil (recibido o resuelto via core)."""
        if profile is None and self.printer is not None:
            try:
                core = self.printer.lookup_object("dog_matrix", None)
                profile = getattr(core, "profile", None) if core is not None else None
            except Exception:  # noqa: BLE001 - core aun no registrado
                profile = None
        if profile is not None:
            self.topology_type = profile.topology_type
            self.selector_type = profile.selector_type
            self.gates = profile.gates
        strategy_config: Dict[str, Any] = dict(profile.topology) if profile is not None else {}
        self._strategy = self._build_strategy(strategy_config)

    def _build_strategy(self, cfg: Dict[str, Any]) -> _SelectorStrategy:
        if self.topology_type != "selector":
            return VirtualSelector(self.motion, cfg)
        if self.selector_type == "linear":
            return LinearSelector(self.motion, cfg)
        if self.selector_type == "rotary":
            return RotarySelector(self.motion, cfg, self.gates)
        return VirtualSelector(self.motion, cfg)

    # -- API publica --------------------------------------------------------
    def home(self) -> bool:
        return self._strategy.home()

    def select_gate(self, gate: int) -> bool:
        return self._strategy.select_gate(gate)

    def get_position(self) -> float:
        return self._strategy.get_position()

    def is_at_gate(self, gate: int) -> bool:
        return self._strategy.is_at_gate(gate)


__all__ = [
    "Selector",
    "SelectorError",
    "LinearSelector",
    "RotarySelector",
    "VirtualSelector",
]
