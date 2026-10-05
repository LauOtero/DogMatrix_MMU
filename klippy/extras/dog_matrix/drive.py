"""Abstraccion unificada de un motor de arrastre (paridad Happy Hare ``mmu_drive``).

Modela cualquier actuador de movimiento del MMU (gear, selector, cutter,
espooler) con una interfaz homogenea de arranque/parada. El actuador de hardware
se inyecta como ``set_speed``/``stop_fn`` para permitir pruebas sin dependencias
de Klipper; si no se inyecta, ``move`` degrada de forma segura devolviendo
``False``. Es determinista, no bloquea y no lanza excepciones hacia el llamante.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

DRIVE_GEAR = "gear"
DRIVE_SELECTOR = "selector"
DRIVE_CUTTER = "cutter"
DRIVE_ESPOOLER = "espooler"


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


class Drive:
    """Motor de arrastre generico con movimiento y parada."""

    def __init__(
        self,
        kind: str = DRIVE_GEAR,
        set_speed: Optional[Callable[[float], None]] = None,
        stop_fn: Optional[Callable[[], None]] = None,
        name: str = "gear",
    ) -> None:
        self.kind = kind
        self.name = name
        self._set_speed = set_speed
        self._stop_fn = stop_fn
        self._moving = False
        self._last_distance_mm = 0.0
        self._last_speed_mm_s = 0.0
        self.errors = 0

    def move(self, distance_mm: float, speed_mm_s: float) -> bool:
        """Arranca el motor firmando la velocidad segun la distancia.

        Devuelve ``False`` si no hay actuador o si este falla; nunca lanza.
        """
        try:
            distance = float(distance_mm)
            speed = float(speed_mm_s)
        except (TypeError, ValueError):
            return False
        if self._set_speed is None:
            return False
        signed_speed = speed if distance >= 0 else -speed
        try:
            self._set_speed(signed_speed)
        except Exception:  # noqa: BLE001 - hardware opcional
            self.errors += 1
            self._moving = False
            return False
        self._last_distance_mm = distance
        self._last_speed_mm_s = speed
        self._moving = speed != 0.0
        return True

    def stop(self) -> None:
        """Detiene el motor (usa ``stop_fn`` o velocidad cero como respaldo)."""
        if self._stop_fn is not None:
            try:
                self._stop_fn()
            except Exception:  # noqa: BLE001 - hardware opcional
                self.errors += 1
        elif self._set_speed is not None:
            try:
                self._set_speed(0.0)
            except Exception:  # noqa: BLE001 - hardware opcional
                self.errors += 1
        self._moving = False

    def is_moving(self) -> bool:
        """Indica si el motor esta en movimiento."""
        return self._moving

    def get_status(self) -> Dict[str, Any]:
        """Estado observable del motor."""
        return {
            "kind": self.kind,
            "name": self.name,
            "moving": self._moving,
            "last_distance_mm": self._last_distance_mm,
            "last_speed_mm_s": self._last_speed_mm_s,
        }


class DriveManager:
    """Registro de motores de arrastre reutilizados por ``(kind, index)``."""

    def __init__(self, factory: Optional[Callable[[str], Drive]] = None) -> None:
        self._factory = factory
        self._drives: Dict[Tuple[str, int], Drive] = {}

    def get(self, kind: str, index: int = 0) -> Drive:
        """Crea o reutiliza el motor identificado por ``(kind, index)``."""
        key = (kind, int(index))
        drive = self._drives.get(key)
        if drive is None:
            if self._factory is not None:
                drive = self._factory(kind)
            else:
                name = kind if key[1] == 0 else f"{kind}_{key[1]}"
                drive = Drive(kind=kind, name=name)
            self._drives[key] = drive
        return drive

    def stop_all(self) -> None:
        """Detiene todos los motores registrados."""
        for drive in self._drives.values():
            drive.stop()

    def get_status(self) -> Dict[str, Any]:
        """Estado agregado de todos los motores."""
        drives = {
            f"{kind}:{index}": drive.get_status()
            for (kind, index), drive in self._drives.items()
        }
        return {"count": len(drives), "drives": drives}


__all__ = ["Drive", "DriveManager", "DRIVE_GEAR", "DRIVE_SELECTOR",
           "DRIVE_CUTTER", "DRIVE_ESPOOLER"]
