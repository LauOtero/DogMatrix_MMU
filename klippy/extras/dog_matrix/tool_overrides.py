"""Overrides de parametros por herramienta (paridad Happy Hare ``MMU_TOOL_OVERRIDES``).

Permite ajustar por herramienta el factor de velocidad, la purga, la
temperatura, el material/color y campos extra arbitrarios. Estado puro en
memoria, determinista, sin bloqueo y sin dependencias de Klipper; serializable
para persistencia (``as_dict``/``load``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_KNOWN_FIELDS = ("speed_factor", "purge_mm", "temperature", "material", "color")


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


@dataclass
class ToolOverride:
    tool: int
    speed_factor: float = 1.0
    purge_mm: float = 0.0
    temperature: int = 0
    material: str = ""
    color: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "speed_factor": self.speed_factor,
            "purge_mm": self.purge_mm,
            "temperature": self.temperature,
            "material": self.material,
            "color": self.color,
            "extra": dict(self.extra),
        }


class ToolOverrideStore:
    """Almacen de overrides por herramienta (determinista, sin E/S)."""

    def __init__(self, config: Any = None) -> None:
        self._overrides: Dict[int, ToolOverride] = {}
        raw = _cfg_get(config, "tool_overrides", None)
        if isinstance(raw, dict):
            self.load(raw)

    # -- Definicion ---------------------------------------------------------
    def set_override(self, tool: int, **fields: Any) -> ToolOverride:
        """Crea o actualiza el override de una herramienta.

        Los campos conocidos se normalizan a su tipo; los desconocidos se
        conservan en ``extra``. Los valores que no se pueden convertir dejan
        intacto el campo correspondiente.
        """
        override = self.get(int(tool))
        if override is None:
            override = ToolOverride(tool=int(tool))
            self._overrides[int(tool)] = override
        for key, value in fields.items():
            if key == "extra":
                if isinstance(value, dict):
                    override.extra.update(value)
                continue
            if key == "tool":
                continue
            if key in _KNOWN_FIELDS:
                _assign_field(override, key, value)
            else:
                override.extra[key] = value
        return override

    def get(self, tool: int) -> Optional[ToolOverride]:
        return self._overrides.get(int(tool))

    def clear(self, tool: Optional[int] = None) -> None:
        """Borra el override de una herramienta o todos si ``tool`` es ``None``."""
        if tool is None:
            self._overrides.clear()
        else:
            self._overrides.pop(int(tool), None)

    # -- Aplicacion ---------------------------------------------------------
    def apply_speed(self, tool: int, speed_mm_s: float) -> float:
        override = self.get(tool)
        if override is None:
            return float(speed_mm_s)
        return float(speed_mm_s) * override.speed_factor

    def apply_purge(self, tool: int, purge_mm: float) -> float:
        override = self.get(tool)
        if override is not None and override.purge_mm > 0:
            return override.purge_mm
        return float(purge_mm)

    def apply_temperature(self, tool: int, temperature: int) -> int:
        override = self.get(tool)
        if override is not None and override.temperature > 0:
            return override.temperature
        return int(temperature)

    # -- Serializacion ------------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {str(tool): override.as_dict() for tool, override in sorted(self._overrides.items())}

    def load(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        for key, payload in data.items():
            if not isinstance(payload, dict):
                continue
            try:
                tool = int(key)
            except (TypeError, ValueError):
                continue
            fields = {name: value for name, value in payload.items() if name != "tool"}
            self.set_override(tool, **fields)

    def get_status(self) -> Dict[str, Any]:
        return {"count": len(self._overrides), "overrides": self.as_dict()}


def _assign_field(override: ToolOverride, key: str, value: Any) -> None:
    """Normaliza y asigna un campo conocido, ignorando valores invalidos."""
    try:
        if key == "speed_factor":
            override.speed_factor = float(value)
        elif key == "purge_mm":
            override.purge_mm = float(value)
        elif key == "temperature":
            override.temperature = int(value)
        elif key == "material":
            override.material = str(value)
        elif key == "color":
            override.color = str(value)
    except (TypeError, ValueError):
        pass


__all__ = ["ToolOverride", "ToolOverrideStore"]
