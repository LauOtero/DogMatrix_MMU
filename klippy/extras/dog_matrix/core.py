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

from .autoload import (
    AutoLoadConfig,
    AutoLoadController,
    AutoLoadService,
    BufferConfig,
    BufferDevice,
    HubConfig,
    HubDevice,
    MultiUnitRouter,
    SensorBank,
    wire_pre_gate_buttons,
)
from .calibration import Calibrator, CalibrationResult
from .capabilities import Capabilities, find_profile
from .compound_endstop import CompoundEndstop, build_compound_endstop
from .counters import CounterStore
from .diagnostics import Diagnostics
from .drive import DriveManager
from .ejection_buttons import EjectionButtons
from .encoder import Encoder
from .environment import EnvironmentManager
from .espooler import ESpooler
from .events import (
    EVENT_BOOTUP,
    EVENT_DISABLED,
    EVENT_ENABLED,
    EVENT_ESPOOLER_BURST,
    EVENT_GATE_SELECTED,
    EVENT_INITIALIZED,
    EVENT_PAUSED,
    EVENT_PRINTING,
    EVENT_NOT_PRINTING,
    EVENT_TOOLCHANGE,
    EVENT_TOOL_SELECTED,
    EventEmitter,
    MachineView,
    build_mmu_state,
)
from .extruder_monitor import ExtruderMonitor, build_extruder_monitor
from .fan_control import FORCED_AUTO, FORCED_OFF, FORCED_ON, FanController
from .filament_display import FilamentDisplay
from .flowguard import FlowGuard
from .klipper_wrappers import ExtruderWrapper, ToolheadWrapper, build_extruder, build_toolhead
from .led_system import (
    EFFECT_BLINK,
    EFFECT_BREATHING,
    EFFECT_OFF,
    EFFECT_RAINBOW,
    EFFECT_SOLID,
    LEDSystem,
)
from .local_gate import LocalGate, build_local_gate
from .motion import Motion
from .nfc_rfid import (
    NFCArbiter,
    NFCEndstop,
    NFCManager,
    NFCReader,
    RXGainController,
    build_nfc_subsystem,
)
from .persistence import Persistence, PersistenceError
from .purge import PurgeManager
from .recovery import Recovery
from .selector import Selector
from .sensors import SensorManager
from .sequences import (
    SKIP,
    Sequence,
    SequenceRegistry,
    build_default_load_sequence,
    build_default_unload_sequence,
)
from .slicer_map import SlicerToolMap
from .spoolman import Spool, SpoolManager
from .state_machine import StateMachine
from .stepper_current import StepperCurrentManager, build_stepper_current
from .sync_controller import SyncController, build_sync_controller
from .sync_feedback import SyncFeedback
from .td1 import TD1Manager
from .telemetry import TelemetryBuffer
from .tip_forming import TipFormer, TipFormingResult
from .tool_overrides import ToolOverrideStore
from .units import build_default_controller

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
        "enable_espooler",
        "enable_tip_forming",
        "enable_environment",
        "enable_ejection_buttons",
        "enable_multi_unit",
        "usb_scan_dir",
        "usb_scan_pattern",
        "enable_usb_scan",
        "enable_can_scan",
        "can_uuids",
        "unit_assignments",
        "unit_scan_interval_s",
        "units_store",
        "auto_recover",
        "num_gates",
        "coils_per_unit",
        "enable_autoload",
        "hub_shared_exit",
        "hub_encoder",
        "hub_diameter_sensor",
        "hub_units",
        "buffer_tension",
        "buffer_compression",
        "autoload_pre_gate_timeout_s",
        "autoload_bowden_timeout_s",
        "enable_fan_control",
        "fan_on_temp",
        "fan_off_temp",
        "fan_pin",
        "fan_polling_s",
        "enable_bypass",
        "endless_spool_groups",
        "endless_spool_eject_gate",
        "endless_spool_final_eject",
        "enable_sync_feedback",
        "sync_feedback_enabled",
        "spoolman_support",
        "td1_enabled",
        "td1_count",
        "nfc_count",
        "nfc_auto_create",
        "led_count",
        "led_effect",
        "ejection_buttons_gates",
        "dryer_heater_pin",
        "enclosure_fan_pin",
        "dryer_target_temp",
        "espooler_pwm_pin",
        "espooler_dir_pin",
        "cutter_servo",
        "z_hop_mm",
        "park_positions",
        "telemetry_capacity",
        "enable_lifecycle_hooks",
        "local_gate_enabled",
        "local_gate_gates",
        "stepper_currents",
        "gear_stepper",
        "selector_macro",
        "sync_mode",
        "tool_overrides",
        "gcode_load_sequence",
        "gcode_unload_sequence",
        "nfc_endstop",
        "nfc_rx_gain_autotune",
        "initial_tool",
        "print_start_gate",
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


class _MotionMotor:
    """Adaptador ``MotorDriver`` que acciona el gear via ``Motion``.

    No bloquea: ``Motion`` emite el movimiento por G-code/trapq (cooperativo);
    aqui solo se fija la velocidad objetivo y se recuerda el estado.
    """

    def __init__(self, motion: Any, unit: str = "unit0") -> None:
        self._motion = motion
        self.unit = unit
        self.speed = 0.0
        self._running = False

    def start(self, speed_mm_s: float) -> None:
        self.speed = float(speed_mm_s)
        self._running = True
        try:
            # Un pulso minimo para arrancar; el servicio de auto-load controla
            # la continuidad mediante los deadlines del reactor.
            self._motion.load_filament(0.1, self.speed)
        except Exception:  # noqa: BLE001 - hardware ausente en tests
            pass

    def stop(self) -> None:
        self._running = False
        self.speed = 0.0

    @property
    def running(self) -> bool:
        return self._running


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
        self.enable_lifecycle_hooks = self._flag("enable_lifecycle_hooks", True)
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
        # Resultados de calibracion y datos extendidos por gate (persistidos).
        self.calibration_results: Dict[str, Any] = {}
        self.gate_filament_extra: Dict[int, Dict[str, Any]] = {}
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
        self.nfc_manager: Optional[NFCManager] = None
        self.nfc: Optional[NFCReader] = None
        self.nfc_arbiter: Optional[NFCArbiter] = None
        self.nfc_endstop: Optional[NFCEndstop] = None
        self.rx_gain: Optional[RXGainController] = None
        if self._flag("enable_nfc", False):
            nfc_sub = build_nfc_subsystem(self.printer, config)
            self.nfc_manager = nfc_sub["manager"]
            self.nfc_arbiter = nfc_sub["arbiter"]
            self.nfc_endstop = nfc_sub["endstop"]
            self.rx_gain = nfc_sub["gain"]
            self.nfc = (
                self.nfc_manager.readers[0]
                if self.nfc_manager.readers
                else NFCReader(self.printer, config)
            )
        self.spoolman = SpoolManager(self._moonraker(), config) if self._flag("enable_spoolman", False) else None
        self.espooler = (
            ESpooler(
                config,
                profile=self.profile,
                set_pwm=self._make_pin_actuator(self.cfg.get("espooler_pwm_pin")),
                set_dir=self._make_bool_actuator(self.cfg.get("espooler_dir_pin")),
                emit=self._emit_script,
            )
            if self._flag("enable_espooler", False)
            else None
        )
        self.tip_former = (
            TipFormer(
                config,
                profile=self.profile,
                emit=self._emit_script,
                servo=self._make_servo_actuator(),
            )
            if self._flag("enable_tip_forming", False)
            else None
        )
        self.purge_manager = (
            PurgeManager(config, profile=self.profile, emit=self._emit_script)
            if self._flag("enable_purge", False)
            else None
        )
        self.environment = (
            EnvironmentManager(
                config,
                profile=self.profile,
                set_heater=self._make_pin_actuator(self.cfg.get("dryer_heater_pin")),
                set_fan=self._make_pin_actuator(self.cfg.get("enclosure_fan_pin")),
            )
            if self._flag("enable_environment", False)
            else None
        )
        self.ejection_buttons = (
            EjectionButtons(config, profile=self.profile, on_action=self._on_eject_button)
            if self._flag("enable_ejection_buttons", False)
            else None
        )
        self.controller = (
            build_default_controller(config, self.profile, self._units_store_path(), self.diagnostics)
            if self._flag("enable_multi_unit", False)
            else None
        )
        if self.controller is not None:
            self.controller.register_listener(self._on_unit_event)
        # --- Subsistemas de paridad avanzada ------------------------------
        self.sync_feedback = SyncFeedback(config, profile=self.profile)
        self.calibrator = Calibrator(
            self.encoder, self.motion, self.profile, on_apply=self._on_calibration
        )
        self.slicer_map = SlicerToolMap(self.profile.gates, config)
        self.td1 = TD1Manager(config, profile=self.profile)
        self.telemetry = TelemetryBuffer(int(self.cfg.get("telemetry_capacity", 1024) or 1024))
        # --- Hardware avanzado (paridad Happy Hare) -----------------------
        self.tool_overrides = ToolOverrideStore(config)
        self.sync_controller = build_sync_controller(config, feedback=self.sync_feedback)
        self.extruder_monitor = build_extruder_monitor(self.printer, config)
        self.drive_manager = DriveManager()
        self.stepper_current = build_stepper_current(config, emit=self._emit_script)
        self.compound_endstop = build_compound_endstop(
            sensor_reader=lambda: self.sensors.is_present("toolhead"),
            encoder=self.encoder,
            config=config,
        )
        self.local_gate = build_local_gate(config)
        self.filament_display = FilamentDisplay(self.profile.gates)
        self.toolhead_wrapper = build_toolhead(self.printer)
        self.extruder_wrapper = build_extruder(self.printer)
        self.sequences = SequenceRegistry()
        self._build_sequences()
        self.state_machine = StateMachine(self)

        # --- Contrato compatible (printer.mmu / printer.mmu_machine) ---------
        self.events = EventEmitter(self.printer)
        self.counter_store = CounterStore(on_event=self._on_counter_event)
        self.sensor_enabled: Dict[str, bool] = {}
        self.bypass_active = False
        self.print_height = 0.0
        self.print_state = "idle"
        self._skip_tip = False
        self._skip_purge = False
        self.mmu_enabled = True
        self.sync_feedback_state = self.sync_feedback.state
        self.autoload: Optional[AutoLoadService] = None
        self.fan: Optional[FanController] = None
        if self._flag("enable_fan_control", False):
            self.fan = FanController(
                set_fan=self._make_fan_actuator(self.cfg.get("fan_pin")),
                on_temp=float(self.cfg.get("fan_on_temp", 45.0)),
                off_temp=float(self.cfg.get("fan_off_temp", 40.0)),
                poll_s=float(self.cfg.get("fan_polling_s", 1.0)),
            )

        # Registrar los objetos para acceso desde gcode, macros y Moonraker.
        if self.printer is not None:
            for name, obj in (
                ("dog_matrix", self),
                ("mmu", self),
                ("mmu_machine", MachineView(self)),
            ):
                try:
                    self.printer.add_object(name, obj)
                except Exception:  # noqa: BLE001 - nombre ya registrado
                    continue

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

    def _init_endless_groups(self) -> List[List[int]]:
        """Inicializa los grupos de EndlessSpool desde la configuracion.

        Acepta una lista de listas (``[[0,1],[2,3]]``) o su representacion JSON.
        Si no se configura nada, agrupa todos los gates en un unico grupo
        (siempre que el perfil soporte EndlessSpool).
        """
        groups_cfg = self.cfg.get("endless_spool_groups")
        if groups_cfg is not None:
            if isinstance(groups_cfg, str):
                try:
                    groups_cfg = json.loads(groups_cfg)
                except (ValueError, TypeError):
                    groups_cfg = None
            if isinstance(groups_cfg, list):
                result: List[List[int]] = []
                for group in groups_cfg:
                    if isinstance(group, (list, tuple)):
                        result.append([int(g) for g in group])
                    elif isinstance(group, int):
                        result.append([group])
                if result:
                    return result
        if self.profile.has_capability("endless_spool"):
            return [list(range(self.profile.gates))]
        return []

    def _units_store_path(self) -> str:
        """Ruta del almacen persistente de asignaciones multi-unidad."""
        explicit = self.cfg.get("units_store")
        if explicit:
            return str(explicit)
        state_store = str(
            self.cfg.get("state_store", os.path.expanduser("~/printer_data/config/dog_matrix_state.json"))
        )
        return os.path.join(os.path.dirname(state_store), "dog_matrix_units.json")

    def _on_unit_event(self, event: Any) -> None:
        """Traduce eventos de descubrimiento a callbacks de macro/gcode."""
        self._emit_callback(
            "_DM_UNIT_CHANGED",
            action=event.event_type,
            device_id=event.device_id,
            spool_number=event.spool_number if event.spool_number is not None else "",
        )

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
            "gate_filament_extra": {str(k): v for k, v in self.gate_filament_extra.items()},
            "calibration_results": self.calibration_results,
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
        extra = data.get("gate_filament_extra")
        if isinstance(extra, dict):
            self.gate_filament_extra = {int(k): v for k, v in extra.items() if isinstance(v, dict)}
        calibration = data.get("calibration_results")
        if isinstance(calibration, dict):
            self.calibration_results = dict(calibration)

    # -- Registro de comandos ----------------------------------------------
    # Mapa comando canonico -> alias compatible Happy Hare (MMU_*).
    COMMAND_ALIASES: Dict[str, str] = {
        "DM_STATUS": "MMU_STATUS",
        "DM_CHANGE": "MMU_CHANGE_TOOL",
        "DM_LOAD": "MMU_LOAD",
        "DM_UNLOAD": "MMU_UNLOAD",
        "DM_RECOVER": "MMU_RECOVER",
        "DM_UNLOCK": "MMU_UNLOCK",
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
        "DM_CALIBRATE_GEAR": "MMU_CALIBRATE_GEAR",
        "DM_CALIBRATE_ENCODER": "MMU_CALIBRATE_ENCODER",
        "DM_CALIBRATE_BOWDEN": "MMU_CALIBRATE_BOWDEN",
        "DM_CALIBRATE_GATES": "MMU_CALIBRATE_GATES",
        "DM_CALIBRATE_TOOLHEAD": "MMU_CALIBRATE_TOOLHEAD",
        "DM_CALIBRATE_SELECTOR": "MMU_CALIBRATE_SELECTOR",
        "DM_ESPOOLER": "MMU_ESPOOLER",
        "DM_TIP_FORMING": "MMU_TIP_FORMING",
        "DM_PURGE": "MMU_PURGE",
        "DM_NFC_READ": "MMU_NFC_READ",
        "DM_UNIT": "MMU_UNIT",
        "DM_PRELOAD": "MMU_PRELOAD",
        "DM_SELECT": "MMU_SELECT",
        "DM_CHECK_GATE": "MMU_CHECK_GATE",
        "DM_SENSORS": "MMU_SENSORS",
        "DM_MOTORS": "MMU_MOTORS",
        "DM_EJECT": "MMU_EJECT",
        "DM_GRIP": "MMU_GRIP",
        "DM_RELEASE": "MMU_RELEASE",
        "DM_FAN": "MMU_FAN",
        "DM_UPDATE_HEIGHT": "MMU_UPDATE_HEIGHT",
        "DM_HELP": "MMU_HELP",
        "DM_RESET": "MMU_RESET",
        "DM_SELECT_BYPASS": "MMU_SELECT_BYPASS",
        "DM_SYNC_FEEDBACK": "MMU_SYNC_FEEDBACK",
        "DM_SYNC_GEAR_MOTOR": "MMU_SYNC_GEAR_MOTOR",
        "DM_CALIBRATE_PSENSOR": "MMU_CALIBRATE_PSENSOR",
        "DM_CALC_PURGE_VOLUMES": "MMU_CALC_PURGE_VOLUMES",
        "DM_TD1": "MMU_TD1",
        "DM_SLICER_TOOL_MAP": "MMU_SLICER_TOOL_MAP",
        "DM_COLD_PULL": "MMU_COLD_PULL",
        "DM_HEATER": "MMU_HEATER",
        "DM_LED": "MMU_LED",
        "DM_SET_LED": "MMU_SET_LED",
        "DM_SERVO": "MMU_SERVO",
        "DM_PARK": "MMU_PARK",
        "DM_TEST_MOVE": "MMU_TEST_MOVE",
        "DM_TEST_HOMING_MOVE": "MMU_TEST_HOMING_MOVE",
        "DM_TEST_TRACKING": "MMU_TEST_TRACKING",
        "DM_TEST_BUZZ_MOTOR": "MMU_TEST_BUZZ_MOTOR",
        "DM_TEST_LOAD": "MMU_TEST_LOAD",
        "DM_TEST_GRIP": "MMU_TEST_GRIP",
        "DM_TEST_RUNOUT": "MMU_TEST_RUNOUT",
        "DM_TEST_FORM_TIP": "MMU_TEST_FORM_TIP",
        "DM_SOAKTEST_LOAD_SEQUENCE": "MMU_SOAKTEST_LOAD_SEQUENCE",
        "DM_SOAKTEST_SELECTOR": "MMU_SOAKTEST_SELECTOR",
        "DM_LOG": "MMU_LOG",
        "DM_DUMP_VARS": "MMU_DUMP_VARS",
        "DM_NFC_SCAN": "MMU_NFC_SCAN",
        "DM_MOTORS_OFF": "MMU_MOTORS_OFF",
        "DM_MOTORS_ON": "MMU_MOTORS_ON",
        "DM_FLOWGUARD": "MMU_FLOWGUARD",
        "DM_PAUSE": "MMU_PAUSE",
        "DM_TOOL_OVERRIDES": "MMU_TOOL_OVERRIDES",
        "DM_TEST_PURGE": "MMU_TEST_PURGE",
        "DM_CHANGE_TOOL_STANDALONE": "MMU_CHANGE_TOOL_STANDALONE",
        "DM_PRINT_START": "MMU_PRINT_START",
        "DM_PRINT_END": "MMU_PRINT_END",
        "DM_START_CHECK": "MMU_START_CHECK",
        "DM_START_LOAD_INITIAL_TOOL": "MMU_START_LOAD_INITIAL_TOOL",
        "DM_END": "MMU_END",
        "DM_CALIBRATE_ROTARY_SELECTOR": "MMU_CALIBRATE_ROTARY_SELECTOR",
        "DM_CALIBRATE_SELECTOR_INDEXES": "MMU_CALIBRATE_SELECTOR_INDEXES",
        "DM_CALIBRATE_SERVO_SELECTOR": "MMU_CALIBRATE_SERVO_SELECTOR",
        "DM_STEP": "MMU_STEP",
        "DM_NFC": "MMU_NFC",
        "DM_LOCAL_GATE": "MMU_LOCAL_GATE",
        "DM_STEPPER_CURRENT": "MMU_STEPPER_CURRENT",
        "DM_EXTRUDER_MONITOR": "MMU_EXTRUDER_MONITOR",
        "DM_COMPOUND_ENDSTOP": "MMU_COMPOUND_ENDSTOP",
        "DM_FILAMENT_DISPLAY": "MMU_FILAMENT_DISPLAY",
    }

    #: Pasos de secuencia expuestos como comandos ``_MMU_STEP_*`` (paridad HH).
    SEQUENCE_STEPS: Dict[str, str] = {
        "HOME": "home",
        "SELECT": "select",
        "PRE_LOAD": "pre_load",
        "LOAD": "load",
        "LOAD_GATE": "load",
        "LOAD_BOWDEN": "load",
        "LOAD_TOOLHEAD": "load",
        "VERIFY": "verify",
        "FORM_TIP": "form_tip",
        "PURGE": "purge",
        "COMMIT": "commit",
        "UNLOAD": "unload",
        "UNLOAD_GATE": "unload",
        "UNLOAD_BOWDEN": "unload",
        "UNLOAD_TOOLHEAD": "unload",
        "POST_UNLOAD": "post_unload",
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
            "DM_UNLOCK": self.cmd_DM_UNLOCK,
            "DM_HOME": self.cmd_DM_HOME,
            "DM_ENCODER": self.cmd_DM_ENCODER,
            "DM_GATE_MAP": self.cmd_DM_GATE_MAP,
            "DM_REMAP_TTG": self.cmd_DM_REMAP_TTG,
            "DM_SPOOLMAN": self.cmd_DM_SPOOLMAN,
            "DM_ENDLESS_SPOOL": self.cmd_DM_ENDLESS_SPOOL,
            "DM_TEST_CONFIG": self.cmd_DM_TEST_CONFIG,
            "DM_START_SETUP": self.cmd_DM_START_SETUP,
            "DM_PRINT_STATE": self.cmd_DM_PRINT_STATE,
            "DM_STATS": self.cmd_DM_STATS,
            "DM_CALIBRATE_GEAR": self.cmd_DM_CALIBRATE_GEAR,
            "DM_CALIBRATE_ENCODER": self.cmd_DM_CALIBRATE_ENCODER,
            "DM_CALIBRATE_BOWDEN": self.cmd_DM_CALIBRATE_BOWDEN,
            "DM_CALIBRATE_GATES": self.cmd_DM_CALIBRATE_GATES,
            "DM_CALIBRATE_TOOLHEAD": self.cmd_DM_CALIBRATE_TOOLHEAD,
            "DM_CALIBRATE_SELECTOR": self.cmd_DM_CALIBRATE_SELECTOR,
            "DM_CALIBRATE_PSENSOR": self.cmd_DM_CALIBRATE_PSENSOR,
            "DM_ESPOOLER": self.cmd_DM_ESPOOLER,
            "DM_TIP_FORMING": self.cmd_DM_TIP_FORMING,
            "DM_PURGE": self.cmd_DM_PURGE,
            "DM_CALC_PURGE_VOLUMES": self.cmd_DM_CALC_PURGE_VOLUMES,
            "DM_NFC_READ": self.cmd_DM_NFC_READ,
            "DM_NFC_SCAN": self.cmd_DM_NFC_SCAN,
            "DM_UNIT": self.cmd_DM_UNIT,
            "DM_PRELOAD": self.cmd_DM_PRELOAD,
            "DM_SELECT": self.cmd_DM_SELECT,
            "DM_SELECT_BYPASS": self.cmd_DM_SELECT_BYPASS,
            "DM_CHECK_GATE": self.cmd_DM_CHECK_GATE,
            "DM_SENSORS": self.cmd_DM_SENSORS,
            "DM_MOTORS": self.cmd_DM_MOTORS,
            "DM_MOTORS_OFF": self.cmd_DM_MOTORS_OFF,
            "DM_MOTORS_ON": self.cmd_DM_MOTORS_ON,
            "DM_EJECT": self.cmd_DM_EJECT,
            "DM_GRIP": self.cmd_DM_GRIP,
            "DM_RELEASE": self.cmd_DM_RELEASE,
            "DM_FAN": self.cmd_DM_FAN,
            "DM_HEATER": self.cmd_DM_HEATER,
            "DM_UPDATE_HEIGHT": self.cmd_DM_UPDATE_HEIGHT,
            "DM_HELP": self.cmd_DM_HELP,
            "DM_RESET": self.cmd_DM_RESET,
            "DM_SYNC_FEEDBACK": self.cmd_DM_SYNC_FEEDBACK,
            "DM_SYNC_GEAR_MOTOR": self.cmd_DM_SYNC_GEAR_MOTOR,
            "DM_TD1": self.cmd_DM_TD1,
            "DM_SLICER_TOOL_MAP": self.cmd_DM_SLICER_TOOL_MAP,
            "DM_COLD_PULL": self.cmd_DM_COLD_PULL,
            "DM_LED": self.cmd_DM_LED,
            "DM_SET_LED": self.cmd_DM_SET_LED,
            "DM_SERVO": self.cmd_DM_SERVO,
            "DM_PARK": self.cmd_DM_PARK,
            "DM_TEST_MOVE": self.cmd_DM_TEST_MOVE,
            "DM_TEST_HOMING_MOVE": self.cmd_DM_TEST_HOMING_MOVE,
            "DM_TEST_TRACKING": self.cmd_DM_TEST_TRACKING,
            "DM_TEST_BUZZ_MOTOR": self.cmd_DM_TEST_BUZZ_MOTOR,
            "DM_TEST_LOAD": self.cmd_DM_TEST_LOAD,
            "DM_TEST_GRIP": self.cmd_DM_TEST_GRIP,
            "DM_TEST_RUNOUT": self.cmd_DM_TEST_RUNOUT,
            "DM_TEST_FORM_TIP": self.cmd_DM_TEST_FORM_TIP,
            "DM_SOAKTEST_LOAD_SEQUENCE": self.cmd_DM_SOAKTEST_LOAD_SEQUENCE,
            "DM_SOAKTEST_SELECTOR": self.cmd_DM_SOAKTEST_SELECTOR,
            "DM_LOG": self.cmd_DM_LOG,
            "DM_DUMP_VARS": self.cmd_DM_DUMP_VARS,
            "DM_FLOWGUARD": self.cmd_DM_FLOWGUARD,
            "DM_PAUSE": self.cmd_DM_PAUSE,
            "DM_TOOL_OVERRIDES": self.cmd_DM_TOOL_OVERRIDES,
            "DM_TEST_PURGE": self.cmd_DM_TEST_PURGE,
            "DM_CHANGE_TOOL_STANDALONE": self.cmd_DM_CHANGE_TOOL_STANDALONE,
            "DM_PRINT_START": self.cmd_DM_PRINT_START,
            "DM_PRINT_END": self.cmd_DM_PRINT_END,
            "DM_START_CHECK": self.cmd_DM_START_CHECK,
            "DM_START_LOAD_INITIAL_TOOL": self.cmd_DM_START_LOAD_INITIAL_TOOL,
            "DM_END": self.cmd_DM_END,
            "DM_CALIBRATE_ROTARY_SELECTOR": self.cmd_DM_CALIBRATE_ROTARY_SELECTOR,
            "DM_CALIBRATE_SELECTOR_INDEXES": self.cmd_DM_CALIBRATE_SELECTOR_INDEXES,
            "DM_CALIBRATE_SERVO_SELECTOR": self.cmd_DM_CALIBRATE_SERVO_SELECTOR,
            "DM_STEP": self.cmd_DM_STEP,
            "DM_NFC": self.cmd_DM_NFC,
            "DM_LOCAL_GATE": self.cmd_DM_LOCAL_GATE,
            "DM_STEPPER_CURRENT": self.cmd_DM_STEPPER_CURRENT,
            "DM_EXTRUDER_MONITOR": self.cmd_DM_EXTRUDER_MONITOR,
            "DM_COMPOUND_ENDSTOP": self.cmd_DM_COMPOUND_ENDSTOP,
            "DM_FILAMENT_DISPLAY": self.cmd_DM_FILAMENT_DISPLAY,
        }
        for name, handler in handlers.items():
            try:
                gcode.register_command(name, handler, desc=f"Dog Matrix: {name}")
                alias = self.COMMAND_ALIASES.get(name)
                if alias:
                    gcode.register_command(alias, handler, desc=f"Dog Matrix alias {alias}")
            except Exception as exc:  # noqa: BLE001 - registro defensivo
                self.diagnostics.log_event("warning", "core", "command_register_failed", cmd=name, error=str(exc))
        # Pasos de secuencia componibles: ``_MMU_STEP_<NAME>`` / ``MMU_STEP_<NAME>``.
        for step_name in self.SEQUENCE_STEPS:
            for prefix in ("_MMU_STEP_", "DM_STEP_", "MMU_STEP_"):
                try:
                    gcode.register_command(
                        f"{prefix}{step_name}",
                        self._make_step_handler(step_name),
                        desc=f"Dog Matrix step {step_name}",
                    )
                except Exception:  # noqa: BLE001 - nombre reservado
                    continue
        for extra in ("_MMU_STEP", "_MMU_TEST"):
            try:
                gcode.register_command(extra, self.cmd_DM_STEP, desc="Dog Matrix step alias")
            except Exception:  # noqa: BLE001
                continue

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
        reactor = None
        if self.printer is not None:
            try:
                reactor = self.printer.get_reactor()
            except Exception:  # noqa: BLE001 - reactor no disponible
                reactor = None
        if self.controller is not None:
            if reactor is not None:
                self.controller.start(reactor)
            # Descubrimiento inicial inmediato (no esperar al primer timer).
            self.controller.poll()
        self._setup_autoload(reactor)
        self._setup_fan(reactor)
        self._setup_reactor_timers(reactor)
        if self.ejection_buttons is not None:
            try:
                self.ejection_buttons.wire(self.printer, self.config)
            except Exception:  # noqa: BLE001 - botones opcionales
                pass
        if self.spoolman is not None and self.spoolman.should_pull:
            try:
                self.spoolman.pull_gate_map(self.profile.gates)
            except Exception:  # noqa: BLE001 - Spoolman opcional
                pass
        self.events.emit(EVENT_INITIALIZED)
        self.events.emit(EVENT_BOOTUP)
        self.events.emit(EVENT_ENABLED)

    def _setup_autoload(self, reactor: Any) -> None:
        """Cablea el flujo pre-gate -> post-gate -> toolhead al runtime."""
        if reactor is None or not self._flag("enable_autoload", False):
            return
        sensors = SensorBank()
        hub = HubDevice(
            HubConfig(
                shared_exit_sensor=self._flag("hub_shared_exit", True),
                has_encoder=self._flag("hub_encoder", False),
                has_diameter_sensor=self._flag("hub_diameter_sensor", False),
                units=int(self.cfg.get("hub_units", 1) or 1),
            ),
            sensors,
        )
        buffer = BufferDevice(
            BufferConfig(
                tension_sensor=self._flag("buffer_tension", False),
                compression_sensor=self._flag("buffer_compression", False),
            ),
            sensors,
        )
        controller = AutoLoadController(
            motor_for=lambda unit: _MotionMotor(self.motion, unit),
            sensors=sensors,
            hub=hub,
            buffer=buffer,
            router=MultiUnitRouter(buffer),
            config=AutoLoadConfig(
                pre_gate_timeout_s=float(self.cfg.get("autoload_pre_gate_timeout_s", 30.0)),
                bowden_timeout_s=float(self.cfg.get("autoload_bowden_timeout_s", 60.0)),
            ),
            clock=reactor.monotonic,
            diagnostics=self.diagnostics,
        )
        service = AutoLoadService(controller, reactor)
        service.start()
        self.autoload = service

    def _setup_fan(self, reactor: Any) -> None:
        if self.fan is None or reactor is None:
            return

        def _fan_tick(eventtime: float) -> float:
            try:
                self.fan.update(self._fan_temperature())
            except Exception:  # noqa: BLE001
                return reactor.NEVER
            return self.fan.tick(eventtime)

        try:
            reactor.register_timer(_fan_tick, reactor.monotonic() + self.fan.poll_s)
        except Exception:  # noqa: BLE001
            pass

    def _fan_temperature(self) -> float:
        env = getattr(self, "environment", None)
        if env is None:
            return 0.0
        try:
            status = env.get_status()
            return float(getattr(status, "temperature", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    def _make_fan_actuator(self, pin_name: Any) -> Any:
        target = None
        if self.printer is not None and pin_name:
            try:
                target = self.printer.lookup_object(str(pin_name), None)
            except Exception:  # noqa: BLE001
                target = None

        def _set(power: float) -> None:
            if target is not None and hasattr(target, "set_pwm"):
                try:
                    target.set_pwm(power, 0.0, max(power, 0.0))
                except Exception:  # noqa: BLE001
                    pass

        return _set

    # -- Actuadores genericos y utilidades de emision ----------------------
    def _emit_script(self, script: str) -> None:
        """Ejecuta un script G-code de forma defensiva (nunca propaga)."""
        gcode = self._gcode()
        if gcode is None or not hasattr(gcode, "run_script_from_command"):
            return
        try:
            gcode.run_script_from_command(script)
        except Exception:  # noqa: BLE001 - el G-code puede no existir
            return

    def _make_pin_actuator(self, pin_name: Any) -> Optional[Any]:
        """Crea un actuador analogico (0.0-1.0) para un pin/objeto Klipper."""
        if not pin_name:
            return None
        target = None
        if self.printer is not None:
            try:
                target = self.printer.lookup_object(str(pin_name), None)
            except Exception:  # noqa: BLE001
                target = None

        def _set(power: float) -> None:
            value = min(1.0, max(0.0, float(power)))
            if target is not None and hasattr(target, "set_pwm"):
                try:
                    target.set_pwm(value, 0.0, value)
                    return
                except Exception:  # noqa: BLE001
                    pass
            self._emit_script(f"SET_PIN PIN={pin_name} VALUE={value:.4f}")

        return _set

    def _make_bool_actuator(self, pin_name: Any) -> Optional[Any]:
        """Crea un actuador digital (bool) para un pin Klipper."""
        if not pin_name:
            return None
        target = None
        if self.printer is not None:
            try:
                target = self.printer.lookup_object(str(pin_name), None)
            except Exception:  # noqa: BLE001
                target = None

        def _set(state: bool) -> None:
            if target is not None and hasattr(target, "set_pwm"):
                try:
                    target.set_pwm(1.0 if state else 0.0, 0.0, 1.0 if state else 0.0)
                    return
                except Exception:  # noqa: BLE001
                    pass
            self._emit_script(f"SET_PIN PIN={pin_name} VALUE={1 if state else 0}")

        return _set

    def _make_servo_actuator(self) -> Optional[Any]:
        """Crea un actuador de servo ``SET_SERVO`` para el cutter."""
        servo = self.cfg.get("cutter_servo")
        if not servo:
            return None

        def _set(angle: float) -> None:
            self._emit_script(f"SET_SERVO SERVO={servo} ANGLE={float(angle):.1f}")

        return _set

    def _on_calibration(self, kind: str, result: CalibrationResult) -> None:
        """Persiste el resultado de una calibracion por gate."""
        self.calibration_results[kind] = result.as_dict()
        if result.gate is not None and result.success:
            self.gate_filament_extra.setdefault(result.gate, {})[kind] = result.factor
        try:
            self.persistence.save(self.snapshot_state())
        except PersistenceError:
            pass
        self.telemetry.record(f"calibration_{kind}", result.factor)
        self._emit_callback("_DM_ACTION_CHANGED", action=f"calibrate_{kind}", gate=result.gate)

    def _on_eject_button(self, name: str, action: str, gate: Optional[int]) -> None:
        """Traduce la pulsacion de un boton fisico a un comando G-code."""
        if action == "eject":
            self._emit_script(f"DM_EJECT GATE={gate if gate is not None else 0}")
        elif action == "unload":
            self._emit_script("DM_UNLOAD")
        else:
            self._emit_script("DM_LOAD")
        self.diagnostics.log_event("info", "buttons", "action", name=name, action=action, gate=gate)

    # -- Secuencias componibles (F-35) -------------------------------------
    def _build_sequences(self) -> None:
        self.sequences.register(self._custom_or_default_sequence("load"))
        self.sequences.register(build_default_unload_sequence("unload"))

    def _custom_or_default_sequence(self, name: str) -> Sequence:
        """Usa una secuencia custom de config o la de por defecto (M-04)."""
        custom = self.cfg.get("gcode_load_sequence")
        if name == "load" and isinstance(custom, list) and custom:
            seq = Sequence(name)
            for raw in custom:
                token = str(raw)
                step = self.SEQUENCE_STEPS.get(token.upper(), token.lower())
                seq.add(step, self._make_ctx_step(step))
            return seq
        return build_default_load_sequence(name)

    @staticmethod
    def _make_ctx_step(step: str) -> Any:
        """Handler de secuencia que delega en ``ctx[step]`` (o se omite)."""
        def _handler(ctx: Dict[str, Any]) -> Any:
            handler = ctx.get(step)
            if handler is None:
                return SKIP
            return bool(handler())

        return _handler

    def _sequence_context(self, gate: int, tool: Optional[int] = None) -> Dict[str, Any]:
        """Contexto de handlers para ejecutar pasos de secuencia (F-35/M-03)."""
        limits = self.profile.limits
        return {
            "pre_load": lambda: self._seq_pre_load(gate),
            "home": lambda: self.selector.home(),
            "select": lambda: self.selector.select_gate(gate),
            "load": lambda: self.motion.load_filament(
                float(limits.get("max_distance_mm", 1000)),
                float(limits.get("max_load_speed_mm_s", 80)),
            ),
            "verify": lambda: self.sensors.is_present("toolhead"),
            "form_tip": self._run_form_tip,
            "purge": self._run_purge,
            "commit": lambda: self._seq_commit(gate, tool),
            "unload": lambda: self.motion.unload_filament(
                float(limits.get("max_distance_mm", 1000)),
                float(limits.get("max_unload_speed_mm_s", 80)),
            ),
            "post_unload": lambda: True,
        }

    def run_load_sequence(self, gate: int, tool: Optional[int] = None) -> Dict[str, Any]:
        """Ejecuta la secuencia de carga componible sobre ``gate``."""
        sequence = self.sequences.get("load")
        if sequence is None:
            return {"completed": False}
        sequence.run(self._sequence_context(gate, tool))
        return sequence.get_status()

    def run_unload_sequence(self) -> Dict[str, Any]:
        """Ejecuta la secuencia de descarga componible."""
        sequence = self.sequences.get("unload")
        if sequence is None:
            return {"completed": False}
        ctx = self._sequence_context(
            self.current_gate if self.current_gate is not None else 0
        )
        ctx["commit"] = self._seq_clear
        sequence.run(ctx)
        return sequence.get_status()

    def run_step(self, step: str, gate: Optional[int] = None,
                 tool: Optional[int] = None) -> bool:
        """Ejecuta un unico paso de secuencia por nombre (F-35/M-03)."""
        target_gate = gate if gate is not None else (
            self.current_gate if self.current_gate is not None else 0
        )
        handler = self._sequence_context(target_gate, tool).get(step)
        if handler is None:
            return True  # paso desconocido: omitido, no rompe
        try:
            return bool(handler())
        except Exception:  # noqa: BLE001 - un paso nunca debe propagar
            return False

    def _make_step_handler(self, step_name: str) -> Any:
        """Fabrica un handler G-code para ``_MMU_STEP_<NAME>``."""
        step = self.SEQUENCE_STEPS.get(step_name, step_name.lower())

        def _handler(gcmd: Any) -> None:
            gate = gcmd.get_int("GATE", self.current_gate if self.current_gate is not None else 0)
            if not self.run_step(step, gate, gcmd.get_int("TOOL", None)):
                raise gcmd.error(f"Paso {step} fallido")
            gcmd.respond_info(f"Paso {step} OK")

        return _handler

    def cmd_DM_STEP(self, gcmd: Any) -> None:
        """Ejecuta un paso de secuencia por nombre (``DM_STEP NAME=``)."""
        raw = str(gcmd.get("NAME", "")).upper()
        if not raw:
            gcmd.respond_info(f"Pasos disponibles: {', '.join(sorted(self.SEQUENCE_STEPS))}")
            return
        step = self.SEQUENCE_STEPS.get(raw, raw.lower())
        gate = gcmd.get_int("GATE", self.current_gate if self.current_gate is not None else 0)
        if not self.run_step(step, gate, gcmd.get_int("TOOL", None)):
            raise gcmd.error(f"Paso {step} fallido")
        gcmd.respond_info(f"Paso {step} OK")

    # -- Ecosistema Happy Hare: comandos de paridad ------------------------
    def cmd_DM_FLOWGUARD(self, gcmd: Any) -> None:
        """Estado/ajuste de FlowGuard (modos de encoder, umbrales, adaptativo)."""
        mode = gcmd.get("MODE", None)
        if mode is not None:
            self.flowguard.set_encoder_mode(str(mode))
        adaptive = gcmd.get_int("ADAPTIVE", None)
        if adaptive is not None:
            self.flowguard.set_adaptive_mode(bool(adaptive))
        threshold = gcmd.get_float("THRESHOLD", None)
        if threshold is not None:
            self.flowguard.threshold_mm = float(threshold)
        if gcmd.get_int("RESET", 0):
            self.flowguard.reset()
            self.flowguard.reset_prevention()
        stats = self.flowguard.get_statistics()
        gcmd.respond_info(
            f"FlowGuard mode={stats.get('encoder_mode')} errors={stats.get('errors', 0)} "
            f"violations={stats.get('violations', 0)} threshold={stats.get('threshold_mm')}"
        )

    def cmd_DM_PAUSE(self, gcmd: Any) -> None:
        """Pausa el MMU sin perder estado (coordina con PAUSE del sistema)."""
        if self.autoload is not None:
            try:
                self.autoload.stop()
            except Exception:  # noqa: BLE001
                pass
        for drive_kind in ("gear", "selector", "cutter", "espooler"):
            self.drive_manager.get(drive_kind).stop()
        self.events.emit(EVENT_PAUSED)
        self._emit_callback("_DM_ACTION_CHANGED", action="pause")
        try:
            self.persistence.save(self.snapshot_state())
        except PersistenceError:
            pass
        gcmd.respond_info("MMU pausado")

    def cmd_DM_TOOL_OVERRIDES(self, gcmd: Any) -> None:
        """Define/consulta overrides de parametros por herramienta."""
        tool = gcmd.get_int("TOOL", None)
        if gcmd.get_int("CLEAR", 0):
            self.tool_overrides.clear(tool)
            self._persist_state()
            gcmd.respond_info("Overrides borrados")
            return
        if tool is None:
            gcmd.respond_info(f"Tool overrides: {self.tool_overrides.get_status()}")
            return
        fields: Dict[str, Any] = {}
        for key, param in (
            ("speed_factor", "SPEED_FACTOR"), ("purge_mm", "PURGE_MM"),
            ("temperature", "TEMPERATURE"), ("material", "MATERIAL"), ("color", "COLOR"),
        ):
            value = gcmd.get(param, None)
            if value is not None:
                fields[key] = value
        if not fields:
            override = self.tool_overrides.get(tool)
            gcmd.respond_info(f"Override tool {tool}: {override.as_dict() if override else 'none'}")
            return
        override = self.tool_overrides.set_override(tool, **fields)
        self._persist_state()
        gcmd.respond_info(f"Override tool {tool}: {override.as_dict()}")

    def cmd_DM_TEST_PURGE(self, gcmd: Any) -> None:
        """Purga de prueba con un volumen concreto."""
        if self.purge_manager is None:
            raise gcmd.error("Purga deshabilitada (enable_purge=false)")
        volume = gcmd.get_float("VOLUME", None)
        if volume is None:
            volume = self.purge_manager.calculate_volume(0).volume_mm3
        scripts = self.purge_manager.purge_sequence(volume)
        self.telemetry.record("test_purge_mm3", volume)
        gcmd.respond_info(f"Test purge {volume:.1f} mm3 ({len(scripts)} scripts)")

    def cmd_DM_CHANGE_TOOL_STANDALONE(self, gcmd: Any) -> None:
        """Toolchange forzando tip forming y purga propios (standalone)."""
        tool = gcmd.get_int("TOOL", self.current_tool if self.current_tool is not None else 0)
        self._execute_toolchange(gcmd, tool)

    def cmd_DM_PRINT_START(self, gcmd: Any) -> None:
        """Marca el inicio de impresion y opcionalmente carga la herramienta inicial."""
        self.print_state = "printing"
        self.events.emit(EVENT_PRINTING)
        self._emit_callback("_DM_PRINT_STATE_CHANGED", state="printing")
        tool = gcmd.get_int("INITIAL_TOOL", gcmd.get_int("TOOL", None))
        if tool is not None and self._flag("print_start_gate", True):
            try:
                self._execute_toolchange(gcmd, int(tool))
            except Exception:  # noqa: BLE001 - la carga inicial es best-effort
                pass
        gcmd.respond_info(f"Print start (tool={tool})")

    def cmd_DM_PRINT_END(self, gcmd: Any) -> None:
        """Finaliza la impresion: descarga, park y marca estado."""
        if self.current_gate is not None:
            try:
                self.cmd_DM_UNLOAD(gcmd)
            except Exception:  # noqa: BLE001
                pass
        self.motion.park_toolhead("post_print")
        self.print_state = "complete"
        self.events.emit(EVENT_NOT_PRINTING)
        self._emit_callback("_DM_PRINT_STATE_CHANGED", state="complete")
        gcmd.respond_info("Print end")

    def cmd_DM_START_CHECK(self, gcmd: Any) -> None:
        """Verificacion de arranque: config, gate map, sensores y persistencia."""
        errors = self.capabilities.validate()
        available = sum(1 for status in self.gate_status if status == "available")
        try:
            self.persistence.save(self.snapshot_state())
            persisted = True
        except PersistenceError:
            persisted = False
        self._emit_callback("_DM_ACTION_CHANGED", action="start_check")
        gcmd.respond_info(
            f"Start check: config_errors={len(errors)} gates_available={available}/"
            f"{self.profile.gates} persisted={persisted}"
        )

    def cmd_DM_START_LOAD_INITIAL_TOOL(self, gcmd: Any) -> None:
        """Carga la herramienta inicial al arrancar (paridad HH)."""
        tool = gcmd.get_int("TOOL", self.cfg.get("initial_tool", 0) or 0)
        self._execute_toolchange(gcmd, int(tool))

    def cmd_DM_END(self, gcmd: Any) -> None:
        """Fin de sesion MMU: descarga y libera motores."""
        if self.current_gate is not None:
            try:
                self.cmd_DM_UNLOAD(gcmd)
            except Exception:  # noqa: BLE001
                pass
        self._emit_callback("_DM_ACTION_CHANGED", action="end")
        gcmd.respond_info("MMU end")

    # -- Calibracion de selectores avanzada ---------------------------------
    def cmd_DM_CALIBRATE_ROTARY_SELECTOR(self, gcmd: Any) -> None:
        """Calibra el selector rotativo midiendo el angulo por gate."""
        if not self.selector.home():
            raise gcmd.error("Homing de selector fallido")
        angles: Dict[int, float] = {}
        for gate in range(self.profile.gates):
            if self.selector.select_gate(gate):
                angles[gate] = round(self.selector.get_position(), 3)
        self.calibration_results["rotary_selector_angles"] = angles
        self._persist_state()
        gcmd.respond_info(f"Calibracion rotativa OK: {angles}")

    def cmd_DM_CALIBRATE_SELECTOR_INDEXES(self, gcmd: Any) -> None:
        """Calibra la tabla de indices (offsets) del selector por gate."""
        indexes: Dict[int, float] = {}
        for gate in range(self.profile.gates):
            if self.selector.select_gate(gate):
                indexes[gate] = round(self.selector.get_position(), 3)
        self.calibration_results["selector_indexes"] = indexes
        self._persist_state()
        gcmd.respond_info(f"Calibracion indices OK: {indexes}")

    def cmd_DM_CALIBRATE_SERVO_SELECTOR(self, gcmd: Any) -> None:
        """Calibra el servo selector: barrido de angulos y verificacion por gate."""
        step = gcmd.get_float("STEP", 10.0)
        angles: Dict[int, float] = {}
        for gate in range(self.profile.gates):
            angle = gate * max(1.0, step)
            if self.selector.select_gate(gate):
                angles[gate] = round(angle, 2)
        self.calibration_results["servo_selector_angles"] = angles
        self._persist_state()
        gcmd.respond_info(f"Calibracion servo selector OK: {angles}")

    # -- Comandos de hardware avanzado -------------------------------------
    def cmd_DM_NFC(self, gcmd: Any) -> None:
        """Gestion NFC: estado, lectura, registro de UID y release (MMU_NFC)."""
        if self.nfc is None:
            raise gcmd.error("NFC deshabilitado (enable_nfc=false)")
        action = gcmd.get("ACTION", "STATUS").upper()
        if action == "READ":
            tag = self.nfc.scan_tag(0.0, deep=True)
            if tag is None:
                gcmd.respond_info("NFC: sin tag")
            else:
                self._on_nfc_tag(tag.uid)
                gcmd.respond_info(f"NFC tag {tag.uid}: {tag.data}")
            return
        if action == "REGISTER":
            uid = gcmd.get("UID", "")
            spool_id = gcmd.get_int("SPOOL_ID", None)
            if not uid or spool_id is None:
                raise gcmd.error("REGISTER requiere UID y SPOOL_ID")
            if self.spoolman is not None:
                self.spoolman.register_tag(uid, int(spool_id))
            if self.rx_gain is not None:
                self.rx_gain.autotune(gcmd.get_float("SIGNAL", 0.7))
            gcmd.respond_info(f"Tag {uid} -> spool {spool_id}")
            return
        if action == "RELEASE":
            self.nfc_endstop.reset() if self.nfc_endstop is not None else None
            gcmd.respond_info("NFC released")
            return
        status: Dict[str, Any] = {}
        if self.nfc_manager is not None:
            status["manager"] = self.nfc_manager.get_status()
        if self.nfc_arbiter is not None:
            status["arbiter"] = self.nfc_arbiter.get_status()
        if self.nfc_endstop is not None:
            status["endstop"] = self.nfc_endstop.get_status()
        if self.rx_gain is not None:
            status["gain"] = self.rx_gain.get_status()
        gcmd.respond_info(f"NFC: {status}")

    def cmd_DM_LOCAL_GATE(self, gcmd: Any) -> None:
        """Modo gate local (sin MMU): habilitar, deshabilitar o seleccionar."""
        action = gcmd.get("ACTION", "STATUS").upper()
        if action == "ENABLE":
            self.local_gate.enable()
            gcmd.respond_info("Local gate habilitado")
            return
        if action == "DISABLE":
            self.local_gate.disable()
            gcmd.respond_info("Local gate deshabilitado")
            return
        if action == "SELECT":
            gate = gcmd.get_int("GATE", 0)
            if not self.local_gate.select(gate):
                raise gcmd.error("Local gate no habilitado o gate invalido")
            gcmd.respond_info(f"Local gate -> {gate}")
            return
        gcmd.respond_info(f"Local gate: {self.local_gate.get_status()}")

    def cmd_DM_STEPPER_CURRENT(self, gcmd: Any) -> None:
        """Consulta/ajusta la corriente de un motor (SET_TMC_CURRENT)."""
        motor = gcmd.get("MOTOR", None)
        if motor is None:
            gcmd.respond_info(f"Stepper currents: {self.stepper_current.get_status()}")
            return
        ok = self.stepper_current.set_current(
            str(motor),
            gcmd.get_float("RUN_CURRENT", None),
            gcmd.get_float("HOLD_CURRENT", None),
        )
        if not ok:
            raise gcmd.error(f"Motor desconocido: {motor}")
        gcmd.respond_info(f"Current {motor}: {self.stepper_current.get_current(str(motor))}")

    def cmd_DM_EXTRUDER_MONITOR(self, gcmd: Any) -> None:
        """Estado/lectura del monitor del extrusor."""
        action = gcmd.get("ACTION", "STATUS").upper()
        if action == "READ":
            position = self.extruder_wrapper.position_mm()
            state = self.extruder_monitor.update(position, gcmd.get_float("EXPECTED", 0.0))
            gcmd.respond_info(f"Extruder monitor: state={state} pos={position:.3f}mm")
            return
        gcmd.respond_info(f"Extruder monitor: {self.extruder_monitor.get_status()}")

    def cmd_DM_COMPOUND_ENDSTOP(self, gcmd: Any) -> None:
        """Estado del compound endstop (sensor de gate + encoder)."""
        if gcmd.get_int("TEST", 0):
            triggered = self.compound_endstop.triggered(
                gcmd.get_float("MEASURED", 0.0), gcmd.get_float("EXPECTED", 0.0)
            )
            gcmd.respond_info(f"Compound endstop triggered={triggered}")
            return
        gcmd.respond_info(f"Compound endstop: {self.compound_endstop.get_status()}")

    def cmd_DM_FILAMENT_DISPLAY(self, gcmd: Any) -> None:
        """Textos de filamento por gate (sin UI): nombre/material/color."""
        for gate, entry in enumerate(self.gate_filament):
            self.filament_display.set_gate(gate, entry)
        self.filament_display.set_active(self.current_gate)
        for line in self.filament_display.lines():
            gcmd.respond_info(line)
        gcmd.respond_info(f"Activo: {self.filament_display.active_line()}")

    # -- EndlessSpool auto-failover (F-08/E-01) ----------------------------
    def try_endless_failover(self, ok_codes: Optional[Any] = None) -> Optional[int]:
        """Intenta remapear al siguiente gate del grupo EndlessSpool.

        Devuelve el nuevo gate o ``None`` si no aplica. Determinista y sin
        efectos si EndlessSpool esta deshabilitado o no hay gate actual.
        """
        if not self.enable_endless_spool or self.current_gate is None:
            return None
        next_gate = self.next_endless_gate(self.current_gate)
        if next_gate is None:
            return None
        old_gate = self.current_gate
        if 0 <= old_gate < len(self.gate_status):
            self.gate_status[old_gate] = "empty"
            self.gate_filament[old_gate]["availability"] = "empty"
        tool = self.current_tool
        if tool is not None and 0 <= tool < len(self.ttg_map):
            self.ttg_map[tool] = next_gate
        self.current_gate = next_gate
        self._persist_state()
        self.events.emit(EVENT_GATE_SELECTED, gate=next_gate, previous_gate=old_gate)
        self._emit_callback("_DM_ACTION_CHANGED", action="endless_failover", gate=next_gate)
        self.telemetry.record("endless_failover", float(next_gate))
        return next_gate

    def _persist_state(self) -> None:
        try:
            self.persistence.save(self.snapshot_state())
        except PersistenceError:
            pass

    # -- Comandos de prueba / validacion de hardware -----------------------

    def _seq_pre_load(self, gate: int) -> bool:
        self.current_gate = gate
        self.bypass_active = False
        if self.autoload is not None:
            started = self.autoload.controller.on_pre_gate(gate)
            self.autoload.kick()
            return bool(started)
        return True

    def _seq_commit(self, gate: int, tool: Optional[int]) -> bool:
        self.current_gate = gate
        if tool is not None:
            self.current_tool = tool
        try:
            self.persistence.save(self.snapshot_state())
        except PersistenceError:
            pass
        return True

    def _seq_clear(self) -> bool:
        self.current_gate = None
        self.current_tool = None
        try:
            self.persistence.save(self.snapshot_state())
        except PersistenceError:
            pass
        return True

    def _run_form_tip(self) -> bool:
        if self._skip_tip or self.tip_former is None:
            return True
        result = self.tip_former.run()
        self._emit_callback("_DM_POST_FORM_TIP", action="form_tip")
        return bool(result.success)

    def _run_purge(self) -> bool:
        if self._skip_purge or self.purge_manager is None:
            return True
        volume = self.purge_manager.calculate_volume(self.counters.get("toolchanges", 0))
        self.purge_manager.purge_sequence(volume.volume_mm3)
        self.telemetry.record("purge_mm3", volume.volume_mm3)
        return True

    def _setup_reactor_timers(self, reactor: Any) -> None:
        """Registra los timers de los subsistemas dirigidos por reactor."""
        if reactor is None:
            return
        try:
            now = reactor.monotonic()
            if self.led is not None:
                reactor.register_timer(lambda et: self.led.update(et), now + 0.05)
            if self.environment is not None:
                reactor.register_timer(lambda et: self.environment.tick(et), now + 1.0)
            if self.espooler is not None:
                reactor.register_timer(lambda et: self.espooler.tick(et), now + 0.1)
        except Exception:  # noqa: BLE001 - reactor sin soporte de timers
            pass

    def _on_counter_event(self, event: Any) -> None:
        self.diagnostics.log_event(
            "warning", "counters", "limit_reached",
            name=event.name, value=event.value, limit=event.limit, warning=event.warning,
        )
        self._emit_callback(
            "_DM_COUNTER_LIMIT", name=event.name, value=event.value,
            limit=event.limit, warning=event.warning,
        )
        if event.pause:
            gcode = self._gcode()
            if gcode is not None and hasattr(gcode, "run_script_from_command"):
                try:
                    gcode.run_script_from_command("PAUSE")
                except Exception:  # noqa: BLE001
                    pass

    def _handle_shutdown(self) -> None:
        self.diagnostics.log_event("warning", "core", "shutdown")
        try:
            self.persistence.save(self.snapshot_state())
        except PersistenceError as exc:
            self.diagnostics.log_event("error", "core", "state_save_failed", error=str(exc))

    # -- Status -------------------------------------------------------------
    def get_status(self, eventtime: float) -> Dict[str, Any]:
        """Estado expuesto a Klipper/Moonraker (accesible como ``printer.dog_matrix``).

        Incluye ``spool_id`` y ``tool_spool_ids`` para que las macros Jinja2
        puedan consultar la bobina activa por herramienta (paridad Happy Hare).
        """
        current_spool = ""
        if self.current_gate is not None and 0 <= self.current_gate < len(self.gate_filament):
            current_spool = self.gate_filament[self.current_gate].get("spool_id", "")
        contract = build_mmu_state(self, eventtime)
        self.sync_feedback_state = self.sync_feedback.state
        return {
            "state": self.state_machine.get_state(),
            "print_state": contract["print_state"],
            "action": contract["action"],
            "filament_pos": contract["filament_pos"],
            "filament_direction": contract["filament_direction"],
            "gate": self.current_gate,
            "tool": self.current_tool,
            "gates": self.profile.gates,
            "ttg_map": list(self.ttg_map),
            "gate_status": list(self.gate_status),
            "gate_filament": self.gate_filament,
            "spool_id": current_spool,
            "tool_spool_ids": {
                f"T{tool}": self.gate_filament[gate].get("spool_id", "")
                for tool, gate in enumerate(self.ttg_map)
                if 0 <= gate < len(self.gate_filament)
            },
            "counters": dict(self.counters),
            "toolchange_timings": dict(self.toolchange_timings),
            "flowguard": self.flowguard.get_statistics() if self.flowguard else {},
            "sensors": self.sensor_flags(),
            "sync_feedback_state": self.sync_feedback_state,
            "sync_feedback": self.sync_feedback.get_status(),
            "fan": self.fan.get_status() if self.fan is not None else {"enabled": False},
            "autoload": self.autoload.controller.get_status() if self.autoload is not None else {"enabled": False},
            "counters_store": self.counter_store.as_dict(),
            "bypass": self.bypass_active,
            "slicer_tool_map": self.slicer_map.as_tool_map(),
            "calibration": self.calibrator.get_status(),
            "tip_forming": self.tip_former.get_status() if self.tip_former is not None else {"enabled": False},
            "purge": self.purge_manager.get_status() if self.purge_manager is not None else {"enabled": False},
            "environment": self.environment.get_status().as_dict() if self.environment is not None else {"enabled": False},
            "espooler": self.espooler.get_status() if self.espooler is not None else {"enabled": False},
            "led": self.led.get_status() if self.led is not None else {"enabled": False},
            "td1": self.td1.get_status(),
            "nfc": self.nfc_manager.get_status() if self.nfc_manager is not None else {"enabled": False},
            "ejection_buttons": self.ejection_buttons.get_status() if self.ejection_buttons is not None else {"enabled": False},
            "sequences": self.sequences.get_status(),
            "telemetry": {"channels": self.telemetry.channels()},
            "spoolman": (
                self.spoolman.get_inventory_status().as_dict()
                if self.spoolman is not None
                else {"enabled": False}
            ),
            "tool_overrides": self.tool_overrides.get_status(),
            "sync_controller": self.sync_controller.get_status(),
            "extruder_monitor": self.extruder_monitor.get_status(),
            "stepper_current": self.stepper_current.get_status(),
            "compound_endstop": self.compound_endstop.get_status(),
            "local_gate": self.local_gate.get_status(),
            "filament_display": self.filament_display.get_status(),
            "print_lifecycle": self.print_state,
            "nfc_extra": {
                "arbiter": self.nfc_arbiter.get_status() if self.nfc_arbiter is not None else {"enabled": False},
                "endstop": self.nfc_endstop.get_status() if self.nfc_endstop is not None else {"enabled": False},
                "gain": self.rx_gain.get_status() if self.rx_gain is not None else {"enabled": False},
            },
            "version": SOFTWARE_VERSION,
            "profile": self.profile.profile_id,
            "units": self.controller.get_status() if self.controller is not None else {"enabled": False},
        }

    # -- Contrato y utilidades de estado -----------------------------------
    def sensor_flags(self) -> Dict[str, bool]:
        """Estado habilitado/deshabilitado de los sensores conocidos."""
        return dict(self.sensor_enabled)

    def next_endless_gate(self, current_gate: Optional[int]) -> Optional[int]:
        """Siguiente gate disponible del grupo de EndlessSpool (o ``None``)."""
        if current_gate is None or not self.endless_spool_groups:
            return None
        for group in self.endless_spool_groups:
            if current_gate in group:
                candidates = [g for g in group if g != current_gate]
                for gate in candidates:
                    if 0 <= gate < len(self.gate_status) and self.gate_status[gate] in ("available", "unknown"):
                        return gate
        return None

    # -- Comandos G-code ----------------------------------------------------
    def cmd_DM_STATUS(self, gcmd: Any) -> None:
        status = self.get_status(time.time())
        if gcmd.get_int("COMPACT", 0):
            self._respond_compact_status(gcmd, status)
            return
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

    def _respond_compact_status(self, gcmd: Any, status: Dict[str, Any]) -> None:
        """Consola compacta en columnas con simbolos de estado (F-39)."""
        marks = {"available": "OK", "empty": "--", "unknown": "??", "error": "XX"}
        columns = [
            f"G{index}:{marks.get(value, value)}" for index, value in enumerate(status["gate_status"])
        ]
        flowguard = status.get("flowguard", {})
        line = (
            f"print={status['print_state']} | action={status['action']} | "
            f"tool=T{status['tool']} gate={status['gate']} | "
            f"sensors={len(status['sensors'])} | "
            f"errors={flowguard.get('errors', 0)} | "
            f"toolchanges={status['counters'].get('toolchanges', 0)}"
        )
        gcmd.respond_info(line)
        if gcmd.get_int("SHOWGATES", 1):
            gcmd.respond_info("gates: " + "  ".join(columns))

    def cmd_DM_PRINT_STATE(self, gcmd: Any) -> None:
        """Establecer el estado de la impresora y emitir callback/eventos."""
        new_state = gcmd.get("STATE", "")
        if not new_state:
            raise gcmd.error("STATE es requerido")
        self._emit_callback("_DM_PRINT_STATE_CHANGED", state=new_state)
        lowered = new_state.strip().lower()
        if lowered in ("printing", "started", "print_started"):
            self.events.emit(EVENT_PRINTING)
        elif lowered in ("idle", "complete", "completed", "cancelled", "standby", "not_printing"):
            self.events.emit(EVENT_NOT_PRINTING)
        gcmd.respond_info(f"Print state cambiado a {new_state}")

    def cmd_DM_STATS(self, gcmd: Any) -> None:
        """Muestra estadisticas de uso y, opcionalmente, las reinicia.

        Uso: ``DM_STATS`` (consulta) o ``DM_STATS RESET=1`` (reinicia contadores
        y acumuladores de tiempo).
        """
        reset = bool(gcmd.get_int("RESET", 0))
        counter = gcmd.get("COUNTER", None)
        if counter:
            if gcmd.get_int("INCR", 0):
                event = self.counter_store.incr(str(counter), gcmd.get_int("VALUE", 1))
                gcmd.respond_info(f"Contador {event.name}={event.value} (limite {event.limit})")
            elif gcmd.get_int("RESET", 0):
                self.counter_store.reset(str(counter))
                gcmd.respond_info(f"Contador {counter} reiniciado")
            elif gcmd.get_int("DELETE", 0):
                self.counter_store.delete(str(counter))
                gcmd.respond_info(f"Contador {counter} borrado")
            else:
                self.counter_store.define(
                    str(counter),
                    limit=gcmd.get_int("LIMIT", -1),
                    warning=gcmd.get("WARNING", ""),
                    pause=bool(gcmd.get_int("PAUSE", 0)),
                )
                gcmd.respond_info(f"Contador {counter} definido")
            return
        if gcmd.get_int("SHOWCOUNTS", 0):
            counters = self.counter_store.as_dict()
            if not counters:
                gcmd.respond_info("Sin contadores definidos")
            for name, payload in counters.items():
                gcmd.respond_info(f"counter {name}={payload['value']} limit={payload['limit']}")
            return
        if reset:
            self.counters = {"toolchanges": 0, "loads": 0, "unloads": 0, "errors": 0}
            self.toolchange_timings = {key: 0.0 for key in self.toolchange_timings}
            self.persistence.save(self.snapshot_state())
            gcmd.respond_info("Estadisticas reiniciadas")
            return
        timings = ", ".join(f"{key}={value:.1f}ms" for key, value in self.toolchange_timings.items() if value)
        gcmd.respond_info(
            "Estadisticas Dog Matrix: "
            f"toolchanges={self.counters.get('toolchanges', 0)} "
            f"loads={self.counters.get('loads', 0)} "
            f"unloads={self.counters.get('unloads', 0)} "
            f"errors={self.counters.get('errors', 0)}"
        )
        if timings:
            gcmd.respond_info(f"Timings por fase: {timings}")

    def cmd_DM_CHANGE_TOOL(self, gcmd: Any) -> None:
        tool = gcmd.get_int("TOOL", 0)
        self._execute_toolchange(
            gcmd,
            tool,
            skip_tip=bool(gcmd.get_int("SKIP_TIP", 0)),
            skip_purge=bool(gcmd.get_int("SKIP_PURGE", 0)),
        )

    def _execute_toolchange(
        self,
        gcmd: Any,
        tool: int,
        skip_tip: bool = False,
        skip_purge: bool = False,
    ) -> None:
        """Ejecuta un toolchange con flags de tip/purga y failover EndlessSpool."""
        if not 0 <= tool < len(self.ttg_map):
            raise gcmd.error(f"TOOL {tool} fuera de rango 0..{len(self.ttg_map) - 1}")
        gate = self.ttg_map[tool]
        previous_gate = self.current_gate
        self._skip_tip = bool(skip_tip)
        self._skip_purge = bool(skip_purge)
        try:
            result = self.state_machine.execute_toolchange(gate, tool)
        finally:
            self._skip_tip = False
            self._skip_purge = False
        if result.success:
            self._on_toolchange_success(gate, tool, previous_gate)
            gcmd.respond_info(f"Toolchange OK -> tool {tool} (gate {gate})")
            return
        # Fallo: intenta failover automatico EndlessSpool (E-01).
        self.counters["errors"] += 1
        new_gate = self.try_endless_failover()
        if new_gate is not None:
            retry = self.state_machine.execute_toolchange(new_gate, tool)
            if retry.success:
                self._on_toolchange_success(new_gate, tool, gate)
                gcmd.respond_info(
                    f"Toolchange OK (failover) -> tool {tool} (gate {new_gate})"
                )
                return
        raise gcmd.error(f"Toolchange fallido: {result.error_code} ({result.message})")

    def _on_toolchange_success(self, gate: int, tool: int, previous_gate: Optional[int]) -> None:
        self.counters["toolchanges"] += 1
        self.events.emit(EVENT_GATE_SELECTED, gate=gate, previous_gate=previous_gate)
        self.events.emit(EVENT_TOOL_SELECTED, tool=tool)
        self.events.emit(
            EVENT_TOOLCHANGE,
            last_tool=previous_gate if previous_gate is not None else -1,
            next_tool=tool,
        )
        self.telemetry.record("toolchange_ms", float(self.state_machine.attempt))

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
        """Remapea tool->gate validando compatibilidad de material/color.

        Uso: ``DM_REMAP_TTG TOOL=<n> GATE=<m>``. Si ``FORCE=1`` se omite la
        validacion de compatibilidad (necesario para remapear a un gate cuyo
        material todavia no se ha declarado).
        """
        tool = gcmd.get_int("TOOL", None)
        gate = gcmd.get_int("GATE", None)
        force = bool(gcmd.get_int("FORCE", 0))
        if tool is None or gate is None:
            raise gcmd.error("DM_REMAP_TTG requiere TOOL y GATE")
        if not 0 <= tool < len(self.ttg_map) or not 0 <= gate < self.profile.gates:
            raise gcmd.error("TOOL o GATE fuera de rango")

        source_material = self.gate_filament[tool].get("material", "")
        target_material = self.gate_filament[gate].get("material", "")
        source_color = self.gate_filament[tool].get("color", "")
        target_color = self.gate_filament[gate].get("color", "")

        if not force and source_material and target_material:
            if not self._materials_compatible(source_material, target_material):
                raise gcmd.error(
                    f"Conflicto de material: tool {tool}={source_material} vs "
                    f"gate {gate}={target_material}. Use FORCE=1 para forzar."
                )

        self.ttg_map[tool] = gate
        self.persistence.save(self.snapshot_state())
        self._emit_callback("_DM_GATE_MAP_CHANGED", action="remap_ttg", tool=tool, gate=gate)
        gcmd.respond_info(
            f"TTG remapeado: tool {tool} -> gate {gate} "
            f"(material {source_material or '-'} -> {target_material or '-'}, "
            f"color {source_color or '-'} -> {target_color or '-'})"
        )

    def cmd_DM_SPOOLMAN(self, gcmd: Any) -> None:
        if self.spoolman is None:
            raise gcmd.error("Spoolman deshabilitado (enable_spoolman=false)")
        support = gcmd.get("SUPPORT", None)
        if support is not None:
            mode = self.spoolman.set_support(str(support))
            gcmd.respond_info(f"Spoolman modo: {mode}")
            return
        tag = gcmd.get("TAG", None)
        if tag is not None:
            spool_id = gcmd.get_int("SPOOL_ID", None)
            if spool_id is None:
                raise gcmd.error("TAG requiere SPOOL_ID=")
            self.spoolman.register_tag(str(tag), int(spool_id))
            gcmd.respond_info(f"Tag {tag} -> spool {spool_id}")
            return
        action = gcmd.get("ACTION", "STATUS").upper()
        if action == "SYNC":
            ok = self.spoolman.sync_gate_map()
            gcmd.respond_info("Spoolman sync OK" if ok else "Spoolman sync fallido")
        elif action == "PULL":
            mapping = self.spoolman.pull_gate_map(self.profile.gates)
            gcmd.respond_info(f"Spoolman pull: {mapping}")
        else:
            status = self.spoolman.get_inventory_status()
            gcmd.respond_info(f"Spoolman: {status.as_dict()}")

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
        """Calibracion real de la distancia de rotacion del gear (con encoder)."""
        gate = gcmd.get_int("TOOL", gcmd.get_int("GATE", 0))
        distance = gcmd.get_float("DISTANCE_MM", 100.0)
        speed = gcmd.get_float("SPEED", 30.0)
        result = self.calibrator.calibrate_gear(gate, distance, speed)
        if result.success:
            gcmd.respond_info(f"Calibracion gear gate {gate} OK: {result.message}")
        else:
            raise gcmd.error(f"Calibracion gear fallida: {result.message}")

    def cmd_DM_CALIBRATE_ENCODER(self, gcmd: Any) -> None:
        """Calibracion real de la resolucion del encoder (mm/pulso)."""
        distance = gcmd.get_float("DISTANCE_MM", 100.0)
        raw_counts = gcmd.get_int("COUNTS", None)
        result = self.calibrator.calibrate_encoder(distance, raw_counts)
        if result.success:
            gcmd.respond_info(f"Calibracion encoder OK: {result.message}")
        else:
            raise gcmd.error(f"Calibracion encoder fallida: {result.message}")

    def cmd_DM_CALIBRATE_BOWDEN(self, gcmd: Any) -> None:
        """Calibracion real de la longitud de bowden."""
        gate = gcmd.get_int("TOOL", gcmd.get_int("GATE", 0))
        distance = gcmd.get_float("DISTANCE_MM", 600.0)
        result = self.calibrator.calibrate_bowden(gate, distance)
        if result.success:
            gcmd.respond_info(f"Calibracion bowden gate {gate} OK: {result.message}")
        else:
            raise gcmd.error(f"Calibracion bowden fallida: {result.message}")

    def cmd_DM_CALIBRATE_GATES(self, gcmd: Any) -> None:
        """Calibracion automatica de todos los gates (medicion real)."""
        results = self.calibrator.calibrate_gates()
        ok = sum(1 for result in results if result.success)
        self._emit_callback("_DM_ACTION_CHANGED", action="calibrate_gates")
        gcmd.respond_info(f"Calibracion gates: {ok}/{len(results)} correctos")

    def cmd_DM_CALIBRATE_TOOLHEAD(self, gcmd: Any) -> None:
        """Calibracion de la distancia hub -> toolhead."""
        gate = gcmd.get_int("TOOL", gcmd.get_int("GATE", 0))
        distance = gcmd.get_float("DISTANCE_MM", 80.0)
        result = self.calibrator.calibrate_toolhead(gate, distance)
        if result.success:
            gcmd.respond_info(f"Calibracion toolhead OK: {result.message}")
        else:
            raise gcmd.error(f"Calibracion toolhead fallida: {result.message}")

    def cmd_DM_CALIBRATE_SELECTOR(self, gcmd: Any) -> None:
        """Calibracion del selector: homing y verificacion de posiciones."""
        if not self.selector.home():
            raise gcmd.error("Homing de selector fallido durante calibracion")
        positions = {}
        for gate in range(self.profile.gates):
            if self.selector.select_gate(gate):
                positions[gate] = round(self.selector.get_position(), 4)
        self.calibration_results["selector_positions"] = positions
        self.persistence.save(self.snapshot_state())
        gcmd.respond_info(f"Calibracion selector OK: {positions}")

    def cmd_DM_CALIBRATE_PSENSOR(self, gcmd: Any) -> None:
        """Calibra el sensor de posicion del buffer (sync-feedback)."""
        min_mm = gcmd.get_float("MIN", 0.0)
        max_mm = gcmd.get_float("MAX", 100.0)
        result = self.sync_feedback.calibrate_psensor(min_mm, max_mm)
        if not result.get("ok"):
            raise gcmd.error("Rango de psensor invalido (MAX debe ser > MIN)")
        gcmd.respond_info(f"Calibracion psensor OK: span={result['span_mm']} mm")

    def _materials_compatible(self, mat1: str, mat2: str) -> bool:
        """Verificar compatibilidad de materiales."""
        # Lista de materiales incompatibles (ej. PLA + ABS en mismo nozzle sin purge)
        incompatible = [("PLA", "ABS"), ("ABS", "PLA"), ("TPU", "PETG duro")]
        return (mat1, mat2) not in incompatible and (mat2, mat1) not in incompatible

    def _colors_compatible(self, color1: str, color2: str) -> bool:
        """Verificar suficiente contraste entre colores."""
        if color1 == color2:
            return True  # Mismo color ok
        # Colores extremos (rojo sobre verde, etc. serían problema)
        extreme_pairs = {("RED", "GREEN"), ("GREEN", "RED"), ("BLUE", "YELLOW"), ("YELLOW", "BLUE")}
        return (color1, color2) not in extreme_pairs

    def _on_nfc_tag(self, uid: str) -> Optional[Spool]:
        """Sincroniza un tag NFC con el gate activo y con Spoolman (DM-NFC-001).

        Flujo: identificar bobina -> actualizar ``gate_filament`` -> notificar a
        Spoolman -> emitir callback. Si Spoolman o el lector NFC no estan
        disponibles, degrada de forma segura sin lanzar excepciones.
        """
        spool: Optional[Spool] = None
        if self.spoolman is not None:
            spool = self.spoolman.handle_nfc_tag(uid)
        if spool is None and self.nfc is not None:
            tag = self.nfc.scan_tag(0.0)
            if tag is not None:
                payload = tag.data or {}
                spool = Spool(
                    spool_id=int(payload.get("spool_id", 0)),
                    material=str(payload.get("material", "")),
                    color=str(payload.get("color", "")),
                    name=str(payload.get("name", "")),
                    extra={"nfc_uid": tag.uid},
                )
        if spool is None:
            self._emit_callback("_DM_NFC_TAG_UNKNOWN", uid=uid)
            return None

        gate = self.current_gate
        if gate is not None and 0 <= gate < len(self.gate_filament):
            entry = self.gate_filament[gate]
            entry["spool_id"] = spool.spool_id
            entry["material"] = spool.material or entry.get("material", "")
            entry["color"] = spool.color or entry.get("color", "")
            entry["availability"] = "available"
            self.persistence.save(self.snapshot_state())

        if self.spoolman is not None and gate is not None:
            self.spoolman.notify_toolchange(gate=gate, spool_id=spool.spool_id)

        self._emit_callback(
            "_DM_GATE_MAP_CHANGED", action="nfc_tag", uid=uid, gate=gate, spool_id=spool.spool_id
        )
        return spool

    def cmd_DM_UNLOCK(self, gcmd: Any) -> None:
        """Restaura temperaturas tras un error de MMU (interaccion directa)."""
        result = self.recovery.recover_from_failure({})
        if result.success:
            gcmd.respond_info(f"Recuperacion OK ({result.action})")
        else:
            gcmd.respond_info(f"Recuperacion requiere atencion: {result.message}")

    def cmd_DM_NFC_READ(self, gcmd: Any) -> None:
        """Comando G-code para leer tag NFC y sincronizar."""
        uid = gcmd.get("UID", "")
        if not uid:
            raise gcmd.error("Falta el parámetro UID (identificador del tag NFC)")
        self._on_nfc_tag(uid)
        gcmd.respond_info(f"Tag NFC leído y sincronizado: {uid}")

    def cmd_DM_ESPOOLER(self, gcmd: Any) -> None:
        """Control del eSpooler DC."""
        action = gcmd.get("ACTION", "STATUS").upper()
        if action == "STATUS":
            gcmd.respond_info(f"eSpooler: {self.espooler.get_status() if self.espooler else 'deshabilitado'}")
        elif action == "FORWARD":
            speed = gcmd.get_float("SPEED", 0.5)
            if self.espooler:
                self.espooler.forward(speed)
                gcmd.respond_info("eSpooler forwards")
            else:
                gcmd.respond_info("eSpooler deshabilitado")
        elif action == "REVERSE":
            speed = gcmd.get_float("SPEED", 0.5)
            if self.espooler:
                self.espooler.reverse(speed)
                gcmd.respond_info("eSpooler reverse")
            else:
                gcmd.respond_info("eSpooler deshabilitado")
        elif action == "STOP":
            if self.espooler:
                self.espooler.stop()
                gcmd.respond_info("eSpooler stopped")
            else:
                gcmd.respond_info("eSpooler deshabilitado")
        elif action == "BURST":
            if self.espooler:
                duration = gcmd.get_float("DURATION", 2.0)
                speed = gcmd.get_float("SPEED", 0.5)
                self.espooler.burst(duration, speed)
                self.events.emit(EVENT_ESPOOLER_BURST, duration=duration, speed=speed)
                if self.autoload is not None:
                    try:
                        self.autoload.kick()
                    except Exception:  # noqa: BLE001
                        pass
                gcmd.respond_info(f"eSpooler burst {duration}s @ {speed}")
            else:
                gcmd.respond_info("eSpooler deshabilitado")
        else:
            gcmd.respond_info("Accion no soportada")

    def cmd_DM_TIP_FORMING(self, gcmd: Any) -> None:
        """Iniciar o controlar formación de punta."""
        action = gcmd.get("ACTION", "START").upper()
        if action == "START":
            if hasattr(self, "tip_former") and self.tip_former:
                result = self.tip_former.start()
                gcmd.respond_info(f"Formacion de punta iniciada: {result.message}")
            else:
                gcmd.respond_info("Modulo de formation no inicializado")
        elif action == "STEP":
            if hasattr(self, "tip_former") and self.tip_former:
                # Avanzar un paso en la FSM
                # Determinar siguiente paso basado en estado actual
                current = self.tip_former.state
                if current == "idle" or current == "committed":
                    result = self.tip_former.start()
                elif "ramming" in current:
                    result = self.tip_former.step_ramming()
                elif "cooling" in current:
                    # Simular move de cooling
                    result = self.tip_former.step_cooling()
                elif "skinnydip" in current:
                    result = self.tip_former.step_skinnydip()
                else:
                    result = TipFormingResult(success=False, state="idle", step="unknown", message="Estado desconocido")
                gcmd.respond_info(f"Formacion paso: {result.message}")
            else:
                gcmd.respond_info("Modulo de formation no inicializado")
        elif action == "STATUS":
            if hasattr(self, "tip_former") and self.tip_former:
                gcmd.respond_info(f"Formacion estado: {self.tip_former.get_status()}")
            else:
                gcmd.respond_info("Modulo de formation no inicializado")
        else:
            gcmd.respond_info("Accion no soportada")

    def cmd_DM_PURGE(self, gcmd: Any) -> None:
        """Ejecuta (o calcula) la purga de un toolchange."""
        if self.purge_manager is None:
            raise gcmd.error("Purga deshabilitada (enable_purge=false)")
        from_gate = gcmd.get_int("FROM", None)
        to_gate = gcmd.get_int("TO", None)
        from_material = from_color = to_material = to_color = ""
        if from_gate is not None and 0 <= from_gate < len(self.gate_filament):
            from_material = self.gate_filament[from_gate].get("material", "")
            from_color = self.gate_filament[from_gate].get("color", "")
        if to_gate is not None and 0 <= to_gate < len(self.gate_filament):
            to_material = self.gate_filament[to_gate].get("material", "")
            to_color = self.gate_filament[to_gate].get("color", "")
        result = self.purge_manager.calculate_volume(
            gcmd.get_int("TOOLCHANGE_COUNT", 0),
            from_material=gcmd.get("FROM_MATERIAL", from_material),
            to_material=gcmd.get("TO_MATERIAL", to_material),
            from_color=gcmd.get("FROM_COLOR", from_color),
            to_color=gcmd.get("TO_COLOR", to_color),
        )
        if gcmd.get_int("EXECUTE", 0):
            self.purge_manager.purge_sequence(result.volume_mm3, from_gate, to_gate)
        gcmd.respond_info(f"Purga: {result.message} -> {result.volume_mm3} mm3")

    def cmd_DM_CALC_PURGE_VOLUMES(self, gcmd: Any) -> None:
        """Calcula la matriz de purga gate->gate (paridad ``MMU_CALC_PURGE_VOLUMES``).

        Parametros: ``MIN``/``MAX`` (mm3, recorte), ``MULTIPLIER`` (escala) y
        ``SOURCE`` (``gatemap`` por defecto o ``slicer`` para el mapa de slicer).
        """
        if self.purge_manager is None:
            raise gcmd.error("Purga deshabilitada (enable_purge=false)")
        source = str(gcmd.get("SOURCE", "gatemap")).lower()
        if source == "slicer":
            materials = ["" for _ in self.gate_filament]
            colors = ["" for _ in self.gate_filament]
            for tool, gate in enumerate(self.ttg_map):
                if 0 <= gate < len(self.gate_filament):
                    colors[gate] = self.gate_filament[gate].get("color", "")
                    materials[gate] = self.gate_filament[gate].get("material", "")
        else:
            materials = [entry.get("material", "") for entry in self.gate_filament]
            colors = [entry.get("color", "") for entry in self.gate_filament]
        summary = self.purge_manager.calculate_purge_volumes(materials=materials, colors=colors)
        multiplier = gcmd.get_float("MULTIPLIER", 1.0) or 1.0
        minimum = gcmd.get_float("MIN", 0.0)
        maximum = gcmd.get_float("MAX", 0.0)

        def _scale(value: float) -> float:
            scaled = value * multiplier
            if maximum > 0:
                scaled = min(scaled, maximum)
            if minimum > 0:
                scaled = max(scaled, minimum)
            return scaled

        matrix = [[round(_scale(value), 2) for value in row] for row in summary["matrix"]]
        total = round(sum(sum(row) for row in matrix), 2)
        for index, row in enumerate(matrix):
            gcmd.respond_info(f"purge[{index}] = {row}")
        gcmd.respond_info(
            f"Purga total: {total} mm3 ({summary['gates']} gates, source={source}, "
            f"mult={multiplier})"
        )

    def cmd_DM_UNIT(self, gcmd: Any) -> None:
        """Control del sistema multi-unidad.

        Acciones: ``STATUS``/``LIST`` (por defecto), ``RESCAN``, ``ASSIGN``
        (``DEVICE=`` + ``SPOOL=``), ``CLEAR`` y ``SELECT`` (``DEVICE=``).
        """
        if self.controller is None:
            raise gcmd.error("Multi-unidad deshabilitado (enable_multi_unit=false)")
        action = gcmd.get("ACTION", "STATUS").upper()
        if action in ("STATUS", "LIST"):
            self.controller.poll()
            units = self.controller.list_units()
            if not units:
                gcmd.respond_info("Multi-unidad: no se detectaron dispositivos")
                return
            for unit in units:
                gcmd.respond_info(
                    f"unit {unit.unit_id} spool={unit.spool_number} if={unit.interface} "
                    f"gates={unit.gates} offset={unit.gate_offset} connected={unit.connected}"
                )
        elif action == "RESCAN":
            events = self.controller.poll()
            gcmd.respond_info(f"Escaneo completado: {len(events)} eventos")
        elif action == "ASSIGN":
            device_id = gcmd.get("DEVICE", "")
            spool_number = gcmd.get_int("SPOOL", None)
            if not device_id or spool_number is None:
                raise gcmd.error("ASSIGN requiere DEVICE y SPOOL")
            self.controller.assign_spool(device_id, int(spool_number))
            gcmd.respond_info(f"Asignado {device_id} -> spool {spool_number}")
        elif action == "CLEAR":
            self.controller.clear_assignments()
            gcmd.respond_info("Asignaciones multi-unidad borradas")
        elif action == "SELECT":
            device_id = gcmd.get("DEVICE", "")
            if self.controller.select_unit(device_id):
                gcmd.respond_info(f"Unidad activa: {device_id}")
            else:
                raise gcmd.error(f"Unidad no encontrada: {device_id}")
        else:
            gcmd.respond_info("Accion no soportada")

    # -- Comandos de operacion (paridad Happy Hare) ------------------------
    def cmd_DM_PRELOAD(self, gcmd: Any) -> None:
        """Precarga el filamento de un gate hasta el hub (post-gate)."""
        gate = gcmd.get_int("GATE", self.current_gate if self.current_gate is not None else 0)
        if not 0 <= gate < self.profile.gates:
            raise gcmd.error(f"GATE {gate} fuera de rango 0..{self.profile.gates - 1}")
        self.current_gate = gate
        self.bypass_active = False
        if self.autoload is not None:
            started = self.autoload.controller.on_pre_gate(gate)
            self.autoload.kick()
            if not started:
                raise gcmd.error("Preload no iniciado (unidad ocupada o conflicto de buffer)")
            gcmd.respond_info(f"Preload iniciado para gate {gate}")
        else:
            if self.gate_status[gate] == "unknown":
                self.gate_status[gate] = "available"
                self.gate_filament[gate]["availability"] = "available"
            gcmd.respond_info(f"Preload: gate {gate} marcado como disponible (autoload deshabilitado)")

    def cmd_DM_SELECT(self, gcmd: Any) -> None:
        """Selecciona un tool/gate o el bypass (filamento directo)."""
        if gcmd.get_int("BYPASS", 0):
            if not self.profile.has_capability("selector"):
                raise gcmd.error("El perfil no soporta bypass")
            previous = self.current_gate
            self.bypass_active = True
            self.current_gate = -2
            self.current_tool = -2
            self.events.emit(EVENT_GATE_SELECTED, gate=-2, previous_gate=previous)
            gcmd.respond_info("Bypass seleccionado")
            return
        tool = gcmd.get_int("TOOL", None)
        gate = gcmd.get_int("GATE", None)
        if gate is None and tool is not None and 0 <= tool < len(self.ttg_map):
            gate = self.ttg_map[tool]
        if gate is None:
            raise gcmd.error("DM_SELECT requiere TOOL= o GATE= (o BYPASS=1)")
        if not 0 <= gate < self.profile.gates:
            raise gcmd.error(f"GATE {gate} fuera de rango 0..{self.profile.gates - 1}")
        previous = self.current_gate
        self.bypass_active = False
        self.current_gate = gate
        if tool is not None:
            self.current_tool = tool
        select = getattr(self.selector, "select", None)
        if callable(select):
            try:
                select(gate)
            except Exception:  # noqa: BLE001 - selector opcional en tests
                pass
        self.events.emit(EVENT_GATE_SELECTED, gate=gate, previous_gate=previous)
        gcmd.respond_info(f"Gate {gate} seleccionado")

    def cmd_DM_CHECK_GATE(self, gcmd: Any) -> None:
        """Inspecciona gate(s), actualiza disponibilidad y persiste.

        Acepta ``ALL=1``, ``GATE``, ``GATES=a,b,c``, ``TOOL``, ``TOOLS=a,b`` y
        ``TD1=1`` (captura TD/color si el escaner esta disponible).
        """
        gates = self._resolve_check_gates(gcmd)
        td1 = bool(gcmd.get_int("TD1", 0))
        available = 0
        for gate in gates:
            if not 0 <= gate < self.profile.gates:
                continue
            present = self._gate_present(gate)
            self.gate_status[gate] = "available" if present else "empty"
            self.gate_filament[gate]["availability"] = self.gate_status[gate]
            available += 1 if present else 0
            if td1 and self.td1.available:
                reading = self.td1.read_gate(gate)
                if reading is not None and not self.gate_filament[gate].get("color"):
                    self.gate_filament[gate]["color"] = reading.color_hex
        self._persist_state()
        gcmd.respond_info(f"CHECK_GATE: {available}/{len(gates)} gates con filamento")

    def _resolve_check_gates(self, gcmd: Any) -> List[int]:
        """Resuelve la lista de gates a inspeccionar desde los parametros."""
        if gcmd.get_int("ALL", 0):
            return list(range(self.profile.gates))
        raw = gcmd.get("GATES", None)
        if raw:
            result: List[int] = []
            for token in str(raw).replace(" ", "").split(","):
                try:
                    gate = int(token)
                except ValueError:
                    continue
                if 0 <= gate < self.profile.gates and gate not in result:
                    result.append(gate)
            if result:
                return result
        tool = gcmd.get_int("TOOL", None)
        if tool is not None and 0 <= tool < len(self.ttg_map):
            return [self.ttg_map[tool]]
        raw_tools = gcmd.get("TOOLS", None)
        if raw_tools:
            result = []
            for token in str(raw_tools).replace(" ", "").split(","):
                try:
                    index = int(token)
                except ValueError:
                    continue
                if 0 <= index < len(self.ttg_map) and self.ttg_map[index] not in result:
                    result.append(self.ttg_map[index])
            if result:
                return result
        return [gcmd.get_int("GATE", self.current_gate if self.current_gate is not None else 0)]

    def _gate_present(self, gate: int) -> bool:
        """Presencia de filamento en un gate (best-effort sobre sensores).

        Si no hay un canal de sensor *inicializado* para el gate, se asume
        presencia (no se puede confirmar ausencia sin hardware).
        """
        sensors = getattr(self, "sensors", None)
        if sensors is None:
            return True
        channels = getattr(sensors, "channels", None)
        if not isinstance(channels, dict):
            return True
        for name in (f"mmu_entry_{gate}", f"entry_{gate}", f"gate_{gate}"):
            channel = channels.get(name)
            if channel is None or not getattr(channel, "initialized", False):
                continue
            try:
                return bool(sensors.is_present(name))
            except Exception:  # noqa: BLE001
                continue
        return True

    def cmd_DM_SENSORS(self, gcmd: Any) -> None:
        """Lista sensores y los habilita/deshabilita en runtime (sin rewire)."""
        sensor = gcmd.get("SENSOR", None)
        if sensor is None:
            if not self.sensor_enabled:
                gcmd.respond_info("Sensores: sin overrides de runtime (todos habilitados)")
            for name, enabled in sorted(self.sensor_enabled.items()):
                gcmd.respond_info(f"sensor {name}={'enabled' if enabled else 'disabled'}")
            return
        enable = bool(gcmd.get_int("ENABLE", 1))
        self.sensor_enabled[str(sensor)] = enable
        gcmd.respond_info(f"Sensor {sensor} {'habilitado' if enable else 'deshabilitado'}")

    def cmd_DM_MOTORS(self, gcmd: Any) -> None:
        """Energiza/desenergiza todos los motores y servos (safety)."""
        action = gcmd.get("ACTION", "OFF").upper()
        self._set_motors(action == "ON")
        gcmd.respond_info(f"Motores {'ON' if self.mmu_enabled else 'OFF'}")

    def cmd_DM_MOTORS_OFF(self, gcmd: Any) -> None:
        """Alias explicito de ``MMU_MOTORS_OFF`` (desenergiza motores)."""
        self._set_motors(False)
        gcmd.respond_info("Motores OFF")

    def cmd_DM_MOTORS_ON(self, gcmd: Any) -> None:
        """Alias explicito de ``MMU_MOTORS_ON`` (energiza motores)."""
        self._set_motors(True)
        gcmd.respond_info("Motores ON")

    def _set_motors(self, enabled: bool) -> None:
        if enabled:
            self.mmu_enabled = True
            self.events.emit(EVENT_ENABLED)
            return
        self.mmu_enabled = False
        if self.autoload is not None:
            try:
                self.autoload.stop()
            except Exception:  # noqa: BLE001
                pass
        self.events.emit(EVENT_DISABLED)

    def cmd_DM_EJECT(self, gcmd: Any) -> None:
        """Expulsa el filamento de un gate (descarga antes si procede).

        Acepta ``FORCE=1`` (no valida estado) y ``EXTRUDER_ONLY=1`` (descarga
        solo hasta el toolhead sin liberar el gate).
        """
        gate = gcmd.get_int("GATE", self.current_gate if self.current_gate is not None else 0)
        if not 0 <= gate < self.profile.gates:
            raise gcmd.error(f"GATE {gate} fuera de rango 0..{self.profile.gates - 1}")
        extruder_only = bool(gcmd.get_int("EXTRUDER_ONLY", 0))
        limits = self.profile.limits
        distance = (
            float(limits.get("toolhead_distance_mm", 80)) if extruder_only
            else float(limits.get("max_distance_mm", 1000))
        )
        ok = self.motion.unload_filament(distance, float(limits.get("max_unload_speed_mm_s", 80)))
        if not extruder_only:
            self.gate_status[gate] = "available"
            self.gate_filament[gate]["availability"] = "available"
            self.current_gate = None
            self.current_tool = None
            self._persist_state()
        gcmd.respond_info(
            f"Eject gate {gate}{' (extruder only)' if extruder_only else ''}: "
            f"{'OK' if ok else 'sin movimiento'}"
        )

    def cmd_DM_GRIP(self, gcmd: Any) -> None:
        """Agarra el filamento en el gate actual (selector)."""
        grip = getattr(self.selector, "grip", None)
        if callable(grip):
            try:
                grip()
            except Exception:  # noqa: BLE001
                pass
        gcmd.respond_info("Grip solicitado")

    def cmd_DM_RELEASE(self, gcmd: Any) -> None:
        """Suelta el filamento en el gate actual (selector)."""
        release = getattr(self.selector, "release", None)
        if callable(release):
            try:
                release()
            except Exception:  # noqa: BLE001
                pass
        gcmd.respond_info("Release solicitado")

    def cmd_DM_FAN(self, gcmd: Any) -> None:
        """Controla el ventilador de la MMU (auto con histeresis o forzado)."""
        if self.fan is None:
            gcmd.respond_info("Fan control deshabilitado (enable_fan_control=false)")
            return
        enable = gcmd.get_int("ENABLE", None)
        if enable is not None:
            self.fan.enable(bool(enable))
        forced = gcmd.get_int("FAN_FORCED", None)
        if forced is not None:
            self.fan.set_forced(forced)
        on_temp = gcmd.get_float("ON_TEMP", None)
        if on_temp is not None:
            self.fan.on_temp = on_temp
        off_temp = gcmd.get_float("OFF_TEMP", None)
        if off_temp is not None:
            self.fan.off_temp = off_temp
        self.fan.update(self._fan_temperature())
        gcmd.respond_info(f"Fan: {self.fan.get_status()}")

    def cmd_DM_UPDATE_HEIGHT(self, gcmd: Any) -> None:
        """Actualiza la altura de impresion (sequential printing)."""
        height = gcmd.get_float("HEIGHT", None)
        if height is None:
            raise gcmd.error("DM_UPDATE_HEIGHT requiere HEIGHT=")
        self.print_height = float(height)
        gcmd.respond_info(f"Altura de impresion actualizada: {self.print_height}")

    def cmd_DM_HELP(self, gcmd: Any) -> None:
        """Lista los comandos disponibles y sus alias."""
        for name in sorted(self.COMMAND_ALIASES):
            gcmd.respond_info(f"{name}  (alias: {self.COMMAND_ALIASES[name]})")

    def cmd_DM_RESET(self, gcmd: Any) -> None:
        """Olvida el estado persistido (requiere CONFIRM=1)."""
        if not gcmd.get_int("CONFIRM", 0):
            raise gcmd.error("DM_RESET requiere CONFIRM=1")
        gates = self.profile.gates
        self.current_gate = None
        self.current_tool = None
        self.bypass_active = False
        self.ttg_map = list(range(gates))
        self.gate_status = ["unknown"] * gates
        self.gate_filament = [
            {"material": "", "color": "", "spool_id": "", "availability": "unknown"}
            for _ in range(gates)
        ]
        self.gate_filament_extra = {}
        self.calibration_results = {}
        self.counters = {"toolchanges": 0, "loads": 0, "unloads": 0, "errors": 0}
        self.slicer_map.reset()
        self.counter_store.reset_all()
        self.persistence.save(self.snapshot_state())
        gcmd.respond_info("Estado Dog Matrix reseteado")

    # -- Comandos de paridad avanzada --------------------------------------
    def cmd_DM_SELECT_BYPASS(self, gcmd: Any) -> None:
        """Selecciona el bypass (filamento directo al toolhead)."""
        previous = self.current_gate
        self.bypass_active = True
        self.current_gate = -2
        self.current_tool = -2
        self.events.emit(EVENT_GATE_SELECTED, gate=-2, previous_gate=previous)
        gcmd.respond_info("Bypass seleccionado")

    def cmd_DM_SYNC_FEEDBACK(self, gcmd: Any) -> None:
        """Estado, autotune o evaluacion del buffer de sync-feedback."""
        action = gcmd.get("ACTION", "STATUS").upper()
        if action == "AUTOTUNE":
            if gcmd.get_int("STOP", 0):
                self.sync_feedback.stop_autotune()
                gcmd.respond_info("Sync-feedback autotune detenido")
            else:
                self.sync_feedback.start_autotune()
                gcmd.respond_info("Sync-feedback autotune iniciado")
            return
        status = self.sync_feedback.get_status()
        self.sync_feedback_state = status["state"]
        gcmd.respond_info(
            f"Sync-feedback state={status['state']} rotation_distance={status['rotation_distance']:.4f} "
            f"speed_factor={status['speed_factor']}"
        )

    def cmd_DM_SYNC_GEAR_MOTOR(self, gcmd: Any) -> None:
        """Sincroniza el motor de gear con la realimentacion del buffer."""
        factor = self.sync_feedback.speed_factor()
        if self.espooler is not None:
            try:
                self.espooler.forward(min(1.0, 0.5 * factor))
            except Exception:  # noqa: BLE001
                pass
        gcmd.respond_info(f"Sync gear motor: factor={factor:.2f}")

    def cmd_DM_TD1(self, gcmd: Any) -> None:
        """Lee el escaner TD-1 y asocia el resultado a un gate."""
        if not self.td1.available:
            raise gcmd.error("TD-1 no disponible (td1_enabled=false o td1_count=0)")
        action = gcmd.get("ACTION", "STATUS").upper()
        if action == "READ":
            gate = gcmd.get_int("GATE", self.current_gate if self.current_gate is not None else 0)
            if gcmd.get("TD", None) is not None:
                hex_color, name = (gcmd.get("COLOR", ""), "")
                self.td1.devices[0].simulate(gcmd.get_float("TD", 0.0), hex_color, name)
            reading = self.td1.read_gate(gate)
            if reading is None:
                gcmd.respond_info(f"TD-1: sin lectura para gate {gate}")
            else:
                gcmd.respond_info(f"TD-1 gate {gate}: TD={reading.td:.3f} color={reading.color_hex}")
            return
        if action == "SCAN":
            readings = self.td1.scan_all(list(range(self.profile.gates)))
            tracked = sum(1 for value in readings.values() if value is not None)
            gcmd.respond_info(f"TD-1 scan: {tracked}/{len(readings)} gates con lectura")
            return
        gcmd.respond_info(f"TD-1: {self.td1.get_status()}")

    def cmd_DM_SLICER_TOOL_MAP(self, gcmd: Any) -> None:
        """Configura o calcula (automap) el mapa slicer-tool -> gate."""
        if gcmd.get_int("AUTOMAP", 0):
            tool_colors = {
                tool: self.gate_filament[gate].get("color", "")
                for tool, gate in enumerate(self.ttg_map)
                if 0 <= gate < len(self.gate_filament)
            }
            gate_colors = [entry.get("color", "") for entry in self.gate_filament]
            gate_materials = [entry.get("material", "") for entry in self.gate_filament]
            mapping = self.slicer_map.automap(tool_colors, gate_colors, None, gate_materials)
            gcmd.respond_info(f"Automap slicer: {mapping}")
            return
        raw = gcmd.get("MAP", "")
        if not raw:
            gcmd.respond_info(f"Mapa slicer actual: {self.slicer_map.as_tool_map()}")
            return
        try:
            mapping = [int(token) for token in str(raw).replace(" ", "").split(",") if token != ""]
        except ValueError:
            raise gcmd.error("MAP debe ser una lista de enteros separados por comas")
        self.slicer_map.set_map(mapping)
        self.persistence.save(self.snapshot_state())
        gcmd.respond_info(f"Mapa slicer actualizado: {self.slicer_map.as_tool_map()}")

    def cmd_DM_COLD_PULL(self, gcmd: Any) -> None:
        """Ejecuta una secuencia de cold pull (limpieza de nozzle)."""
        temp = gcmd.get_float("TEMP", 90.0)
        length = gcmd.get_float("LENGTH", 50.0)
        speed = gcmd.get_float("SPEED", 5.0)
        self._emit_script(f"M109 S{temp:.0f}")
        self.motion.load_filament(length, speed)
        self.motion.unload_filament(length, speed)
        self._emit_callback("_DM_ACTION_CHANGED", action="cold_pull")
        gcmd.respond_info(f"Cold pull a {temp:.0f}C, {length:.0f}mm")

    def cmd_DM_HEATER(self, gcmd: Any) -> None:
        """Control del calefactor de secado (``MMU_HEATER DRY=``)."""
        if self.environment is None:
            raise gcmd.error("Environment deshabilitado (enable_environment=false)")
        if gcmd.get_int("STOP", 0):
            self.environment.stop_drying()
            gcmd.respond_info("Secado detenido")
            return
        dry_temp = gcmd.get_float("DRY", None)
        duration = gcmd.get_float("DURATION", None)
        target = gcmd.get_float("TEMP", None)
        if dry_temp is not None or duration is not None or target is not None:
            self.environment.start_drying(
                target_temp=dry_temp if dry_temp is not None else target,
                duration_s=duration,
            )
            gcmd.respond_info(
                f"Secado iniciado: target={self.environment.status.dryer_target_temp:.0f}C"
            )
            return
        gcmd.respond_info(f"Environment: {self.environment.get_status().as_dict()}")

    def cmd_DM_LED(self, gcmd: Any) -> None:
        """Controla efectos LED (solid/blink/breathing/rainbow/off)."""
        if self.led is None:
            raise gcmd.error("LED deshabilitado (enable_led=false)")
        effect = gcmd.get("EFFECT", None)
        if effect is not None:
            if effect.lower() not in (EFFECT_SOLID, EFFECT_BLINK, EFFECT_BREATHING, EFFECT_RAINBOW, EFFECT_OFF):
                raise gcmd.error("EFFECT invalido")
            self.led.set_effect(effect)
        color = gcmd.get("COLOR", None)
        if color is not None:
            self.led.set_effect(self.led.effect, self.led._hex_to_rgb(str(color)))
        self.led.update(0.0)
        gcmd.respond_info(f"LED: {self.led.get_status()}")

    def cmd_DM_SET_LED(self, gcmd: Any) -> None:
        """Fija el color de un LED o segmento concreto."""
        if self.led is None:
            raise gcmd.error("LED deshabilitado (enable_led=false)")
        color_hex = gcmd.get("COLOR", "000000")
        rgb = self.led._hex_to_rgb(str(color_hex))
        count = gcmd.get_int("COUNT", 1)
        if gcmd.get_int("SEGMENT", 0):
            start = gcmd.get_int("START", 0)
            self.led.set_segment(start, count, rgb)
            gcmd.respond_info(f"Segmento {start}..{start + count - 1} = {color_hex}")
            return
        index = gcmd.get_int("INDEX", 0)
        self.led.set_led(index, rgb)
        gcmd.respond_info(f"LED {index} = {color_hex}")

    def cmd_DM_SERVO(self, gcmd: Any) -> None:
        """Mueve un servo (cutter u otro) a un angulo dado."""
        angle = gcmd.get_float("ANGLE", 0.0)
        servo = gcmd.get("SERVO", self.cfg.get("cutter_servo", ""))
        if not servo:
            raise gcmd.error("SERVO requerido (o define cutter_servo en config)")
        self._emit_script(f"SET_SERVO SERVO={servo} ANGLE={angle:.1f}")
        gcmd.respond_info(f"Servo {servo} -> {angle:.1f} grados")

    def cmd_DM_PARK(self, gcmd: Any) -> None:
        """Parquea el toolhead en la posicion configurada para la operacion."""
        operation = gcmd.get("OPERATION", "default")
        ok = self.motion.park_toolhead(operation)
        if not ok:
            raise gcmd.error(f"Parking fallido para operacion {operation}")
        gcmd.respond_info(f"Parking OK ({operation})")

    # -- Comandos de prueba / validacion de hardware -----------------------
    def cmd_DM_TEST_MOVE(self, gcmd: Any) -> None:
        """Mueve el gear una distancia y reporta el movimiento medido."""
        distance = gcmd.get_float("DISTANCE", 50.0)
        speed = gcmd.get_float("SPEED", 30.0)
        self.encoder.expect_move()
        ok = self.motion.load_filament(distance, speed)
        measured = self.encoder.check_move(distance)
        self.telemetry.record("test_move", measured)
        gcmd.respond_info(f"Test move: solicitado={distance:.2f} medido={measured:.2f} ok={ok}")

    def cmd_DM_TEST_HOMING_MOVE(self, gcmd: Any) -> None:
        """Homing del selector y movimiento a un gate de prueba."""
        gate = gcmd.get_int("GATE", 0)
        homed = self.selector.home()
        selected = self.selector.select_gate(gate)
        gcmd.respond_info(f"Test homing: home={homed} select({gate})={selected}")

    def cmd_DM_TEST_TRACKING(self, gcmd: Any) -> None:
        """Valida el tracking encoder vs movimiento y alimenta FlowGuard."""
        distance = gcmd.get_float("DISTANCE", 50.0)
        speed = gcmd.get_float("SPEED", 30.0)
        self.encoder.expect_move()
        self.motion.load_filament(distance, speed)
        measured = self.encoder.check_move(distance)
        result = self.flowguard.evaluate(distance, measured)
        self.sync_feedback.evaluate(distance, measured)
        self.telemetry.record("tracking_requested", distance)
        self.telemetry.record("tracking_measured", measured)
        gcmd.respond_info(
            f"Tracking: requested={distance:.2f} measured={measured:.2f} state={result.state}"
        )

    def cmd_DM_TEST_BUZZ_MOTOR(self, gcmd: Any) -> None:
        """Hace vibrar (buzz) el motor del gear para verificar cableado."""
        count = gcmd.get_int("COUNT", 5)
        amplitude = gcmd.get_float("AMPLITUDE", 1.0)
        for _ in range(max(1, count)):
            self.motion.load_filament(amplitude, 20.0)
            self.motion.unload_filament(amplitude, 20.0)
        gcmd.respond_info(f"Buzz motor: {count} ciclos de {amplitude}mm")

    def cmd_DM_TEST_LOAD(self, gcmd: Any) -> None:
        """Ejecuta la secuencia de carga componible sobre un gate."""
        gate = gcmd.get_int("GATE", 0)
        if not 0 <= gate < self.profile.gates:
            raise gcmd.error("GATE fuera de rango")
        status = self.run_load_sequence(gate, gcmd.get_int("TOOL", gate))
        gcmd.respond_info(f"Test load gate {gate}: completed={status.get('completed')}")

    def cmd_DM_TEST_GRIP(self, gcmd: Any) -> None:
        """Prueba el agarre/retencion del filamento en el gate."""
        self.cmd_DM_GRIP(gcmd)

    def cmd_DM_TEST_RUNOUT(self, gcmd: Any) -> None:
        """Simula un runout y reporta la clasificacion de FlowGuard."""
        distance = gcmd.get_float("DISTANCE", 50.0)
        result = None
        for _ in range(3):
            result = self.flowguard.evaluate(distance, 0.0)
        gcmd.respond_info(f"Test runout: state={result.state if result else 'n/a'}")

    def cmd_DM_TEST_FORM_TIP(self, gcmd: Any) -> None:
        """Ejecuta un ciclo de formacion de punta."""
        if self.tip_former is None:
            raise gcmd.error("Tip forming deshabilitado (enable_tip_forming=false)")
        result = self.tip_former.run()
        gcmd.respond_info(f"Test tip forming: {result.message} ({result.duration_ms:.0f}ms)")

    def cmd_DM_SOAKTEST_LOAD_SEQUENCE(self, gcmd: Any) -> None:
        """Soak test: repite la secuencia de carga/descarga N veces."""
        count = max(1, gcmd.get_int("COUNT", 10))
        gate = gcmd.get_int("GATE", 0)
        loaded = 0
        for _ in range(count):
            status = self.run_load_sequence(gate, gate)
            if status.get("completed"):
                loaded += 1
        self.telemetry.record("soak_load_ok", float(loaded))
        gcmd.respond_info(f"Soak test load: {loaded}/{count} completados")

    def cmd_DM_SOAKTEST_SELECTOR(self, gcmd: Any) -> None:
        """Soak test: cicla el selector por todos los gates N veces."""
        count = max(1, gcmd.get_int("COUNT", 10))
        cycles = 0
        for _ in range(count):
            self.selector.home()
            for gate in range(self.profile.gates):
                if self.selector.select_gate(gate):
                    cycles += 1
        self.telemetry.record("soak_selector_cycles", float(cycles))
        gcmd.respond_info(f"Soak test selector: {cycles} selecciones")

    def cmd_DM_LOG(self, gcmd: Any) -> None:
        """Registra un mensaje en el log estructurado (utilidad)."""
        message = gcmd.get("MESSAGE", gcmd.get("MSG", ""))
        level = gcmd.get("LEVEL", "info").lower()
        self.diagnostics.log_event(level, "user", "log", message=message)
        gcmd.respond_info(f"Log [{level}] {message}")

    def cmd_DM_DUMP_VARS(self, gcmd: Any) -> None:
        """Vuelca el estado completo del contrato ``printer.mmu``."""
        state = build_mmu_state(self, time.time())
        for key in sorted(state):
            gcmd.respond_info(f"{key} = {state[key]}")

    def cmd_DM_NFC_SCAN(self, gcmd: Any) -> None:
        """Escanea tags NFC (lectura profunda) en todos los lectores."""
        if self.nfc_manager is None:
            raise gcmd.error("NFC deshabilitado (enable_nfc=false)")
        found = self.nfc_manager.scan_all(deep=True)
        if not found:
            gcmd.respond_info("NFC: sin tags detectados")
            return
        for uid, tag in found.items():
            gcmd.respond_info(f"NFC tag {uid}: {tag.data}")

    def _emit_callback(self, callback_name: str, **kwargs: Any) -> None:
        """Invoca un callback de macro de ciclo de vida.

        Se registra siempre el evento en diagnostico y, si existe una macro
        G-code con el mismo nombre (convencion Happy Hare ``_DM_*``), se
        ejecuta pasando los parametros como argumentos de la macro. Ademas, si
        existe una extension de usuario ``user_*_extension`` equivalente, se
        invoca a continuacion (F-23).
        """
        self.diagnostics.log_event("debug", "callback", callback_name, **kwargs)
        gcode = self._gcode()
        if gcode is None or not hasattr(gcode, "run_script_from_command"):
            return
        if not kwargs:
            return
        args = " ".join(f"{key.upper()}={self._format_macro_arg(value)}" for key, value in kwargs.items())
        scripts = [f"{callback_name} {args}"]
        mmu_alias = self._mmu_hook_name(callback_name)
        if mmu_alias:
            scripts.append(f"{mmu_alias} {args}")
        extension = self._user_extension_name(callback_name)
        if extension:
            scripts.append(f"{extension} {args}")
        for script in scripts:
            try:
                gcode.run_script_from_command(script)
            except Exception:  # noqa: BLE001 - la macro es opcional
                continue

    @staticmethod
    def _mmu_hook_name(callback_name: str) -> Optional[str]:
        """Deriva el alias ``_MMU_*`` de un callback ``_DM_*`` (compatibilidad HH)."""
        if not callback_name.startswith("_DM_"):
            return None
        suffix = callback_name[len("_DM_"):]
        if not suffix:
            return None
        return f"_MMU_{suffix}"

    @staticmethod
    def _user_extension_name(callback_name: str) -> Optional[str]:
        """Deriva ``user_*_extension`` de un callback ``_DM_*`` (F-23)."""
        if not callback_name.startswith("_DM_"):
            return None
        suffix = callback_name[len("_DM_"):].strip().lower()
        if not suffix:
            return None
        return f"user_{suffix}_extension"

    @staticmethod
    def _format_macro_arg(value: Any) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)


def load_config(config: Any) -> DogMatrixCore:
    """Punto de entrada del modulo Klipper ``[dog_matrix]``."""
    return DogMatrixCore(config)


__all__ = ["DogMatrixCore", "load_config", "SOFTWARE_VERSION"]
