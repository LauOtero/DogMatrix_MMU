"""Contrato de estado y bus de eventos compatible con Happy Hare (``mmu:*``).

Expone:

- ``EventEmitter``: emisor determinista sobre el bus de eventos de Klipper
  (``printer.send_event``). No bloquea: ``send_event`` de Klipper ejecuta los
  handlers de forma sincrona pero estos son cooperativos; aqui no se hace
  ningun trabajo pesado, solo se publica el cambio.
- ``build_mmu_state(core, eventtime)``: diccionario compatible con
  ``printer.mmu`` (subconjunto de variables de Happy Hare).
- ``build_mmu_machine(core)``: estructura ``printer.mmu_machine`` (unidades).

Diseno: autocontenido (no importa Klipper), deterministico y de coste O(gates)
con listas preasignadas; pensado para llamarse desde ``get_status`` (< 5 ms).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# --- Nombres de evento (prefijo mmu:) --------------------------------------
EVENT_ENABLED = "enabled"
EVENT_DISABLED = "disabled"
EVENT_INITIALIZED = "initialized"
EVENT_BOOTUP = "bootup"
EVENT_TOOL_SELECTED = "tool_selected"
EVENT_GATE_SELECTED = "gate_selected"
EVENT_UNIT_SELECTED = "unit_selected"
EVENT_TOOLCHANGE = "toolchange"
EVENT_SYNCED = "synced"
EVENT_UNSYNCED = "unsynced"
EVENT_SYNC_FEEDBACK = "sync_feedback"
EVENT_PRINTING = "printing"
EVENT_NOT_PRINTING = "not_printing"
EVENT_PAUSED = "mmu_paused"
EVENT_RESUMED = "mmu_resumed"
EVENT_SPOOLID_PENDING = "spoolid_pending"
EVENT_SPOOLID_NOT_PENDING = "spoolid_not_pending"
EVENT_ESPOOLER_BURST = "espooler_burst"
EVENT_ESPOOLER_BURST_DONE = "espooler_burst_done"

EVENT_PREFIX = "mmu:"

ALL_EVENTS = (
    EVENT_ENABLED, EVENT_DISABLED, EVENT_INITIALIZED, EVENT_BOOTUP,
    EVENT_TOOL_SELECTED, EVENT_GATE_SELECTED, EVENT_UNIT_SELECTED, EVENT_TOOLCHANGE,
    EVENT_SYNCED, EVENT_UNSYNCED, EVENT_SYNC_FEEDBACK, EVENT_PRINTING, EVENT_NOT_PRINTING,
    EVENT_PAUSED, EVENT_RESUMED, EVENT_SPOOLID_PENDING, EVENT_SPOOLID_NOT_PENDING,
    EVENT_ESPOOLER_BURST, EVENT_ESPOOLER_BURST_DONE,
)

# --- Estados de impresion compatibles --------------------------------------
PRINT_STATE_IDLE = "idle"
PRINT_STATE_STANDBY = "standby"
PRINT_STATE_STARTED = "started"
PRINT_STATE_PRINTING = "printing"
PRINT_STATE_PAUSED = "paused"
PRINT_STATE_COMPLETE = "complete"
PRINT_STATE_CANCELLED = "cancelled"
PRINT_STATE_ERROR = "error"
PRINT_STATE_INITIALIZED = "initialized"

# --- Posiciones de filamento (0..10, subconjunto Happy Hare) ---------------
FILAMENT_POS = {
    "unknown": -1,
    "unloaded": 0,
    "homed_gate": 1,
    "start_bowden": 2,
    "in_bowden": 3,
    "end_bowden": 4,
    "homed_entry": 5,
    "homed_extruder": 6,
    "past_extruder": 7,
    "homed_ts": 8,
    "in_extruder": 9,
    "loaded": 10,
}


class EventEmitter:
    """Emisor de eventos ``mmu:*`` sobre el bus de eventos de Klipper.

    ``send_event`` de Klipper ya despacha a los handlers registrados; aqui se
    envuelve con proteccion para que un fallo del bus nunca propague. Ademas
    soporta suscriptores locales (util en tests sin Klipper). No bloquea.
    """

    def __init__(self, printer: Any = None) -> None:
        self.printer = printer
        self._subscribers: Dict[str, List[Callable[..., None]]] = {}

    def subscribe(self, event: str, callback: Callable[..., None]) -> None:
        self._subscribers.setdefault(event, []).append(callback)

    def emit(self, event: str, **kwargs: Any) -> None:
        """Publica ``mmu:<event>`` sin bloquear ni propagar excepciones."""
        if self.printer is not None:
            try:
                self.printer.send_event(EVENT_PREFIX + event, **kwargs)
            except Exception:  # noqa: BLE001 - el bus no debe romper el flujo
                pass
        for callback in self._subscribers.get(event, ()):
            try:
                callback(**kwargs)
            except Exception:  # noqa: BLE001 - aislar suscriptores
                continue


class MachineView:
    """Vista de solo lectura para ``printer.mmu_machine``."""

    def __init__(self, core: Any) -> None:
        self._core = core

    def get_status(self, eventtime: float = 0.0) -> Dict[str, Any]:
        return build_mmu_machine(self._core)


def _print_state(core: Any) -> str:
    """Deriva el ``print_state`` de Happy Hare desde el estado del core."""
    state = ""
    try:
        state = str(core.state_machine.get_state())
    except Exception:  # noqa: BLE001
        state = ""
    mapping = {
        "IDLE": PRINT_STATE_IDLE,
        "READY": PRINT_STATE_STANDBY,
        "LOAD": PRINT_STATE_PRINTING,
        "UNLOAD": PRINT_STATE_PRINTING,
        "CHANGE": PRINT_STATE_PRINTING,
        "COMPLETED": PRINT_STATE_COMPLETE,
        "FAILED": PRINT_STATE_ERROR,
        "RECOVER": PRINT_STATE_PAUSED,
    }
    return mapping.get(state.upper(), PRINT_STATE_IDLE if not state else state.lower())


def _filament_pos(core: Any) -> int:
    if getattr(core, "current_gate", None) is None:
        return FILAMENT_POS["unknown"]
    state = ""
    try:
        state = str(core.state_machine.get_state()).upper()
    except Exception:  # noqa: BLE001
        state = ""
    if state == "COMPLETED":
        return FILAMENT_POS["loaded"]
    return FILAMENT_POS["homed_gate"]


def build_mmu_state(core: Any, eventtime: float = 0.0) -> Dict[str, Any]:
    """Subconjunto compatible de ``printer.mmu`` (coste O(gates))."""
    gates = core.profile.gates
    ttg_map = list(core.ttg_map)
    gate_filament = core.gate_filament
    active: Dict[str, Any] = {}
    if core.current_gate is not None and 0 <= core.current_gate < gates:
        active = dict(gate_filament[core.current_gate])
    return {
        "enabled": True,
        "num_gates": gates,
        "print_state": _print_state(core),
        "action": _print_state(core),
        "tool": core.current_tool if core.current_tool is not None else -1,
        "gate": core.current_gate if core.current_gate is not None else -1,
        "filament_pos": _filament_pos(core),
        "filament_position": 0.0,
        "filament_direction": 0,
        "num_toolchanges": core.counters.get("toolchanges", 0),
        "last_tool": core.current_tool if core.current_tool is not None else -1,
        "next_tool": -1,
        "ttg_map": ttg_map,
        "endless_spool_groups": [list(g) for g in core.endless_spool_groups],
        "endless_spool_enabled": bool(getattr(core, "enable_endless_spool", False)),
        "gate_status": list(core.gate_status),
        "gate_material": [f.get("material", "") for f in gate_filament],
        "gate_color": [f.get("color", "") for f in gate_filament],
        "gate_spool_id": [f.get("spool_id", "") for f in gate_filament],
        "active_filament": active,
        "sensors": core.sensor_flags() if hasattr(core, "sensor_flags") else {},
        "sync_feedback_state": getattr(core, "sync_feedback_state", "disabled"),
        "slicer_tool_map": core.slicer_map.as_tool_map() if getattr(core, "slicer_map", None) else [],
        "environment": (
            core.environment.get_status().as_dict()
            if getattr(core, "environment", None) is not None
            else {}
        ),
        "tip_forming": (
            core.tip_former.get_status()
            if getattr(core, "tip_former", None) is not None
            else {}
        ),
    }


def build_mmu_machine(core: Any) -> Dict[str, Any]:
    """Estructura ``printer.mmu_machine`` (unidades y capacidades)."""
    controller = getattr(core, "controller", None)
    units: List[Dict[str, Any]] = []
    if controller is not None:
        for unit in controller.list_units():
            units.append(
                {
                    "name": unit.unit_id,
                    "first_gate": unit.gate_offset,
                    "num_gates": unit.gates,
                    "selector_type": core.profile.selector_type,
                    "has_bypass": False,
                    "vendor": core.profile.profile_id,
                }
            )
    if not units:
        units.append(
            {
                "name": "unit0",
                "first_gate": 0,
                "num_gates": core.profile.gates,
                "selector_type": core.profile.selector_type,
                "has_bypass": False,
                "vendor": core.profile.profile_id,
            }
        )
    return {
        "happy_hare_version": "n/a",
        "num_units": len(units),
        "num_gates": core.profile.gates,
        "units": units,
    }


__all__ = [
    "EventEmitter",
    "MachineView",
    "build_mmu_state",
    "build_mmu_machine",
    "ALL_EVENTS",
    "FILAMENT_POS",
]
