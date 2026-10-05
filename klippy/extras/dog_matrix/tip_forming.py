"""Módulo de formación térmica de punta (tip forming).

Implementa la máquina de estados de cinco pasos para dar forma de lanza
a la punta del filamento, compatible con los perfiles de Happy Hare.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, List

from dataclasses import dataclass, field


# Estados de la formación de punta
STATE_IDLE = "idle"
STATE_RAMMING = "ramming"
STATE_COOLING = "cooling"
STATE_SKINNYDIP = "skinnydip"
STATE_COMMITTED = "committed"
STATE_ERROR = "error"


@dataclass
class TipFormingResult:
    """Resultado de un ciclo de formación de punta."""
    success: bool
    state: str
    step: str
    duration_ms: float = 0.0
    message: str = ""


@dataclass
class TipFormingConfig:
    """Configuración para la formación de punta."""
    ramming_speed_mm_s: float = 30.0
    ramming_distance_mm: float = 15.0
    cooling_moves: int = 3
    cooling_retract_mm: float = 2.0
    skinnydip_pressure_n: float = 5.0
    skinnydip_duration_s: float = 2.0


class TipFormer:
    """Controlador de formación térmica de punta."""

    def __init__(self, config: Any = None, profile: Any = None) -> None:
        self.config = config
        self.profile = profile
        self.state: str = STATE_IDLE
        self.current_step: str = ""
        self.start_time: float = 0.0
        self.config_data: TipFormingConfig = (
            TipFormingConfig() if config is None else self._load_config(config)
        )
        self.result: Optional[TipFormingResult] = None

    def _load_config(self, config: Any) -> TipFormingConfig:
        """Cargar configuración desde el perfil o config general."""
        # Leer valores del perfil YAML si están definidos
        # Por ahora, usar valores por defecto
        return TipFormingConfig()

    def start(self) -> TipFormingResult:
        """Iniciar ciclo de formación de punta."""
        if self.state != STATE_IDLE:
            return TipFormingResult(
                success=False,
                state=self.state,
                step=self.current_step,
                message="Formación already in progress",
            )

        self.state = STATE_RAMMING
        self.current_step = "ramming"
        self.start_time = time.monotonic()
        self.result = TipFormingResult(success=False, state=self.state, step=self.current_step)
        return self.result

    def step_ramming(self) -> TipFormingResult:
        """Ejecutar fase de ramming."""
        # Simulación: en implementación real, movería toolhead contra cutter
        duration = 2000  # ms simulado
        self._advance_step()
        return self.result

    def step_cooling(self, move_idx: int = 0) -> TipFormingResult:
        """Ejecutar fase de enfriamiento."""
        # Moves de enfriamiento para asentar la punta
        duration = 1000  # ms simulado por move
        self._advance_step()
        return self.result

    def step_skinnydip(self) -> TipFormingResult:
        """Ejecutar fase de skinnydip (presión suave)."""
        # Presión suave para dar forma final
        duration = 1500  # ms simulado
        self._complete_step()
        return self.result

    def _advance_step(self) -> None:
        """Avanzar al siguiente paso de la FSM."""
        if self.current_step == "ramming":
            self.current_step = "cooling"
        elif self.current_step.startswith("cooling"):
            # Multiple cooling moves, then skinnydip
            if self.current_step == "cooling":
                self.current_step = "skinnydip"
            else:
                self._complete_step()

    def _complete_step(self) -> None:
        """Completar el ciclo actual."""
        self.state = STATE_COMMITTED
        duration = int((time.monotonic() - self.start_time) * 1000)
        self.result = TipFormingResult(
            success=True,
            state=self.state,
            step=self.current_step,
            duration_ms=duration,
            message="Formación de punta completada",
        )
        self.state = STATE_IDLE
        self.current_step = ""

    def abort(self, reason: str) -> None:
        """Abortar ciclo actual."""
        self.state = STATE_ERROR
        self.current_step = ""
        self.result = TipFormingResult(
            success=False,
            state=self.state,
            step=self.current_step,
            message=f"Formación abortada: {reason}",
        )
        self.state = STATE_IDLE
        self.current_step = ""

    def get_status(self) -> Dict[str, Any]:
        """Obtener estado actual."""
        return {
            "state": self.state,
            "current_step": self.current_step,
            "result": self.result.as_dict() if self.result else None,
            "config": {
                "ramming_speed_mm_s": self.config_data.ramming_speed_mm_s,
                "ramming_distance_mm": self.config_data.ramming_distance_mm,
                "cooling_moves": self.config_data.cooling_moves,
                "skinnydip_duration_s": self.config_data.skinnydip_duration_s,
            },
        }


__all__ = ["TipFormer", "TipFormingResult", "TipFormingConfig", "STATE_IDLE", "STATE_RAMMING", "STATE_COOLING", "STATE_SKINNYDIP", "STATE_COMMITTED", "STATE_ERROR"]