"""Planificacion de movimiento para carga, descarga y posicionamiento.

Python valida parametros y orquesta; el calculo del perfil de velocidad se
delega a la capa nativa (CFFI/chelper) para determinismo temporal (informe 6.4).
La interpolacion final la realiza el MCU de Klipper.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from . import _native


class GearError(Exception):
    """Fallo en el movimiento del motor de arrastre."""


class Motion:
    """Emite comandos de movimiento hacia Klipper con limites del perfil."""

    def __init__(self, printer: Any, config: Any) -> None:
        self.printer = printer
        self.config = config
        self.s_curve = False
        self.jerk_limit: float = 0.0
        self.last_plan: Dict[str, float] = {}
        self.moves = 0
        self._watchdog_synced = False

    # -- Utilidades Klipper -------------------------------------------------
    def _gcode(self) -> Any:
        if self.printer is None:
            return None
        try:
            return self.printer.lookup_object("gcode", None)
        except Exception:  # noqa: BLE001
            return None

    def _run(self, script: str) -> None:
        gcode = self._gcode()
        if gcode is None:
            return
        try:
            gcode.run_script_from_command(script)
        except Exception as exc:  # noqa: BLE001 - propagar como GearError
            raise GearError(str(exc)) from exc

    def _emit_move(self, distance_mm: float, speed_mm_s: float) -> bool:
        feedrate = max(1.0, speed_mm_s) * 60.0
        try:
            self._run(f"G1 E{distance_mm:.4f} F{feedrate:.2f}")
            self.moves += 1
            return True
        except GearError:
            return False

    # -- API publica --------------------------------------------------------
    def prepare(self) -> None:
        """Prepara el subsistema de movimiento (sincroniza watchdog si procede)."""
        self._watchdog_synced = False

    def load_filament(self, distance_mm: float, speed_mm_s: float) -> bool:
        self.last_plan = _native.plan_trajectory(
            distance_mm, speed_mm_s, accel_mm_s2=2000.0, jerk_mm_s3=self.jerk_limit, s_curve=self.s_curve
        )
        return self._emit_move(abs(distance_mm), speed_mm_s)

    def unload_filament(self, distance_mm: float, speed_mm_s: float) -> bool:
        self.last_plan = _native.plan_trajectory(
            distance_mm, speed_mm_s, accel_mm_s2=2000.0, jerk_mm_s3=self.jerk_limit, s_curve=self.s_curve
        )
        return self._emit_move(-abs(distance_mm), speed_mm_s)

    def move_selector(self, position: float) -> bool:
        """El desplazamiento del selector lo ejecuta la estrategia activa.

        Se mantiene en ``Motion`` como punto unico de emision para trazabilidad.
        """
        return True

    def park_toolhead(self) -> bool:
        try:
            self._run("G27")  # park toolhead (macro estandar)
            return True
        except GearError:
            return False

    def purge(self, length_mm: float) -> bool:
        return self._emit_move(abs(length_mm), 5.0)

    def get_trajectory_stats(self) -> Dict[str, Any]:
        return {
            "moves": self.moves,
            "last_plan": dict(self.last_plan),
            "s_curve": self.s_curve,
            "jerk_limit": self.jerk_limit,
            "native": _native.native_available(),
            "timestamp": time.time(),
        }

    def set_jerk_limit(self, jerk_mm_s3: float) -> None:
        self.jerk_limit = max(0.0, float(jerk_mm_s3))

    def enable_s_curve(self, enabled: bool) -> None:
        self.s_curve = bool(enabled)


__all__ = ["Motion", "GearError"]
