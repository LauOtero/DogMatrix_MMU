"""Formacion termica de punta y corte de filamento (tip forming / cutting).

Implementa la maquina de estados de cinco pasos que da forma de lanza a la
punta del filamento (ramming -> cooling -> skinnydip) y el corte opcional
mediante servo (cutter). Los movimientos se emiten como scripts de G-code a
traves de un emisor inyectado (``emit``), de modo que el modulo es
determinista, no bloqueante y testeable sin hardware.

Compatibilidad: conserva la API consumida por ``core`` (``start``,
``step_ramming``, ``step_cooling``, ``step_skinnydip``, ``abort``, ``state``,
``get_status``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# Estados de la formacion de punta
STATE_IDLE = "idle"
STATE_RAMMING = "ramming"
STATE_COOLING = "cooling"
STATE_SKINNYDIP = "skinnydip"
STATE_COMMITTED = "committed"
STATE_ERROR = "error"


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
class TipFormingResult:
    """Resultado de un ciclo de formacion de punta."""

    success: bool
    state: str
    step: str
    duration_ms: float = 0.0
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "state": self.state,
            "step": self.step,
            "duration_ms": self.duration_ms,
            "message": self.message,
        }


@dataclass
class TipFormingConfig:
    """Configuracion para la formacion de punta."""

    ramming_speed_mm_s: float = 30.0
    ramming_distance_mm: float = 15.0
    ramming_moves: int = 1
    cooling_moves: int = 3
    cooling_retract_mm: float = 2.0
    skinnydip_pressure_n: float = 5.0
    skinnydip_duration_s: float = 2.0
    skinnydip_distance_mm: float = 2.0
    use_cutter: bool = False
    cutter_servo_angle: float = 90.0
    cutter_servo: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ramming_speed_mm_s": self.ramming_speed_mm_s,
            "ramming_distance_mm": self.ramming_distance_mm,
            "ramming_moves": self.ramming_moves,
            "cooling_moves": self.cooling_moves,
            "cooling_retract_mm": self.cooling_retract_mm,
            "skinnydip_duration_s": self.skinnydip_duration_s,
            "use_cutter": self.use_cutter,
        }


class TipFormer:
    """Controlador de formacion termica de punta y corte."""

    def __init__(
        self,
        config: Any = None,
        profile: Any = None,
        emit: Optional[Callable[[str], None]] = None,
        servo: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self._emit_fn = emit
        self._servo_fn = servo
        self.state: str = STATE_IDLE
        self.current_step: str = ""
        self.start_time: float = 0.0
        self.cooling_index: int = 0
        self.errors: int = 0
        self.config_data: TipFormingConfig = self._load_config(config, profile)
        self.result: Optional[TipFormingResult] = None
        self.scripts: List[str] = []

    # -- Configuracion ------------------------------------------------------
    def _load_config(self, config: Any, profile: Any) -> TipFormingConfig:
        hardware = getattr(profile, "hardware", {}) if profile is not None else {}
        section = hardware.get("tip_forming", {}) if isinstance(hardware, dict) else {}
        get = lambda key, default: _cfg_get(config, key, section.get(key, default))  # noqa: E731
        return TipFormingConfig(
            ramming_speed_mm_s=float(get("tip_ramming_speed_mm_s", 30.0)),
            ramming_distance_mm=float(get("tip_ramming_distance_mm", 15.0)),
            ramming_moves=int(get("tip_ramming_moves", 1)),
            cooling_moves=int(get("tip_cooling_moves", 3)),
            cooling_retract_mm=float(get("tip_cooling_retract_mm", 2.0)),
            skinnydip_pressure_n=float(get("tip_skinnydip_pressure_n", 5.0)),
            skinnydip_duration_s=float(get("tip_skinnydip_duration_s", 2.0)),
            skinnydip_distance_mm=float(get("tip_skinnydip_distance_mm", 2.0)),
            use_cutter=bool(get("tip_use_cutter", False)),
            cutter_servo_angle=float(get("cutter_servo_angle", 90.0)),
            cutter_servo=str(get("cutter_servo", "")),
        )

    # -- Emision ------------------------------------------------------------
    def _emit(self, script: str) -> None:
        self.scripts.append(script)
        if self._emit_fn is not None:
            try:
                self._emit_fn(script)
            except Exception:  # noqa: BLE001 - el emisor nunca debe romper el ciclo
                pass

    def _move(self, distance_mm: float, speed_mm_s: float) -> None:
        feedrate = max(1.0, float(speed_mm_s)) * 60.0
        self._emit(f"G1 E{distance_mm:.4f} F{feedrate:.2f}")

    def _cut(self) -> None:
        if not self.config_data.use_cutter:
            return
        if self._servo_fn is not None:
            try:
                self._servo_fn(self.config_data.cutter_servo_angle)
                return
            except Exception:  # noqa: BLE001
                pass
        servo = self.config_data.cutter_servo
        if servo:
            self._emit(f"SET_SERVO SERVO={servo} ANGLE={self.config_data.cutter_servo_angle:.1f}")

    # -- Ciclo --------------------------------------------------------------
    def start(self) -> TipFormingResult:
        """Inicia ciclo de formacion de punta."""
        if self.state != STATE_IDLE:
            return TipFormingResult(
                success=False,
                state=self.state,
                step=self.current_step,
                message="Formacion ya en progreso",
            )
        self.state = STATE_RAMMING
        self.current_step = "ramming"
        self.start_time = time.monotonic()
        self.cooling_index = 0
        self.scripts = []
        self.result = TipFormingResult(success=False, state=self.state, step=self.current_step)
        return self.result

    def step_ramming(self) -> TipFormingResult:
        """Ejecuta la fase de ramming (empuje contra el filamento fundido)."""
        if self.state != STATE_RAMMING:
            return self._result_now()
        cfg = self.config_data
        for _ in range(max(1, cfg.ramming_moves)):
            self._move(cfg.ramming_distance_mm, cfg.ramming_speed_mm_s)
        self.state = STATE_COOLING
        self.current_step = "cooling"
        return self._partial("ramming")

    def step_cooling(self, move_idx: int = 0) -> TipFormingResult:
        """Ejecuta un movimiento de enfriamiento (retraccion corta)."""
        if self.state != STATE_COOLING:
            return self._result_now()
        self._move(-abs(self.config_data.cooling_retract_mm), self.config_data.ramming_speed_mm_s)
        self.cooling_index = int(move_idx) + 1
        if self.cooling_index >= max(1, self.config_data.cooling_moves):
            self.state = STATE_SKINNYDIP
            self.current_step = "skinnydip"
        return self._partial("cooling")

    def step_skinnydip(self) -> TipFormingResult:
        """Ejecuta la fase de skinnydip (presion suave) y corta si procede."""
        if self.state != STATE_SKINNYDIP:
            return self._result_now()
        self._move(self.config_data.skinnydip_distance_mm, self.config_data.ramming_speed_mm_s * 0.3)
        self._cut()
        return self._complete()

    def run(self) -> TipFormingResult:
        """Ejecuta la secuencia completa (start + pasos) sin bloquear."""
        result = self.start()
        if not result.success and self.state != STATE_RAMMING:
            return result
        self.step_ramming()
        for index in range(max(1, self.config_data.cooling_moves)):
            self.step_cooling(index)
        return self.step_skinnydip()

    # -- Resultados ---------------------------------------------------------
    def _partial(self, step: str) -> TipFormingResult:
        self.result = TipFormingResult(
            success=False, state=self.state, step=step,
            duration_ms=self._elapsed_ms(), message=f"paso {step} completado",
        )
        return self.result

    def _complete(self) -> TipFormingResult:
        duration = self._elapsed_ms()
        self.result = TipFormingResult(
            success=True, state=STATE_COMMITTED, step="skinnydip",
            duration_ms=duration, message="Formacion de punta completada",
        )
        self.state = STATE_IDLE
        self.current_step = ""
        return self.result

    def _result_now(self) -> TipFormingResult:
        if self.result is not None:
            return self.result
        return TipFormingResult(success=False, state=self.state, step=self.current_step)

    def _elapsed_ms(self) -> float:
        if not self.start_time:
            return 0.0
        return round((time.monotonic() - self.start_time) * 1000.0, 3)

    def abort(self, reason: str) -> TipFormingResult:
        """Aborta el ciclo actual dejando el modulo en reposo."""
        self.errors += 1
        self.result = TipFormingResult(
            success=False, state=STATE_ERROR, step=self.current_step,
            message=f"Formacion abortada: {reason}",
        )
        self.state = STATE_IDLE
        self.current_step = ""
        return self.result

    def get_status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "current_step": self.current_step,
            "cooling_index": self.cooling_index,
            "errors": self.errors,
            "result": self.result.as_dict() if self.result else None,
            "config": self.config_data.as_dict(),
        }


__all__ = [
    "TipFormer", "TipFormingResult", "TipFormingConfig",
    "STATE_IDLE", "STATE_RAMMING", "STATE_COOLING", "STATE_SKINNYDIP",
    "STATE_COMMITTED", "STATE_ERROR",
]
