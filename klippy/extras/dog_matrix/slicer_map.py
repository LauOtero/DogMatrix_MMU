"""Mapa de herramientas del slicer a gates fisicos del MMU (TTG maps).

Traduce los numeros de herramienta que emite el slicer (T0, T1, ...) a los
gates fisicos del MMU e implementa un automap por color/material. Estado puro
en memoria, determinista, sin bloqueo y sin dependencias de Klipper.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

SLICER_MAP_UNSET = -1

_HEX_LENGTHS = (3, 6, 8)
_HEX_DIGITS = "0123456789abcdef"


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    """Lee una clave de un ``dict`` o de un ConfigWrapper (``.get``)."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default)
    return default


def normalize_color(value: str) -> str:
    """Normaliza un color a hex sin ``#`` (minusculas) o texto en mayusculas.

    Los valores hex validos (3, 6 u 8 digitos) se devuelven en minusculas y sin
    ``#``; cualquier otro texto se devuelve en mayusculas y sin espacios.
    """
    stripped = str(value).replace("#", "").strip()
    lowered = stripped.lower()
    if len(lowered) in _HEX_LENGTHS and all(char in _HEX_DIGITS for char in lowered):
        return lowered
    return "".join(stripped.split()).upper()


def colors_match(a: str, b: str) -> bool:
    """Devuelve ``True`` si ambos colores normalizan al mismo valor no vacio."""
    norm_a = normalize_color(a)
    norm_b = normalize_color(b)
    return bool(norm_a) and norm_a == norm_b


def _norm_material(value: Optional[str]) -> str:
    if value is None:
        return ""
    return "".join(str(value).split()).upper()


class SlicerToolMap:
    """Mapa slicer-tool -> gate con automap por color y material."""

    def __init__(self, gates: int, config: Any = None) -> None:
        self._gates = max(0, int(gates))
        self._map: List[int] = list(range(self._gates))
        raw = _cfg_get(config, "slicer_tool_map", None)
        if raw is not None:
            self.set_map(raw)

    def set_map(self, mapping: List[int]) -> List[int]:
        """Normaliza y fija el mapa; entradas invalidas pasan a ``UNSET``."""
        if not isinstance(mapping, (list, tuple)):
            return list(self._map)
        normalized: List[int] = []
        for entry in mapping:
            try:
                gate = int(entry)
            except (TypeError, ValueError):
                gate = SLICER_MAP_UNSET
            if gate < 0 or gate >= self._gates:
                gate = SLICER_MAP_UNSET
            normalized.append(gate)
        self._map = normalized
        return list(self._map)

    def map_tool(self, slicer_tool: int) -> int:
        """Devuelve el gate de una herramienta o ``UNSET`` si esta fuera de rango."""
        index = int(slicer_tool)
        if 0 <= index < len(self._map):
            return self._map[index]
        return SLICER_MAP_UNSET

    def automap(
        self,
        tool_colors: Dict[int, str],
        gate_colors: List[str],
        tool_materials: Optional[Dict[int, str]] = None,
        gate_materials: Optional[List[str]] = None,
    ) -> Dict[int, int]:
        """Asigna gates por color/material (determinista, sin reusar gates)."""
        materials = tool_materials or {}
        gate_mats = gate_materials or []
        tools = sorted({int(tool) for tool in tool_colors} | {int(tool) for tool in materials})
        used: Set[int] = set()
        assigned: Dict[int, int] = {}

        def color_ok(tool: int, gate: int) -> bool:
            tool_color = tool_colors.get(tool)
            gate_color = gate_colors[gate] if gate < len(gate_colors) else None
            if tool_color is None or gate_color is None:
                return False
            if not colors_match(tool_color, gate_color):
                return False
            return self._material_ok(tool, gate, materials, gate_mats, require_match=False)

        for tool in tools:
            gate: Optional[int] = None
            for candidate in range(min(self._gates, len(gate_colors))):
                if candidate not in used and color_ok(tool, candidate):
                    gate = candidate
                    break
            if gate is None:
                for candidate in range(min(self._gates, len(gate_mats))):
                    if candidate not in used and self._material_ok(
                        tool, candidate, materials, gate_mats, require_match=True
                    ):
                        gate = candidate
                        break
            if gate is None and 0 <= tool < self._gates and tool not in used:
                gate = tool
            if gate is not None:
                used.add(gate)
                assigned[tool] = gate

        length = max(self._gates, (max(tools) + 1) if tools else 0)
        new_map = [SLICER_MAP_UNSET] * length
        for index in range(min(self._gates, length)):
            new_map[index] = index
        for tool in tools:
            new_map[tool] = assigned.get(tool, SLICER_MAP_UNSET)
        self._map = new_map
        return dict(assigned)

    def reset(self) -> None:
        """Vuelve al mapa identidad."""
        self._map = list(range(self._gates))

    def get_status(self) -> Dict[str, Any]:
        mapped = {str(index): gate for index, gate in enumerate(self._map) if gate != SLICER_MAP_UNSET}
        unmapped = [index for index, gate in enumerate(self._map) if gate == SLICER_MAP_UNSET]
        return {"map": list(self._map), "mapped": mapped, "unmapped": unmapped}

    def as_tool_map(self) -> List[int]:
        return list(self._map)

    @staticmethod
    def _material_ok(
        tool: int,
        gate: int,
        materials: Dict[int, str],
        gate_mats: List[str],
        require_match: bool,
    ) -> bool:
        tool_material = _norm_material(materials.get(tool))
        gate_material = _norm_material(gate_mats[gate]) if gate < len(gate_mats) else ""
        if not tool_material or not gate_material:
            return not require_match
        return tool_material == gate_material


__all__ = ["SLICER_MAP_UNSET", "SlicerToolMap", "normalize_color", "colors_match"]
