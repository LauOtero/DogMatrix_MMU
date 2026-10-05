"""Soporte del escaner TD-1 (transmission distance / densidad optica).

Equivalente a la funcion Feature-TD1 de Happy Hare: lee el valor TD del
filamento (y opcionalmente su color) para asociarlo a un gate del MMU. El
dispositivo fisico se inyecta como ``reader`` (callable) para permitir pruebas
y banco sin hardware real.

Integracion: los valores TD y color se exponen via ``get_status`` y pueden
consumirse desde el core para mapear gates a materiales.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


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


# Paleta determinista TD -> (hex, nombre). TD bajo implica filamento mas opaco
# (oscuro); TD alto, mas translucido (claro). No pretende precision real.
TD_COLOR_PALETTE: Tuple[Tuple[float, str, str], ...] = (
    (0.0, "#1A1A1A", "negro"),
    (0.3, "#4A4A4A", "gris oscuro"),
    (0.6, "#8C8C8C", "gris"),
    (0.9, "#D9D9D9", "gris claro"),
    (1.2, "#FFFFFF", "blanco"),
)


def td_to_color(td: float) -> Tuple[str, str]:
    """Mapea un valor TD a ``(hex, nombre)`` de color de la paleta."""
    value = float(td)
    hex_color, name = TD_COLOR_PALETTE[0][1], TD_COLOR_PALETTE[0][2]
    for threshold, candidate_hex, candidate_name in TD_COLOR_PALETTE:
        if value >= threshold:
            hex_color, name = candidate_hex, candidate_name
        else:
            break
    return hex_color, name


@dataclass
class TD1Reading:
    """Lectura del escaner TD-1 para un filamento."""

    td: float
    color_hex: str = ""
    color_name: str = ""
    timestamp: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "td": self.td,
            "color_hex": self.color_hex,
            "color_name": self.color_name,
            "timestamp": self.timestamp,
        }


class TD1Device:
    """Dispositivo fisico TD-1 (lector inyectable o simulado)."""

    def __init__(
        self,
        config: Any = None,
        reader: Optional[Callable[[], Optional[TD1Reading]]] = None,
        name: str = "td1",
    ) -> None:
        self.config = config
        self.name = name
        self._reader = reader
        self.available: bool = reader is not None or config is not None
        self.last: Optional[TD1Reading] = None
        self.reads: int = 0

    def read(self) -> Optional[TD1Reading]:
        """Lee del ``reader`` (aislando fallos) o devuelve la ultima lectura."""
        self.reads += 1
        if self._reader is None:
            return self.last
        try:
            reading = self._reader()
        except Exception:  # noqa: BLE001 - aislar fallos del lector
            return self.last
        if reading is not None:
            self.last = reading
        return reading

    def simulate(self, td: float, color_hex: str = "", color_name: str = "") -> TD1Reading:
        """Inyecta una lectura (tests / banco) y actualiza ``last``."""
        reading = TD1Reading(
            td=float(td),
            color_hex=color_hex,
            color_name=color_name,
            timestamp=time.time(),
        )
        self.last = reading
        return reading

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "reads": self.reads,
            "last": self.last.as_dict() if self.last is not None else None,
        }


class TD1Manager:
    """Gestiona uno o varios dispositivos TD-1 y su asociacion a gates."""

    def __init__(self, config: Any = None, profile: Any = None) -> None:
        self.config = config
        self.profile = profile
        self.enabled: bool = bool(_cfg_get(config, "td1_enabled", False))
        raw_count = int(_cfg_get(config, "td1_count", 0))
        self.count: int = max(0, raw_count) if self.enabled else 0
        self.gate_readings: Dict[int, TD1Reading] = {}
        self.devices: List[TD1Device] = [
            TD1Device(config=config, name=f"td1_{index}") for index in range(self.count)
        ]

    @property
    def available(self) -> bool:
        """Disponible solo si esta habilitado y hay al menos un dispositivo."""
        return self.enabled and self.count > 0

    def _device(self, device_index: int) -> Optional[TD1Device]:
        if 0 <= device_index < len(self.devices):
            return self.devices[device_index]
        return None

    def read_gate(self, gate: int, device_index: int = 0) -> Optional[TD1Reading]:
        """Lee un dispositivo y asocia la lectura al ``gate`` indicado."""
        device = self._device(device_index)
        if device is None:
            return None
        reading = device.read()
        if reading is not None:
            self.assign_to_gate(gate, reading)
        return reading

    def assign_to_gate(self, gate: int, reading: TD1Reading) -> None:
        """Asocia una lectura ya obtenida a un gate."""
        self.gate_readings[int(gate)] = reading

    def scan_all(self, gates: List[int]) -> Dict[int, Optional[TD1Reading]]:
        """Escanea una lista de gates devolviendo la lectura de cada uno."""
        return {int(gate): self.read_gate(int(gate)) for gate in gates}

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "count": self.count,
            "devices": [device.get_status() for device in self.devices],
            "gates": {
                str(gate): reading.as_dict() for gate, reading in self.gate_readings.items()
            },
        }


__all__ = ["TD1Reading", "TD1Device", "TD1Manager", "td_to_color", "TD_COLOR_PALETTE"]
