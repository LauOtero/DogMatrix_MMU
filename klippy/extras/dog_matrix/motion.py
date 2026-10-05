"""Planificacion de movimiento para carga, descarga y posicionamiento.

Python valida parametros y orquesta; el calculo del perfil de velocidad se
delega a la capa nativa (CFFI/chelper) para determinismo temporal (informe 6.4).
La interpolacion final la realiza el MCU de Klipper.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from . import _native


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
        # Parking por operacion + z-hop (paridad Happy Hare Toolchange-Movement).
        self.z_hop_mm: float = float(_cfg_get(config, "z_hop_mm", 1.0))
        self.z_hop_speed_mm_s: float = float(_cfg_get(config, "z_hop_speed_mm_s", 20.0))
        self.park_positions: Dict[str, List[float]] = self._load_park_positions(config)

    @staticmethod
    def _load_park_positions(config: Any) -> Dict[str, List[float]]:
        raw = _cfg_get(config, "park_positions", None)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = None
        result: Dict[str, List[float]] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    result[str(key)] = [float(v) for v in value]
        return result

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

    def park_toolhead(self, operation: Optional[str] = None) -> bool:
        """Parquea el toolhead en la posicion configurada para la operacion.

        Si no hay posicion configurada, recurre a la macro estandar ``G27``.
        """
        position = None
        if operation and operation in self.park_positions:
            position = self.park_positions[operation]
        elif "default" in self.park_positions:
            position = self.park_positions["default"]
        if position is not None:
            x, y = position[0], position[1]
            parts = [f"G1 X{x:.3f} Y{y:.3f}"]
            if len(position) >= 3:
                parts.append(f"Z{position[2]:.3f}")
            parts.append(f"F{max(1.0, self.z_hop_speed_mm_s) * 60:.2f}")
            try:
                self._run(" ".join(parts))
                return True
            except GearError:
                return False
        try:
            self._run("G27")  # park toolhead (macro estandar)
            return True
        except GearError:
            return False

    def z_hop(self, height_mm: Optional[float] = None, speed_mm_s: Optional[float] = None) -> bool:
        """Realiza un z-hop (subida) relativo antes de una operacion."""
        height = self.z_hop_mm if height_mm is None else float(height_mm)
        speed = self.z_hop_speed_mm_s if speed_mm_s is None else float(speed_mm_s)
        if height <= 0:
            return True
        try:
            self._run(f"G91")
            self._run(f"G1 Z{height:.3f} F{max(1.0, speed) * 60:.2f}")
            self._run("G90")
            return True
        except GearError:
            return False

    def z_hop_down(self, height_mm: Optional[float] = None, speed_mm_s: Optional[float] = None) -> bool:
        """Deshace un z-hop previo (bajada relativa)."""
        height = self.z_hop_mm if height_mm is None else float(height_mm)
        speed = self.z_hop_speed_mm_s if speed_mm_s is None else float(speed_mm_s)
        if height <= 0:
            return True
        try:
            self._run(f"G91")
            self._run(f"G1 Z-{height:.3f} F{max(1.0, speed) * 60:.2f}")
            self._run("G90")
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
            "z_hop_mm": self.z_hop_mm,
            "park_positions": dict(self.park_positions),
            "native": _native.native_available(),
            "timestamp": time.time(),
        }

    def set_jerk_limit(self, jerk_mm_s3: float) -> None:
        self.jerk_limit = max(0.0, float(jerk_mm_s3))

    def enable_s_curve(self, enabled: bool) -> None:
        self.s_curve = bool(enabled)


__all__ = ["Motion", "GearError"]
