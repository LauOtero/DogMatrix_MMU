"""Texto de filamento para consumidores externos (paridad Happy Hare).

Genera etiquetas legibles (material/color/spool) y lineas por gate para paneles
o *front-ends* externos. No incluye ninguna interfaz grafica: solo funciones
puras y un contenedor de estado determinista que tolera entradas incompletas y
nunca lanza excepciones.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

SIN_FILAMENTO = "sin filamento"


def _text(value: Any) -> str:
    """Convierte un valor a texto limpio, tolerando None y tipos raros."""
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:  # noqa: BLE001 - nunca debe lanzar
        return ""


def format_filament(entry: Any) -> str:
    """Etiqueta legible de un filamento, p. ej. ``"PLA #FF0000 (spool 12)"``.

    Tolera campos vacios o ausentes y nunca lanza.
    """
    try:
        if not isinstance(entry, dict):
            return SIN_FILAMENTO
        material = _text(entry.get("material"))
        color = _text(entry.get("color"))
        spool = _text(entry.get("spool_id") or entry.get("spool"))
        label = " ".join(part for part in (material, color) if part)
        if spool:
            suffix = "(spool {})".format(spool)
            return "{} {}".format(label, suffix) if label else suffix
        return label or SIN_FILAMENTO
    except Exception:  # noqa: BLE001 - contrato: no lanzar
        return SIN_FILAMENTO


def format_gate(gate: int, entry: Any, active: bool = False) -> str:
    """Linea de un gate, p. ej. ``"G0: PLA #FF0000"``; marca ``*`` si activo."""
    line = "G{}: {}".format(gate, format_filament(entry))
    return "{} *".format(line) if active else line


class FilamentDisplay:
    """Estado de visualizacion de filamentos por gate (sin UI)."""

    def __init__(self, gate_count: int = 0) -> None:
        self.gate_count: int = max(0, int(gate_count or 0))
        self._gates: Dict[int, Dict[str, Any]] = {}
        self._active: Optional[int] = None

    def set_gate(self, gate: int, entry: Dict[str, Any]) -> None:
        """Registra (o actualiza) los datos de filamento de un gate."""
        try:
            index = int(gate)
        except (TypeError, ValueError):
            return
        self._gates[index] = dict(entry) if isinstance(entry, dict) else {}

    def set_active(self, gate: Optional[int]) -> None:
        """Fija el gate activo; ``None`` indica que no hay ninguno."""
        if gate is None:
            self._active = None
            return
        try:
            self._active = int(gate)
        except (TypeError, ValueError):
            self._active = None

    def lines(self) -> List[str]:
        """Una linea por gate (usa ``format_gate``)."""
        return [
            format_gate(gate, self._gates.get(gate, {}), active=gate == self._active)
            for gate in range(self.gate_count)
        ]

    def active_line(self) -> str:
        """Etiqueta del gate activo o ``"sin filamento"`` si no hay ninguno."""
        if self._active is None:
            return SIN_FILAMENTO
        return format_filament(self._gates.get(self._active, {}))

    def get_status(self) -> Dict[str, Any]:
        return {
            "lines": self.lines(),
            "active": self._active,
            "gate_count": self.gate_count,
        }


__all__ = ["FilamentDisplay", "format_filament", "format_gate", "SIN_FILAMENTO"]
