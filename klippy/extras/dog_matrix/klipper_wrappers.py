"""Envoltorios finos sobre objetos de Klipper (paridad Happy Hare).

Expone ``ToolheadWrapper`` y ``ExtruderWrapper`` como adaptadores defensivos
sobre ``toolhead``/``extruder``/``heater`` inyectados, sin importar Klipper. Las
factorias ``build_toolhead``/``build_extruder`` resuelven los objetos desde el
``printer`` y degradan de forma segura ante cualquier fallo de ``lookup_object``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


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


def _lookup(printer: Any, name: str) -> Any:
    """Resuelve un objeto desde ``printer.lookup_object`` de forma defensiva."""
    if printer is None:
        return None
    lookup = getattr(printer, "lookup_object", None)
    if not callable(lookup):
        return None
    try:
        return lookup(name, None)
    except Exception:  # noqa: BLE001 - printer opcional
        return None


class ToolheadWrapper:
    """Adaptador defensivo sobre el objeto ``toolhead`` de Klipper."""

    def __init__(self, toolhead: Any = None, gcode: Any = None) -> None:
        self._toolhead = toolhead
        self._gcode = gcode

    def position(self) -> Optional[Tuple[float, float, float, float]]:
        """Posicion actual ``(x, y, z, e)`` o ``None`` si no esta disponible."""
        get_position = getattr(self._toolhead, "get_position", None)
        if not callable(get_position):
            return None
        try:
            pos = get_position()
            return (float(pos[0]), float(pos[1]), float(pos[2]), float(pos[3]))
        except Exception:  # noqa: BLE001 - toolhead opcional
            return None

    def is_homed(self) -> bool:
        """Indica si el toolhead esta homed; ``False`` por defecto."""
        toolhead = self._toolhead
        if toolhead is None:
            return False
        get_status = getattr(toolhead, "get_status", None)
        if callable(get_status):
            status = None
            try:
                status = get_status(0.0)
            except TypeError:
                try:
                    status = get_status()
                except Exception:  # noqa: BLE001
                    status = None
            except Exception:  # noqa: BLE001
                status = None
            if isinstance(status, dict):
                homed = status.get("homed_axes")
                if isinstance(homed, str):
                    return len(homed) > 0
                if homed is not None:
                    return bool(homed)
        attr = getattr(toolhead, "is_homed", None)
        if callable(attr):
            try:
                return bool(attr())
            except Exception:  # noqa: BLE001
                return False
        return False

    def wait_moves(self) -> None:
        """Espera a que terminen los movimientos en curso si es posible."""
        wait = getattr(self._toolhead, "wait_moves", None)
        if not callable(wait):
            return
        try:
            wait()
        except Exception:  # noqa: BLE001 - toolhead opcional
            return


class ExtruderWrapper:
    """Adaptador defensivo sobre el objeto ``extruder`` de Klipper."""

    def __init__(self, extruder: Any = None, heater: Any = None) -> None:
        self._extruder = extruder
        self._heater = heater

    def position_mm(self) -> float:
        """Posicion del eje E en mm; ``0.0`` si no esta disponible."""
        get_position = getattr(self._extruder, "get_position", None)
        if not callable(get_position):
            return 0.0
        try:
            return float(get_position()[3])
        except Exception:  # noqa: BLE001 - extruder opcional
            return 0.0

    def temperature(self) -> float:
        """Temperatura actual del heater; ``0.0`` si no esta disponible."""
        get_temp = getattr(self._heater, "get_temp", None)
        if not callable(get_temp):
            return 0.0
        try:
            temp = get_temp()
            if isinstance(temp, (tuple, list)):
                temp = temp[0]
            return float(temp)
        except Exception:  # noqa: BLE001 - heater opcional
            return 0.0


def build_toolhead(printer: Any) -> ToolheadWrapper:
    """Construye un ``ToolheadWrapper`` resolviendo ``toolhead`` del printer."""
    return ToolheadWrapper(toolhead=_lookup(printer, "toolhead"))


def build_extruder(printer: Any) -> ExtruderWrapper:
    """Construye un ``ExtruderWrapper`` resolviendo ``extruder`` del printer."""
    return ExtruderWrapper(extruder=_lookup(printer, "extruder"))


__all__ = ["ToolheadWrapper", "ExtruderWrapper", "build_toolhead", "build_extruder"]
