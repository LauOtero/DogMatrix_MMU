"""Paneles de KlipperScreen para control del MMU (patron MVP).

Presenter que transforma los objetos de estado de Moonraker en modelos de vista
y delega el render en KlipperScreen. Si KlipperScreen/GTK no esta disponible,
los metodos devuelven los modelos de vista (headless), lo que permite probar la
logica sin pantalla (informe 6.20).

Requisitos: respuesta tactil < 100 ms, actualizacion reactiva via WebSocket.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class KlipperScreenMMUPanel:
    """Presenter del panel MMU para KlipperScreen."""

    PANEL_NAME = "dog_matrix"

    def __init__(self, screen_manager: Any = None) -> None:
        self.screen_manager = screen_manager
        self.last_error: Optional[Dict[str, Any]] = None
        self.last_step: Optional[Dict[str, Any]] = None

    # -- Modelos de vista ---------------------------------------------------
    def render_gates_view(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Construye el modelo de vista de gates a partir del estado MMU."""
        gates = int(status.get("gates", 0))
        gate_status = status.get("gate_status", [])
        ttg = status.get("ttg_map", [])
        view = {
            "title": "Dog Matrix MMU",
            "state": status.get("state", "UNKNOWN"),
            "active_gate": status.get("gate"),
            "active_tool": status.get("tool"),
            "gates": [
                {
                    "index": index,
                    "status": gate_status[index] if index < len(gate_status) else "unknown",
                    "tool": ttg.index(index) if index in ttg else None,
                    "active": status.get("gate") == index,
                }
                for index in range(gates)
            ],
        }
        self._maybe_emit("gates", view)
        return view

    def render_spoolman_selector(self, spools: List[Any]) -> Dict[str, Any]:
        view = {
            "title": "Seleccionar bobina",
            "spools": [
                {
                    "id": getattr(spool, "spool_id", None),
                    "material": getattr(spool, "material", ""),
                    "color": getattr(spool, "color", ""),
                    "name": getattr(spool, "name", ""),
                }
                for spool in spools
            ],
        }
        self._maybe_emit("spoolman", view)
        return view

    def show_calibration_wizard_step(self, step_info: Dict[str, Any]) -> Dict[str, Any]:
        self.last_step = dict(step_info)
        view = {
            "title": step_info.get("title", "Calibracion"),
            "step": step_info.get("index", 0),
            "total": step_info.get("total", 1),
            "instructions": step_info.get("instructions", ""),
            "actions": step_info.get("actions", []),
        }
        self._maybe_emit("wizard_step", view)
        return view

    def trigger_toolchange(self, target_tool: int) -> Dict[str, Any]:
        """Solicita un cambio de herramienta via Moonraker (no bloqueante)."""
        command = f"DM_CHANGE TOOL={int(target_tool)}"
        sent = self._send_gcode(command)
        return {"sent": sent, "command": command, "tool": int(target_tool)}

    def show_error_dialog(self, error: Dict[str, Any]) -> Dict[str, Any]:
        self.last_error = dict(error) if isinstance(error, dict) else {"message": str(error)}
        view = {
            "title": "Error MMU",
            "code": self.last_error.get("error_code", ""),
            "message": self.last_error.get("message", ""),
            "actions": ["Retry", "Recover", "Cancel"],
        }
        self._maybe_emit("error", view)
        return view

    # -- Vistas ampliadas (paridad KlipperScreen fork) ----------------------
    def render_ttg_editor(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Editor de mapa tool->gate (TTG) para la UI."""
        ttg = status.get("ttg_map", [])
        gates = int(status.get("gates", len(ttg)))
        view = {
            "title": "Mapa TTG",
            "rows": [
                {
                    "tool": tool,
                    "gate": ttg[tool] if tool < len(ttg) else None,
                    "gates": list(range(gates)),
                }
                for tool in range(len(ttg))
            ],
        }
        self._maybe_emit("ttg_editor", view)
        return view

    def render_gate_editor(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Editor de gate map (material/color/spool) para la UI."""
        gates = int(status.get("gates", 0))
        gate_status = status.get("gate_status", [])
        filament = status.get("gate_filament", [])
        view = {
            "title": "Editar gates",
            "gates": [
                {
                    "index": index,
                    "status": gate_status[index] if index < len(gate_status) else "unknown",
                    "material": filament[index].get("material", "") if index < len(filament) else "",
                    "color": filament[index].get("color", "") if index < len(filament) else "",
                    "spool_id": filament[index].get("spool_id", "") if index < len(filament) else "",
                }
                for index in range(gates)
            ],
        }
        self._maybe_emit("gate_editor", view)
        return view

    def render_maintenance_view(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Vista de mantenimiento (contadores y limites)."""
        counters = status.get("counters_store", {})
        view = {
            "title": "Mantenimiento",
            "counters": [
                {"name": name, "value": payload.get("value", 0), "limit": payload.get("limit", -1)}
                for name, payload in sorted(counters.items())
            ],
            "actions": ["Limpiar contador", "Test movimiento", "Motores OFF"],
        }
        self._maybe_emit("maintenance", view)
        return view

    def render_environment_view(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Vista de entorno (secado/ventilacion)."""
        env = status.get("environment", {}) if isinstance(status, dict) else {}
        view = {
            "title": "Entorno",
            "temperature": env.get("actual_temp", env.get("temperature", 0.0)),
            "humidity": env.get("actual_humidity", env.get("humidity", 0.0)),
            "drying": env.get("drying", False),
            "dryer_power": env.get("dryer_power", 0.0),
            "actions": ["Secar", "Parar secado", "Ventilar"],
        }
        self._maybe_emit("environment", view)
        return view

    def render_stats_view(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Vista de estadisticas y telemetria."""
        view = {
            "title": "Estadisticas",
            "counters": status.get("counters", {}),
            "timings": status.get("toolchange_timings", {}),
            "flowguard": status.get("flowguard", {}),
        }
        self._maybe_emit("stats", view)
        return view

    def render_test_menu(self) -> Dict[str, Any]:
        """Menu de pruebas guiadas de hardware."""
        view = {
            "title": "Pruebas",
            "items": [
                "DM_TEST_MOVE", "DM_TEST_HOMING_MOVE", "DM_TEST_TRACKING",
                "DM_TEST_BUZZ_MOTOR", "DM_TEST_LOAD", "DM_TEST_GRIP",
                "DM_TEST_RUNOUT", "DM_TEST_FORM_TIP",
            ],
        }
        self._maybe_emit("test_menu", view)
        return view

    # -- Integracion --------------------------------------------------------
    def _send_gcode(self, script: str) -> bool:
        if self.screen_manager is None:
            return False
        printer = getattr(self.screen_manager, "printer", None)
        if printer is None or not hasattr(printer, "gcode"):
            return False
        try:
            printer.gcode(script)
            return True
        except Exception:  # noqa: BLE001 - transporte no disponible
            return False

    def _maybe_emit(self, event: str, view: Dict[str, Any]) -> None:
        if self.screen_manager is None:
            return
        emit = getattr(self.screen_manager, "emit_panel_event", None)
        if callable(emit):
            try:
                emit(self.PANEL_NAME, event, view)
            except Exception:  # noqa: BLE001
                return


__all__ = ["KlipperScreenMMUPanel"]
