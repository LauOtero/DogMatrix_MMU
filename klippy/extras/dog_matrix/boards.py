"""Sistema integral de configuracion de pines y registro de placas.

Objetivo (informe, requisito de configuracion de pines): ofrecer un **mapeo
uniforme** de pines para todas las placas soportadas, con un namespace de
alias estable e independiente del hardware:

- ``MMU_*``: alias compatibles con Happy Hare (retrocompatibilidad total).
- ``DM_*``: espejo con la nomenclatura nativa de Dog Matrix.

El modulo es autocontenido (no importa Klipper) para poder reutilizarse desde
el instalador. Modela:

- ``BoardDefinition``: descripcion fisica de una placa (drivers, sensores,
  perifericos, capacidades, topologia y limites de hardware).
- ``PinPlan``: resolucion rol -> alias -> pin fisico para una configuracion
  concreta, reservando todos los recursos de ``coils_per_unit`` bobinas.
- ``build_pin_plan``: construye el plan (con ``COILS_PER_UNIT`` bobinas por
  unidad para las placas gear-per-gate de Dog Matrix, o N gates para las
  selectoras ERCF).
- ``validate_pin_plan`` / ``validate_board_definition``: deteccion de
  conflictos de recursos (mismo pin en dos funciones).
- ``render_board_pins``: render del bloque ``[board_pins]`` de Klipper.

Las definiciones de placa viven en ``config/boards/*.yaml`` y siguen
``schema_version: 1``.
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .capabilities import _load_document

SCHEMA_VERSION = 1

#: Bobinas gestionadas por cada unidad/placa Dog Matrix (diseno gear-per-gate).
COILS_PER_UNIT = 4

#: Limites de gates soportados por las placas selectoras (ERCF y variantes).
SELECTOR_MAX_GATES = 12

ALIAS_PREFIX = "MMU_"
MIRROR_PREFIX = "DM_"

TOPOLOGY_SELECTOR = "selector"
TOPOLOGY_GEAR_PER_GATE = "gear_per_gate"

CONFIG_DIRNAME = "config"
BOARDS_DIRNAME = "boards"

#: Rol canonico -> sufijo de alias uniforme.
ROLE_ALIASES: "OrderedDict[str, str]" = OrderedDict(
    [
        ("gear_uart", "GEAR_UART"),
        ("gear_step", "GEAR_STEP"),
        ("gear_dir", "GEAR_DIR"),
        ("gear_enable", "GEAR_ENABLE"),
        ("gear_diag", "GEAR_DIAG"),
        ("selector_uart", "SEL_UART"),
        ("selector_step", "SEL_STEP"),
        ("selector_dir", "SEL_DIR"),
        ("selector_enable", "SEL_ENABLE"),
        ("selector_diag", "SEL_DIAG"),
        ("selector_endstop", "SEL_ENDSTOP"),
        ("encoder", "ENCODER"),
        ("gate_sensor", "GATE_SENSOR"),
        ("shared_exit", "SHARED_EXIT"),
        ("i2c_scl", "I2C_SCL"),
        ("i2c_sda", "I2C_SDA"),
        ("servo", "SERVO"),
        ("cut_servo", "CUT_SERVO"),
        ("neopixel", "NEOPIXEL"),
        ("pre_gate", "PRE_GATE"),
        ("post_gate", "POST_GATE"),
    ]
)

#: Clasificacion de recursos por categoria (requisito de reserva de hardware).
RESOURCE_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "control": ("step", "dir", "enable"),
    "power": ("enable",),
    "communication": ("uart", "i2c"),
    "sensors": ("diag", "endstop", "encoder", "gate_sensor", "shared_exit", "pre_gate", "post_gate"),
    "peripherals": ("servo", "cut_servo", "neopixel"),
}

_MODIFIERS = "!^~"


class BoardError(Exception):
    """Placa ausente, ilegible o invalida."""


def boards_dir(directory: Optional[str] = None) -> Path:
    """Localiza ``config/boards`` (override con DM_BOARDS_DIR)."""
    if directory:
        return Path(directory).expanduser()
    env = os.environ.get("DM_BOARDS_DIR")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[3] / CONFIG_DIRNAME / BOARDS_DIRNAME


def _normalize_pin(pin: str) -> str:
    """Elimina modificadores Klipper (``!``, ``^``, ``~``) del pin."""
    return re.sub(rf"^[{re.escape(_MODIFIERS)}]+", "", str(pin))


# ---------------------------------------------------------------------------
# Modelo de placa
# ---------------------------------------------------------------------------
@dataclass
class BoardDefinition:
    """Descripcion fisica de una placa controladora de MMU."""

    board_id: str
    display_name: str
    topology: str = TOPOLOGY_SELECTOR
    mcu_name: str = "mmu"
    mcu_transport: str = "usb"
    mcu_arch: str = ""
    i2c_bus: str = ""
    coils_per_unit: int = COILS_PER_UNIT
    max_gates: int = SELECTOR_MAX_GATES
    drivers: List[Dict[str, Any]] = field(default_factory=list)
    pins: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, bool] = field(default_factory=dict)
    shared_pins: List[str] = field(default_factory=list)
    #: Si es cierto, los pines de ``pre_gate`` pueden reutilizarse como
    #: ``post_gate`` (mismo conector; el rol depende del cableado).
    dual_gate_sensors: bool = False
    #: Perifericos soportados en el conector I2C (humidity/temperature/mux/rfid).
    i2c_devices: List[str] = field(default_factory=list)
    happy_hare_board_type: str = "OTHER"
    source_url: str = ""
    notes: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def is_multi_topology(self) -> bool:
        return self.topology in (TOPOLOGY_SELECTOR, TOPOLOGY_GEAR_PER_GATE, "both")

    def supports(self, topology: str) -> bool:
        return self.topology == "both" or self.topology == topology

    def default_topology(self) -> str:
        if self.topology == "both":
            return TOPOLOGY_SELECTOR
        return self.topology if self.topology != "both" else TOPOLOGY_SELECTOR

    def driver_count(self) -> int:
        return len(self.drivers)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "board_id": self.board_id,
            "display_name": self.display_name,
            "topology": self.topology,
            "mcu": {"name": self.mcu_name, "transport": self.mcu_transport, "arch": self.mcu_arch},
            "i2c_bus": self.i2c_bus,
            "coils_per_unit": self.coils_per_unit,
            "max_gates": self.max_gates,
            "drivers": [dict(driver) for driver in self.drivers],
            "pins": dict(self.pins),
            "capabilities": dict(self.capabilities),
            "shared_pins": list(self.shared_pins),
            "dual_gate_sensors": self.dual_gate_sensors,
            "i2c_devices": list(self.i2c_devices),
            "happy_hare_board_type": self.happy_hare_board_type,
            "source_url": self.source_url,
        }


@dataclass
class PinPlanConflict:
    """Conflicto: un pin fisico asignado a mas de una funcion."""

    pin: str
    aliases: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {"pin": self.pin, "aliases": list(self.aliases)}


@dataclass
class PinPlan:
    """Resolucion rol -> alias -> pin fisico para una placa concreta."""

    board_id: str
    display_name: str
    mcu_name: str
    topology: str
    gates: int
    units: int
    coils_per_unit: int
    aliases: "OrderedDict[str, str]" = field(default_factory=OrderedDict)
    pin_roles: Dict[str, List[str]] = field(default_factory=dict)
    shared_pins: List[Tuple[str, str]] = field(default_factory=list)
    reserved: Dict[str, str] = field(default_factory=dict)
    board_type: str = "OTHER"
    i2c_bus: str = ""

    def resolve(self, alias: str) -> Optional[str]:
        return self.aliases.get(alias)

    def coils(self) -> List[Dict[str, str]]:
        """Devuelve, por bobina, el mapeo de roles de control reservados."""
        result: List[Dict[str, str]] = []
        for index in range(self.coils_per_unit):
            suffix = "" if index == 0 else f"_{index}"
            coil: Dict[str, str] = {"index": index}
            for role in ("gear_step", "gear_dir", "gear_enable", "gear_uart", "gear_diag"):
                alias = f"{ALIAS_PREFIX}{ROLE_ALIASES[role]}{suffix}"
                if alias in self.aliases:
                    coil[role] = self.aliases[alias]
            if coil:
                result.append(coil)
        return result

    def resource_summary(self) -> Dict[str, List[str]]:
        """Agrupa los pines reservados por categoria de hardware."""
        summary: Dict[str, List[str]] = {name: [] for name in RESOURCE_CATEGORIES}
        for alias, pin in self.aliases.items():
            role = alias[len(ALIAS_PREFIX):] if alias.startswith(ALIAS_PREFIX) else alias
            base = role.rsplit("_", 1)[0] if role[-1:].isdigit() else role
            lowered = base.lower()
            for category, keys in RESOURCE_CATEGORIES.items():
                if any(key in lowered for key in keys):
                    summary[category].append(alias)
        return summary

    def as_dict(self) -> Dict[str, Any]:
        return {
            "board_id": self.board_id,
            "display_name": self.display_name,
            "mcu": self.mcu_name,
            "topology": self.topology,
            "gates": self.gates,
            "units": self.units,
            "coils_per_unit": self.coils_per_unit,
            "aliases": dict(self.aliases),
            "pin_roles": {pin: list(roles) for pin, roles in self.pin_roles.items()},
            "shared_pins": [list(pair) for pair in self.shared_pins],
            "reserved": dict(self.reserved),
            "i2c_bus": self.i2c_bus,
        }


# ---------------------------------------------------------------------------
# Carga de placas
# ---------------------------------------------------------------------------
def _board_from_document(document: Dict[str, Any], fallback_id: str) -> BoardDefinition:
    mcu = document.get("mcu") or {}
    if not isinstance(mcu, dict):
        mcu = {}
    drivers = document.get("drivers") or []
    if not isinstance(drivers, list):
        drivers = []
    pins = document.get("pins") or {}
    if not isinstance(pins, dict):
        pins = {}
    capabilities = document.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        capabilities = {}
    shared = document.get("shared_pins") or []
    shared_pins = [str(item) for item in shared] if isinstance(shared, list) else []
    i2c = document.get("i2c") or {}
    i2c_bus = str(document.get("i2c_bus", ""))
    if not i2c_bus and isinstance(i2c, dict):
        i2c_bus = str(i2c.get("bus", ""))
    i2c_devices = i2c.get("devices") if isinstance(i2c, dict) else None
    if not isinstance(i2c_devices, list):
        i2c_devices = []
    return BoardDefinition(
        board_id=str(document.get("board_id", fallback_id)),
        display_name=str(document.get("display_name", document.get("board_id", fallback_id))),
        topology=str(document.get("topology", TOPOLOGY_SELECTOR)),
        mcu_name=str(mcu.get("name", "mmu")),
        mcu_transport=str(mcu.get("transport", "usb")),
        mcu_arch=str(mcu.get("arch", "")),
        i2c_bus=i2c_bus,
        coils_per_unit=int(document.get("coils_per_unit", COILS_PER_UNIT)),
        max_gates=int(document.get("max_gates", SELECTOR_MAX_GATES)),
        drivers=[dict(driver) for driver in drivers if isinstance(driver, dict)],
        pins=dict(pins),
        capabilities={str(k): bool(v) for k, v in capabilities.items()},
        shared_pins=shared_pins,
        dual_gate_sensors=bool(document.get("dual_gate_sensors", False)),
        i2c_devices=[str(item) for item in i2c_devices],
        happy_hare_board_type=str(document.get("happy_hare_board_type", "OTHER")),
        source_url=str(document.get("source_url", "")),
        notes=str(document.get("notes", "")),
        raw=document,
    )


def load_board(board_id: str, directory: Optional[str] = None) -> BoardDefinition:
    """Carga una placa por identificador. Lanza ``BoardError`` si falla."""
    base = boards_dir(directory)
    candidate = Path(board_id).expanduser()
    if candidate.exists():
        path = candidate
    else:
        path = base / f"{board_id}.yaml"
        if not path.exists():
            path = base / f"{board_id}.json"
    if not path.exists():
        raise BoardError(f"placa no encontrada: {board_id} (buscado en {base})")
    try:
        document = _load_document(path)
    except (OSError, ValueError) as exc:
        raise BoardError(f"placa ilegible: {exc}") from exc
    if not isinstance(document, dict):
        raise BoardError("el documento de placa debe ser un mapa de nivel superior")
    if int(document.get("schema_version", 0)) != SCHEMA_VERSION:
        raise BoardError(
            f"schema_version no soportada: {document.get('schema_version')!r} "
            f"(esperada {SCHEMA_VERSION})"
        )
    return _board_from_document(document, path.stem)


def list_boards(directory: Optional[str] = None) -> List[str]:
    """Lista los identificadores de placa disponibles (ordenados)."""
    base = boards_dir(directory)
    if not base.exists():
        return []
    return sorted(path.stem for path in base.glob("*.yaml"))


def load_all_boards(directory: Optional[str] = None) -> Dict[str, BoardDefinition]:
    """Carga todas las placas disponibles."""
    return {board_id: load_board(board_id, directory) for board_id in list_boards(directory)}


def happy_hare_board_catalog() -> List[Dict[str, str]]:
    """Catalogo de tipos de placa (``BOARD_TYPE_*``) de Happy Hare.

    Mapea cada tipo a una placa Dog Matrix (``archetype``) para soporte nativo.
    Los tipos sin pinout publico se mapean a arquetipos genericos que requieren
    que el usuario confirme/ajuste los pines (mismo comportamiento que el
    ``Other``/``Manual`` de Happy Hare).
    """
    return [
        {"board_type": "EASY_BRD", "menu_name": "Standard EASY-BRD (SAMD21)", "archetype": "ercf_easy_brd_v1_1"},
        {"board_type": "EASY_BRD_RP2040", "menu_name": "EASY-BRD (RP2040)", "archetype": "ercf_easy_brd_v1_1"},
        {"board_type": "MELLOW_EASY_BRD_CAN_1", "menu_name": "Mellow EASY-BRD v1.x", "archetype": "mellow_fly_ercf_v1"},
        {"board_type": "MELLOW_EASY_BRD_CAN_2", "menu_name": "Mellow EASY-BRD v2.x", "archetype": "mellow_fly_ercf_v2"},
        {"board_type": "ERB_1", "menu_name": "Fysetc Burrows ERB v1", "archetype": "fysetc_erb_v1"},
        {"board_type": "ERB_2", "menu_name": "Fysetc Burrows ERB v2", "archetype": "fysetc_erb_v2"},
        {"board_type": "SKR_PICO_1", "menu_name": "BTT SKR Pico v1.0", "archetype": "generic_selector"},
        {"board_type": "EBB42_1_2", "menu_name": "BTT EBB42 (gen 1.2)", "archetype": "generic_selector"},
        {"board_type": "MMB_1_0", "menu_name": "BTT MMB CAN v1.0", "archetype": "btt_mmb_can_v1_0"},
        {"board_type": "MMB_1_1", "menu_name": "BTT MMB CAN v1.1", "archetype": "btt_mmb_can_v1_1"},
        {"board_type": "MMB_2_0", "menu_name": "BTT MMB CAN v2.0", "archetype": "btt_mmb_can_v2_0"},
        {"board_type": "KMS_1_0", "menu_name": "BIQU KMS MCU", "archetype": "generic_gear_per_gate"},
        {"board_type": "VVD_1_0", "menu_name": "BTT ViViD MCU", "archetype": "generic_gear_per_gate"},
        {"board_type": "QIDI_BOX_2_0", "menu_name": "QIDI Box v2 MCU", "archetype": "generic_gear_per_gate"},
        {"board_type": "AFC_LITE_1", "menu_name": "AFC Lite v1", "archetype": "generic_selector"},
        {"board_type": "AFC_PRO", "menu_name": "AFC Pro", "archetype": "generic_selector"},
        {"board_type": "CHAMELEON_X5_1", "menu_name": "3D Chameleon X5 v1", "archetype": "generic_selector"},
        {"board_type": "OWLFC_MINI_1_0", "menu_name": "OwlFC Mini v1.0", "archetype": "generic_gear_per_gate"},
        {"board_type": "TZB_1_0", "menu_name": "TZB v1.0", "archetype": "generic_selector"},
        {"board_type": "WGB_3_0", "menu_name": "WGB v3.0", "archetype": "generic_selector"},
        {"board_type": "OTHER", "menu_name": "Not listed / Other", "archetype": "generic_selector"},
        {"board_type": "MANUAL", "menu_name": "Shared / externally configured MCU", "archetype": "generic_selector"},
    ]


# ---------------------------------------------------------------------------
# Construccion del plan de pines
# ---------------------------------------------------------------------------
def _driver_roles(driver: Dict[str, Any]) -> Dict[str, Any]:
    return {key: driver[key] for key in ("step", "dir", "enable", "uart", "diag") if driver.get(key)}


def _add_alias(
    plan_aliases: "OrderedDict[str, str]",
    plan_pin_roles: Dict[str, List[str]],
    mcu_name: str,
    suffix: str,
    pin: str,
    emit_dm_aliases: bool,
) -> None:
    alias = f"{ALIAS_PREFIX}{suffix}"
    plan_aliases[alias] = pin
    normalized = f"{mcu_name}:{_normalize_pin(pin)}"
    plan_pin_roles.setdefault(normalized, []).append(alias)
    if emit_dm_aliases:
        mirror = f"{MIRROR_PREFIX}{suffix}"
        plan_aliases[mirror] = pin
        plan_pin_roles[normalized].append(mirror)


def build_pin_plan(
    board: BoardDefinition,
    gates: Optional[int] = None,
    units: int = 1,
    topology: Optional[str] = None,
    coils_per_unit: Optional[int] = None,
    mcu_name: Optional[str] = None,
    emit_dm_aliases: bool = True,
) -> PinPlan:
    """Construye el plan de pines reservando los recursos de cada bobina.

    - ``topology == gear_per_gate``: reserva ``coils_per_unit`` (por defecto 4)
      conjuntos de control STEP/DIR/ENABLE/UART/DIAG, uno por bobina, mas los
      recursos compartidos (encoder, sensores, servo, neopixel).
    - ``topology == selector``: un driver compartido + selector, soportando
      ``gates`` configurable (4, 9, 12, ...) como en ERCF.
    """
    resolved_mcu = mcu_name or board.mcu_name
    requested_topology = topology or board.default_topology()
    resolved_topology = (
        requested_topology if board.supports(requested_topology) else board.default_topology()
    )
    resolved_coils = max(1, int(coils_per_unit or board.coils_per_unit or COILS_PER_UNIT))

    plan = PinPlan(
        board_id=board.board_id,
        display_name=board.display_name,
        mcu_name=resolved_mcu,
        topology=resolved_topology,
        gates=0,
        units=max(1, int(units)),
        coils_per_unit=resolved_coils,
        board_type=board.happy_hare_board_type,
        i2c_bus=board.i2c_bus,
    )
    plan.shared_pins = _parse_shared_pairs(board.shared_pins)

    if resolved_topology == TOPOLOGY_GEAR_PER_GATE:
        # Cada placa/unidad gear-per-gate controla exactamente coils_per_unit
        # bobinas (4 por defecto); las unidades multiples son MCUs separados.
        plan.gates = resolved_coils
        for index in range(resolved_coils):
            if index >= len(board.drivers):
                break
            suffix = "" if index == 0 else f"_{index}"
            for role, pin in _driver_roles(board.drivers[index]).items():
                if role in ("step", "dir", "enable", "uart", "diag"):
                    _add_alias(
                        plan.aliases, plan.pin_roles, resolved_mcu,
                        f"GEAR_{role.upper()}{suffix}", str(pin), emit_dm_aliases,
                    )
    else:
        plan.gates = int(gates or board.max_gates)
        drivers = board.drivers
        selector_driver = drivers[0] if drivers else {}
        gear_driver = drivers[1] if len(drivers) > 1 else selector_driver
        for role, pin in _driver_roles(selector_driver).items():
            _add_alias(plan.aliases, plan.pin_roles, resolved_mcu, f"SEL_{role.upper()}", str(pin), emit_dm_aliases)
        for role, pin in _driver_roles(gear_driver).items():
            _add_alias(plan.aliases, plan.pin_roles, resolved_mcu, f"GEAR_{role.upper()}", str(pin), emit_dm_aliases)

    # Pines no pertenecientes a drivers (sensores y perifericos).
    pins_view: Dict[str, Any] = dict(board.pins)
    # Uso dual pre_gate/post_gate: el mismo conector sirve para detectar la
    # ausencia de filamento (pre-gate) y la llegada al post-gate. Al activarse
    # el pre-gate se carga hasta que se activa el post-gate.
    if board.dual_gate_sensors and "post_gate" not in pins_view and isinstance(pins_view.get("pre_gate"), list):
        pre_gate = list(pins_view["pre_gate"])
        pins_view["post_gate"] = pre_gate
        for index in range(len(pre_gate)):
            plan.shared_pins.append((f"PRE_GATE_{index}", f"POST_GATE_{index}"))
        # Reflejar en el post-gate cualquier comparticion declarada del pre-gate.
        for first, second in list(plan.shared_pins):
            for index in range(len(pre_gate)):
                pre, post = f"PRE_GATE_{index}", f"POST_GATE_{index}"
                if first == pre:
                    plan.shared_pins.append((post, second))
                elif second == pre:
                    plan.shared_pins.append((first, post))
    for role, value in pins_view.items():
        suffix = ROLE_ALIASES.get(role)
        if suffix is None:
            continue
        if role in ("pre_gate", "post_gate"):
            entries = value if isinstance(value, list) else [value]
            limit = min(len(entries), plan.gates) if plan.gates else len(entries)
            for index in range(limit):
                _add_alias(
                    plan.aliases, plan.pin_roles, resolved_mcu,
                    f"{suffix}_{index}", str(entries[index]), emit_dm_aliases,
                )
        else:
            _add_alias(plan.aliases, plan.pin_roles, resolved_mcu, suffix, str(value), emit_dm_aliases)

    # Reserva de recursos extra declarados que no exponen alias (p. ej. endstops
    # libres, fans compartidos o el bus CAN).
    extras = board.raw.get("reserved_pins") or board.raw.get("reserved") or {}
    if isinstance(extras, dict):
        for name, pin in extras.items():
            plan.reserved[str(name)] = str(pin)
    return plan


def _parse_shared_pairs(entries: List[str]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for entry in entries:
        parts = [part.strip() for part in str(entry).split(",") if part.strip()]
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


# ---------------------------------------------------------------------------
# Validacion de conflictos
# ---------------------------------------------------------------------------
def _alias_family(alias: str) -> str:
    """Sufijo uniforme de un alias (sin prefijo MMU_/DM_)."""
    for prefix in (ALIAS_PREFIX, MIRROR_PREFIX):
        if alias.startswith(prefix):
            return alias[len(prefix):]
    return alias


def _is_allowed_share(a: str, b: str, shared_pairs: List[Tuple[str, str]]) -> bool:
    """Indica si dos familias de rol comparten pin de forma legitima."""
    fa, fb = _alias_family(a), _alias_family(b)
    for first, second in shared_pairs:
        if {fa, fb} == {first, second}:
            return True
    return False


def _conflicts_for_pin(
    pin: str, families: List[str], shared_pairs: List[Tuple[str, str]]
) -> Optional[PinPlanConflict]:
    """Conflicto si alguna pareja de familias no esta permitida en el pin."""
    distinct = list(dict.fromkeys(families))
    if len(distinct) <= 1:
        return None
    pairs = [(a, b) for i, a in enumerate(distinct) for b in distinct[i + 1:]]
    if all(_is_allowed_share(a, b, shared_pairs) for a, b in pairs):
        return None
    offending = sorted({f for a, b in pairs if not _is_allowed_share(a, b, shared_pairs) for f in (a, b)})
    return PinPlanConflict(pin=pin, aliases=[f"{ALIAS_PREFIX}{family}" for family in offending])


def validate_pin_plan(plan: PinPlan) -> List[PinPlanConflict]:
    """Detecta pines asignados a funciones distintas en el plan resuelto."""
    conflicts: List[PinPlanConflict] = []
    for pin, aliases in plan.pin_roles.items():
        # Ignorar el espejo DM_* al comparar (comparte pin con su MMU_*).
        families = [_alias_family(alias) for alias in aliases]
        conflict = _conflicts_for_pin(pin, families, plan.shared_pins)
        if conflict is not None:
            conflicts.append(conflict)
    return conflicts


def _driver_role_suffix(board: BoardDefinition, index: int, role: str) -> str:
    """Sufijo de familia para un rol de driver segun la topologia de la placa."""
    role_up = role.upper()
    if board.topology == TOPOLOGY_GEAR_PER_GATE:
        return f"GEAR_{role_up}" if index == 0 else f"GEAR_{role_up}_{index}"
    if index == 0:
        return f"SEL_{role_up}"
    return f"GEAR_{role_up}" if index == 1 else f"GEAR_{role_up}_{index}"


def validate_board_definition(board: BoardDefinition) -> List[PinPlanConflict]:
    """Detecta pines duplicados dentro de la propia definicion de placa."""
    pin_roles: Dict[str, List[str]] = {}
    for index, driver in enumerate(board.drivers):
        for role, pin in _driver_roles(driver).items():
            normalized = f"{board.mcu_name}:{_normalize_pin(str(pin))}"
            pin_roles.setdefault(normalized, []).append(_driver_role_suffix(board, index, role))
    for role, value in board.pins.items():
        suffix = ROLE_ALIASES.get(role)
        if suffix is None:
            continue
        entries = value if isinstance(value, list) else [value]
        for item_index, entry in enumerate(entries):
            normalized = f"{board.mcu_name}:{_normalize_pin(str(entry))}"
            label = f"{suffix}_{item_index}" if isinstance(value, list) else suffix
            pin_roles.setdefault(normalized, []).append(label)
    shared = _parse_shared_pairs(board.shared_pins)
    conflicts: List[PinPlanConflict] = []
    for pin, roles in pin_roles.items():
        conflict = _conflicts_for_pin(pin, roles, shared)
        if conflict is not None:
            conflicts.append(conflict)
    return conflicts


def required_roles(board: BoardDefinition, topology: str) -> List[str]:
    """Roles minimos que debe declarar una placa para una topologia."""
    if topology == TOPOLOGY_GEAR_PER_GATE:
        required = ["step", "dir", "enable"]
    else:
        required = ["step", "dir", "enable"]
    return required


# ---------------------------------------------------------------------------
# Render del bloque [board_pins]
# ---------------------------------------------------------------------------
def render_board_pins(plan: PinPlan, section: str = "dogmatrix") -> str:
    """Renderiza el bloque ``[board_pins]`` de Klipper para el plan dado."""
    lines = [f"[board_pins {section}]", f"mcu: {plan.mcu_name}", "aliases:"]
    for alias, pin in plan.aliases.items():
        lines.append(f"  {alias}={pin}")
    return "\n".join(lines) + "\n"


__all__ = [
    "SCHEMA_VERSION",
    "COILS_PER_UNIT",
    "SELECTOR_MAX_GATES",
    "ALIAS_PREFIX",
    "MIRROR_PREFIX",
    "TOPOLOGY_SELECTOR",
    "TOPOLOGY_GEAR_PER_GATE",
    "ROLE_ALIASES",
    "RESOURCE_CATEGORIES",
    "BoardError",
    "BoardDefinition",
    "PinPlan",
    "PinPlanConflict",
    "boards_dir",
    "load_board",
    "list_boards",
    "load_all_boards",
    "happy_hare_board_catalog",
    "build_pin_plan",
    "validate_pin_plan",
    "validate_board_definition",
    "required_roles",
    "render_board_pins",
]
