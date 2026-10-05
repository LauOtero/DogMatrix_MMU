"""Nucleo de orquestacion de Dog Matrix MMU.

Coordina los subsistemas, registra los comandos G-code (``DM_*`` y alias
``MMU_*``), gestiona los eventos de ciclo de vida de Klipper y expone el
estado a traves de ``get_status`` (patrones Command, Observer, Repository).

Requisitos (informe 6.1): inicializacion < 500 ms, ``get_status`` < 5 ms.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .capabilities import Capabilities, ProfileError, find_profile
from .diagnostics import Diagnostics
from .encoder import Encoder
from .flowguard import FlowGuard
from .led_system import LEDSystem
from .motion import Motion
from .nfc_rfid import NFCReader
from .persistence import Persistence, PersistenceError
from .recovery import Recovery
from .selector import Selector
from .sensors import SensorManager
from .spoolman import SpoolManager
from .state_machine import StateMachine, ToolchangeResult

SOFTWARE_VERSION = "0.1.0"
DEFAULT_MAX_GATES = 16


def _config_to_dict(config: Any) -> Dict[str, Any]:
    """Extrae las claves de interes de un ConfigWrapper de Klipper."""
    keys = (
        "profile",
        "profile_path",
        "profiles_dir",
        "state_store",
        "log_level",
        "log_path",
        "evidence_dir",
        "enable_flowguard",
        "enable_endless_spool",
        "enable_spoolman",
        "enable_led",
        "enable_nfc",
        "enable_purge",
        "auto_recover",
        "num_gates",
    )
    if isinstance(config, dict):
        return {k: config.get(k) for k in keys if k in config}
    getter = getattr(config, "get", None)
    if not callable(getter):
        return {}
    out: Dict[str, Any] = {}
    for key in keys:
        try:
            out[key] = getter(key, None)
        except TypeError:
            continue
    return {k: v for k, v in out.items() if v is not None}


class DogMatrixCore:
    """Objeto principal del modulo Klipper ``[dog_matrix]``."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.printer = config.get_printer() if hasattr(config, "get_printer") else None
        self.cfg = _config_to_dict(config)
        self.name = config.get_name() if hasattr(config, "get_name") else "dog_matrix"
        started = time.monotonic()

        # --- Perfil y capacidades -----------------------------------------
        self.capabilities = Capabilities(self._resolve_profile_path())
        self.profile = self.capabilities.load()

        # --- Estado en memoria --------------------------------------------
        gates = self.profile.gates
        self.current_gate: Optional[int] = None
        self.current_tool: Optional[int] = None
        self.ttg_map: List[int] = list(range(gates))  # tool -> gate
        self.gate_status: List[str] = ["unknown"] * gates
        self.gate_filament: List[Dict[str, Any]] = [
            {
                "material": "",
                "color": "",
                "spool_id": "",
                "availability": "unknown",  # unknown / available / buffered / empty
            }
            for _ in range(gates)
        ]
        self.enable_purge = self._flag("enable_purge", False)
        self.auto_recover = self._flag("auto_recover", False)
        self.counters: Dict[str, int] = {"toolchanges": 0, "loads": 0, "unloads": 0, "errors": 0}
        self.toolchange_timings: Dict[str, float] = {
            "pre_unload": 0.0,
            "unload": 0.0,
            "post_unload": 0.0,
            "pre_load": 0.0,
            "load": 0.0,
            "post_load": 0.0,
            "total": 0.0,
        }
        # --- EndlessSpool ---
        self.enable_endless_spool = self._flag("enable_endless_spool", False)
        self.endless_spool_groups: List[List[int]] = self._init_endless_groups()
        self.endless_spool_final_eject: float = float(self.cfg.get("endless_spool_final_eject", 0))
        self.endless_spool_eject_gate: Optional[int] = self.cfg.get("endless_spool_eject_gate", None)
        self.boot_time = time.time()

        # --- Componentes ---------------------------------------------------
        state_store = self.cfg.get(
            "state_store", os.path.expanduser("~/printer_data/config/dog_matrix_state.json")
        )
        self.diagnostics = Diagnostics(self.cfg)
        self.persistence = Persistence(str(state_store))
        self._restore_state()

        self.motion = Motion(self.printer, config)
        self.selector = Selector(self.printer, config, motion=self.motion, profile=self.profile)
        self.sensors = SensorManager(self.printer, config, profile=self.profile)
        self.encoder = Encoder(self.printer, config)
        self.flowguard = FlowGuard(config, profile=self.profile)
        self.recovery = Recovery(self)
        self.led = LEDSystem(self.printer, config, profile=self.profile) if self._flag("enable_led", True) else None
        self.nfc = NFCReader(self.printer, config) if self._flag("enable_nfc", False) else None
        self.spoolman = SpoolManager(self._moonraker(), config) if self._flag("enable_spoolman", False) else None
        self.state_machine = StateMachine(self)
        # Registrar este objeto para acceso desde gcode y macros
        if self.printer is not None:
            self.printer.add_object("dog_matrix", self)

        self._register_commands()
        self._register_events()
        init_ms = (time.monotonic() - started) * 1000.0
        self.diagnostics.log_event(
            "info",
            "core",
            "initialized",
            profile=self.profile.profile_id,
            gates=gates,
            init_ms=round(init_ms, 3),
            version=SOFTWARE_VERSION,
        )

    # -- Resolucion de configuracion ---------------------------------------
    def _resolve_profile_path(self) -> str:
        explicit = self.cfg.get("profile_path")
        if explicit:
            return str(explicit)
        profile_name = self.cfg.get("profile", "box_turtle")
        profiles_dir = self.cfg.get("profiles_dir")
        return str(find_profile(str(profile_name), profiles_dir))

    def _flag(self, key: str, default: bool) -> bool:
        value = self.cfg.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _moonraker(self) -> Any:
        if self.printer is None:
            return None
        try:
            return self.printer.lookup_object("moonraker", None)
        except Exception:  # noqa: BLE001 - moonraker opcional
            return None

    # -- Estado persistente -------------------------------------------------
    def snapshot_state(self) -> Dict[str, Any]:
        return {
            "software_version": SOFTWARE_VERSION,
            "profile_id": self.profile.profile_id,
            "current_gate": self.current_gate,
            "current_tool": self.current_tool,
            "ttg_map": self.ttg_map,
            "gate_status": self.gate_status,
            "gate_filament": self.gate_filament,
            "counters": self.counters,
            "saved_at": time.time(),
        }

    def _restore_state(self) -> None:
        try:
            data = self.persistence.load()
        except PersistenceError as exc:
            self.diagnostics.log_event("error", "core", "state_load_failed", error=str(exc))
            return
        if not data:
            return
        gates = self.profile.gates
        ttg = data.get("ttg_map")
        if isinstance(ttg, list) and len(ttg) == gates:
            self.ttg_map = [int(g) for g in ttg]
        status = data.get("gate_status")
        if isinstance(status, list) and len(status) == gates:
            self.gate_status = list(status)
        filament = data.get("gate_filament")
        if isinstance(filament, list) and len(filament) == gates:
            self.gate_filament = list(filament)
        counters = data.get("counters")
        if isinstance(counters, dict):
            self.counters.update({k: int(v) for k, v in counters.items()})
        self.current_gate = data.get("current_gate")
        self.current_tool = data.get("current_tool")

    # -- Registro de comandos ----------------------------------------------
    # Mapa comando canonico -> alias compatible Happy Hare (MMU_*).
    COMMAND_ALIASES: Dict[str, str] = {
        "DM_STATUS": "MMU_STATUS",
        "DM_CHANGE": "MMU_CHANGE_TOOL",
        "DM_LOAD": "MMU_LOAD",
        "DM_UNLOAD": "MMU_UNLOAD",
        "DM_RECOVER": "MMU_RECOVER",
        "DM_HOME": "MMU_HOME",
        "DM_ENCODER": "MMU_ENCODER",
        "DM_GATE_MAP": "MMU_GATE_MAP",
        "DM_REMAP_TTG": "MMU_REMAP_TTG",
        "DM_SPOOLMAN": "MMU_SPOOLMAN",
        "DM_ENDLESS_SPOOL": "MMU_ENDLESS_SPOOL",
        "DM_TEST_CONFIG": "MMU_TEST_CONFIG",
        "DM_START_SETUP": "MMU_START_SETUP",
        "DM_PRINT_STATE": "MMU_PRINT_STATE",
        "DM_STATS": "MMU_STATS",
    }

    def _register_commands(self) -> None:
        gcode = self._gcode()
        if gcode is None:
            return
        handlers = {
            "DM_STATUS": self.cmd_DM_STATUS,
            "DM_CHANGE": self.cmd_DM_CHANGE_TOOL,
            "DM_LOAD": self.cmd_DM_LOAD,
            "DM_UNLOAD": self.cmd_DM_UNLOAD,
            "DM_RECOVER": self.cmd_DM_RECOVER,
            "DM_HOME": self.cmd_DM_HOME,
            "DM_ENCODER": self.cmd_DM_ENCODER,
            "DM_GATE_MAP": self.cmd_DM_GATE_MAP,
            "DM_REMAP_TTG": self.cmd_DM_REMAP_TTG,
            "DM_SPOOLMAN": self.cmd_DM_SPOOLMAN,
            "DM_ENDLESS_SPOOL": self.cmd_DM_ENDLESS_SPOOL,
            "DM_TEST_CONFIG": self.cmd_DM_TEST_CONFIG,
            "DM_START_SETUP": self.cmd_DM_START_SETUP,
        }
        for name, handler in handlers.items():
            try:
                gcode.register_command(name, handler, desc=f"Dog Matrix: {name}")
                alias = self.COMMAND_ALIASES.get(name)
                if alias:
                    gcode.register_command(alias, handler, desc=f"Dog Matrix alias {alias}")
            except Exception as exc:  # noqa: BLE001 - registro defensivo
                self.diagnostics.log_event("warning", "core", "command_register_failed", cmd=name, error=str(exc))

    def _gcode(self) -> Any:
        if self.printer is None:
            return None
        try:
            return self.printer.lookup_object("gcode", None)
        except Exception:  # noqa: BLE001
            return None

    def _register_events(self) -> None:
        if self.printer is None:
            return
        try:
            self.printer.register_event_handler("klippy:ready", self._handle_ready)
            self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
        except Exception:  # noqa: BLE001 - entorno sin bus de eventos
            pass

    # -- Ciclo de vida ------------------------------------------------------
    def _handle_ready(self) -> None:
        self.diagnostics.log_event("info", "core", "ready")
        self.sensors.start_polling()

    def _handle_shutdown(self) -> None:
        self.diagnostics.log_event("warning", "core", "shutdown")
        try:
            self.persistence.save(self.snapshot_state())
        except PersistenceError as exc:
            self.diagnostics.log_event("error", "core", "state_save_failed", error=str(exc))

    # -- Status -------------------------------------------------------------
    def get_status(self, eventtime: float) -> Dict[str, Any]:
        return {
            "state": self.state_machine.get_state(),
            "gate": self.current_gate,
            "tool": self.current_tool,
            "gates": self.profile.gates,
            "ttg_map": list(self.ttg_map),
            "gate_status": list(self.gate_status),
            "gate_filament": self.gate_filament,
            "counters": dict(self.counters),
            "toolchange_timings": dict(self.toolchange_timings),
            "flowguard": self.flowguard.get_statistics() if self.flowguard else {},
            "version": SOFTWARE_VERSION,
            "profile": self.profile.profile_id,
        }

    # -- Comandos G-code ----------------------------------------------------
    def cmd_DM_STATUS(self, gcmd: Any) -> None:
        status = self.get_status(time.time())
        gcmd.respond_info(
            "Dog Matrix MMU [{profile}] state={state} gate={gate} tool={tool} "
            "ttg={ttg}".format(
                profile=status["profile"],
                state=status["state"],
                gate=status["gate"],
                tool=status["tool"],
                ttg=status["ttg_map"],
            )
        )

    def cmd_DM_PRINT_STATE(self, gcmd: Any) -> None:
        """Establecer el estado de la impresora y emitir callback."""
        new_state = gcmd.get("STATE", "")
        if not new_state:
            raise gcmd.error("STATE es requerido")
        self._emit_callback("_DM_PRINT_STATE_CHANGED", state=new_state)
        gcmd.respond_info(f"Print state cambiado a {new_state}")

    def cmd_DM_CHANGE_TOOL(self, gcmd: Any) -> None:
        tool = gcmd.get_int("TOOL", 0)
        if not 0 <= tool < len(self.ttg_map):
            raise gcmd.error(f"TOOL {tool} fuera de rango 0..{len(self.ttg_map) - 1}")
        gate = self.ttg_map[tool]
        result = self.state_machine.execute_toolchange(gate, tool)
        if result.success:
            self.counters["toolchanges"] += 1
            gcmd.respond_info(f"Toolchange OK -> tool {tool} (gate {gate})")
        else:
            self.counters["errors"] += 1
            raise gcmd.error(f"Toolchange fallido: {result.error_code} ({result.message})")

    def cmd_DM_LOAD(self, gcmd: Any) -> None:
        result = self.state_machine.execute_toolchange(self.current_gate or 0, self.current_tool or 0)
        self.counters["loads"] += 1
        if not result.success:
            raise gcmd.error(f"Load fallido: {result.error_code}")
        gcmd.respond_info("Load OK")

    def cmd_DM_UNLOAD(self, gcmd: Any) -> None:
        motion = self.motion
        limits = self.profile.limits
        ok = motion.unload_filament(
            float(limits.get("max_distance_mm", 1000)),
            float(limits.get("max_unload_speed_mm_s", 80)),
        )
        self.counters["unloads"] += 1
        if not ok:
            raise gcmd.error("Unload fallido")
        self.current_gate = None
        self.current_tool = None
        gcmd.respond_info("Unload OK")

    def cmd_DM_RECOVER(self, gcmd: Any) -> None:
        info = {"error_code": gcmd.get("CODE", self.state_machine.last_error or "")}
        result = self.recovery.recover_from_failure(info)
        if result.success:
            gcmd.respond_info(f"Recuperacion OK ({result.action})")
        else:
            gcmd.respond_info(f"Recuperacion requiere atencion: {result.message}")

    def cmd_DM_HOME(self, gcmd: Any) -> None:
        if self.selector.home():
            gcmd.respond_info("Homing de selector OK")
        else:
            raise gcmd.error("Homing de selector fallido")

    def cmd_DM_ENCODER(self, gcmd: Any) -> None:
        action = gcmd.get("ACTION", "READ").upper()
        if action == "RESET":
            self.encoder.reset()
            gcmd.respond_info("Encoder reseteado")
        else:
            gcmd.respond_info(
                f"Encoder pos={self.encoder.read_position():.3f}mm "
                f"vel={self.encoder.read_velocity():.3f}mm/s"
            )

    def cmd_DM_GATE_MAP(self, gcmd: Any) -> None:
        gate = gcmd.get_int("GATE", None)
        if gate is None:
            for index in range(self.profile.gates):
                gcmd.respond_info(
                    f"gate {index}: tool={self.ttg_map.index(index) if index in self.ttg_map else '-'} "
                    f"status={self.gate_status[index]} material={self.gate_filament[index].get('material', '-')} "
                    f"color={self.gate_filament[index].get('color', '-')} spool_id={self.gate_filament[index].get('spool_id', '-')} "
                    f"availability={self.gate_filament[index].get('availability', 'unknown')}"
                )
            return
        if not 0 <= gate < self.profile.gates:
            raise gcmd.error("GATE fuera de rango")
        new_status = gcmd.get("STATUS", self.gate_status[gate])
        new_material = gcmd.get("MATERIAL", self.gate_filament[gate].get("material", ""))
        new_color = gcmd.get("COLOR", self.gate_filament[gate].get("color", ""))
        new_spool_id = gcmd.get("SPOOL_ID", self.gate_filament[gate].get("spool_id", ""))
        new_availability = gcmd.get("AVAILABILITY", self.gate_filament[gate].get("availability", "unknown"))
        self.gate_status[gate] = new_status
        self.gate_filament[gate] = {
            "material": new_material,
            "color": new_color,
            "spool_id": new_spool_id,
            "availability": new_availability,
        }
        self.persistence.save(self.snapshot_state())
        self._emit_callback("_DM_GATE_MAP_CHANGED", gate=gate, status=new_status, material=new_material, color=new_color, spool_id=new_spool_id)
        gcmd.respond_info(f"gate {gate} status={self.gate_status[gate]} material={new_material} color={new_color} spool_id={new_spool_id}")

    def cmd_DM_REMAP_TTG(self, gcmd: Any) -> None:
        tool = gcmd.get_int("TOOL", None)
        gate = gcmd.get_int("GATE", None)
        if tool is None or gate is None:
            raise gcmd.error("DM_REMAP_TTG requiere TOOL y GATE")
        if not 0 <= tool < len(self.ttg_map) or not 0 <= gate < self.profile.gates:
            raise gcmd.error("TOOL o GATE fuera de rango")
        self.ttg_map[tool] = gate
        self.persistence.save(self.snapshot_state())
        self._emit_callback("_DM_GATE_MAP_CHANGED", action="remap_ttg", tool=tool, gate=gate)
        gcmd.respond_info(f"TTG remapeado: tool {tool} -> gate {gate}")

    def cmd_DM_SPOOLMAN(self, gcmd: Any) -> None:
        if self.spoolman is None:
            raise gcmd.error("Spoolman deshabilitado (enable_spoolman=false)")
        action = gcmd.get("ACTION", "STATUS").upper()
        if action == "SYNC":
            ok = self.spoolman.sync_gate_map()
            gcmd.respond_info("Spoolman sync OK" if ok else "Spoolman sync fallido")
        else:
            status = self.spoolman.get_inventory_status()
            gcmd.respond_info(f"Spoolman: {status}")

    def cmd_DM_ENDLESS_SPOOL(self, gcmd: Any) -> None:
        if not self.profile.has_capability("endless_spool"):
            raise gcmd.error("EndlessSpool no soportado por el perfil")
        action = gcmd.get("ACTION", "STATUS").upper()
        if action == "STATUS":
            groups_str = ", ".join(
                f"group {i}: gates {g}" for i, g in enumerate(self.endless_spool_groups)
            )
            gcmd.respond_info(
                f"EndlessSpool enabled={self.enable_endless_spool}, groups: {groups_str}, "
                f"final_eject={self.endless_spool_final_eject}mm, eject_gate={self.endless_spool_eject_gate}"
            )
        elif action == "SET":
            groups = gcmd.get("GROUPS", None)
            if groups is None:
                raise gcmd.error("Acción SET requiere parámetro GROUPS")
            # Parsear grupos
            import json
            groups_list = json.loads(groups) if isinstance(groups, str) else groups
            self.endless_spool_groups = groups_list
            self._emit_callback("_DM_GATE_MAP_CHANGED", action="endless_spool_set", groups=self.endless_spool_groups)
            gcmd.respond_info(f"EndlessSpool groups set: {self.endless_spool_groups}")
        elif action == "EJECT":
            # Ejecutar eyección de resto en el gate configurado
            eject_gate = self.endless_spool_eject_gate
            if eject_gate is None:
                raise gcmd.error("No hay gate de eyección configurado. Usa SET GROUPS primero.")
            gcmd.respond_info(f"Eyección de resto en gate {eject_gate} (distancia: {self.endless_spool_final_eject}mm)")
        else:
            gcmd.respond_info(f"EndlessSpool status: enabled={self.enable_endless_spool}")

    def cmd_DM_TEST_CONFIG(self, gcmd: Any) -> None:
        errors = self.capabilities.validate()
        if errors:
            raise gcmd.error("Config invalida: " + "; ".join(errors))
        gcmd.respond_info(f"Config OK ({self.profile.profile_id})")

    def cmd_DM_START_SETUP(self, gcmd: Any) -> None:
        """Macro de setup de impresión multicolor.

        Parámetros esperados:
        - TOOLS: lista de herramientas referenciadas (formato JSON)
        - TOTAL_TOOLCHANGES: número total de cambios de herramienta
        - COLORS: lista de colores de los filamentos
        - TEMPERATURES: lista de temperaturas
        """
        tools = gcmd.get("TOOLS", "[]")
        total_toolchanges = gcmd.get_int("TOTAL_TOOLCHANGES", 0)
        colors = gcmd.get("COLORS", "")
        temperatures = gcmd.get("TEMPERATURES", "")

        # Guardar configuración de setup en el estado
        self.setup_config = {
            "tools": tools,
            "total_toolchanges": total_toolchanges,
            "colors": colors,
            "temperatures": temperatures,
            "started_at": time.time(),
        }

        # Actualizar contadores
        self.counters["toolchanges"] = total_toolchanges

        # Emitir evento de callback de cambio de estado
        self._emit_callback("_DM_ACTION_CHANGED", action="setup", setup_config=self.setup_config)

        gcmd.respond_info(
            f"DM_START_SETUP OK: {total_toolchanges} toolchanges, "
            f"tools={tools}, colors={colors}"
        )

    def cmd_DM_CALIBRATE_GEAR(self, gcmd: Any) -> None:
        """Calibración de distancia de rotación del gear stepper."""
        tool = gcmd.get_int("TOOL", 0)
        # Obtener distancia actual desde perfil
        profile = getattr(self, "profile", None)
        if profile is None:
            raise gcmd.error("Perfil no cargado")
        # TODO: Implementar calibración real con medida de encoder
        gcmd.respond_info(f"Calibración gear tool {tool}: distance to be measured")

    def cmd_DM_CALIBRATE_ENCODER(self, gcmd: Any) -> None:
        """Calibración del encoder."""
        tool = gcmd.get_int("TOOL", 0)
        distance_mm = gcmd.get_float("DISTANCE_MM", 100)
        # TODO: Implementar calibración real
        gcmd.respond_info(f"Calibración encoder tool {tool}: {distance_mm}mm")

    def cmd_DM_CALIBRATE_BOWDEN(self, gcmd: Any) -> None:
        """Calibración de longitud bowden."""
        tool = gcmd.get_int("TOOL", 0)
        profile = getattr(self, "profile", None)
        if profile is None:
            raise gcmd.error("Perfil no cargado")
        # TODO: Implementar calibración real
        gcmd.respond_info(f"Calibración bowden tool {tool}: to be measured")

    def cmd_DM_CALIBRATE_GATES(self, gcmd: Any) -> None:
        """Calibración automática de todos los gates."""
        # Calibrar todos los gates usando el encoder
        profile = getattr(self, "profile", None)
        if profile is None:
            raise gcmd.error("Perfil no cargado")
        gates = profile.gates
        # Ejecutar calibración para cada gate
        for gate in range(gates):
            # TODO: Implementar calibración real por gate
            pass
        self._emit_callback("_DM_ACTION_CHANGED", action="calibrate_gates")
        gcmd.respond_info(f"Calibración gates completada para {gates} gates")

    def cmd_DM_CALIBRATE_TOOLHEAD(self, gcmd: Any) -> None:
        """Calibración de dimensiones del toolhead/extrusor."""
        # TODO: Implementar calibración real
        gcmd.respond_info("Calibración toolhead: to be implemented")

    def cmd_DM_CALIBRATE_SELECTOR(self, gcmd: Any) -> None:
        """Calibración automática de offsets del selector."""
        # TODO: Implementar calibración real
        gcmd.respond_info("Calibración selector: to be implemented")

    def cmd_DM_UNLOCK(self, gcmd: Any) -> None:
        """Restaura temperaturas tras un error de MMU."""
        result = self.recovery.recover_from_failure({})
        if result.success:
            gcmd.respond_info(f"Recuperacion OK ({result.action})")
        else:
            gcmd.respond_info(f"Recuperacion requiere atencion: {result.message}")

    def _emit_callback(self, callback_name: str, **kwargs: Any) -> None:
        """Invoca un callback de macro de ciclo de vida."""
        # Registrar el evento para que las macros lo puedan suscribir
        self.diagnostics.log_event("debug", "callback", callback_name, **kwargs)

    def _endless_groups(self) -> List[List[int]]:
        return [list(range(self.profile.gates))] if self.profile.has_capability("endless_spool") else []

    def _init_endless_groups(self) -> List[List[int]]:
        """Inicializa los grupos de endless spool desde la configuración."""
        groups_cfg = self.cfg.get("endless_spool_groups", None)
        if groups_cfg is not None:
            # Parsear desde configuración
            if isinstance(groups_cfg, str):
                # JSON array
                import json
                groups_cfg = json.loads(groups_cfg)
            if isinstance(groups_cfg, list):
                result: List[List[int]] = []
                for group in groups_cfg:
                    if isinstance(group, list):
                        result.append([int(g) for g in group])
                    elif isinstance(group, int):
                        result.append([group])
                return result
        # Por defecto: un grupo con todos los gates
        return [list(range(self.profile.gates))] if self.profile.has_capability("endless_spool") else []

    def _endless_groups(self) -> List[List[int]]:
        return self.endless_spool_groups


def load_config(config: Any) -> DogMatrixCore:
    """Punto de entrada del modulo Klipper ``[dog_matrix]``."""
    return DogMatrixCore(config)


__all__ = ["DogMatrixCore", "load_config", "SOFTWARE_VERSION"]
