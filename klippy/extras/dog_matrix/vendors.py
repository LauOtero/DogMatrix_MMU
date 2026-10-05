"""Catalogo de proveedores/MMU (presets) y resolucion de configuracion.

Replica, de forma declarativa y validable, los *defaults* que Happy Hare v4
deriva por fabricante en su ``installer/mmu_types/Kconfig.*`` (vendor, gates por
unidad, selector, capacidades y placa por defecto). A diferencia de Kconfig:

- No hay DSL propietario: son dataclasses Python inspeccionables y testeables.
- Las capacidades se **derivan** de reglas (topologia) en lugar de duplicarse.
- Soporta **multi-unidad**: N unidades iguales multiplican los gates y se
  numeran de forma contigua (0..N).

El modulo es autocontenido (no importa Klipper) para reutilizarse desde el
instalador.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .capabilities import MMUProfile

SCHEMA_VERSION = 1

TOPOLOGY_GEAR_PER_GATE = "gear_per_gate"
TOPOLOGY_SELECTOR = "selector"

#: Limites fisicos por defecto (usados si el vendor no los sobreescribe).
DEFAULT_LIMITS: Dict[str, float] = {
    "max_load_speed_mm_s": 80,
    "max_unload_speed_mm_s": 100,
    "max_distance_mm": 1500,
    "max_accel_mm_s2": 2000,
    "max_jerk_mm_s3": 10000,
    "sensor_timeout_ms": 500,
    "encoder_error_mm": 5,
    "bowden_length_mm": 600,
    "toolhead_distance_mm": 80,
}

#: Capacidades base por topologia (reglas de derivacion, no datos duplicados).
_BASE_CAPABILITIES: Dict[str, Dict[str, bool]] = {
    TOPOLOGY_GEAR_PER_GATE: {
        "selector": False,
        "encoder": True,
        "gate_sensors": True,
        "toolhead_sensor": True,
        "sync_feedback": False,
        "endless_spool": True,
        "spoolman": True,
        "nfc": False,
        "led": True,
        "cutter": False,
        "servo": False,
        "espooler": False,
    },
    TOPOLOGY_SELECTOR: {
        "selector": True,
        "encoder": True,
        "gate_sensors": True,
        "toolhead_sensor": True,
        "sync_feedback": False,
        "endless_spool": True,
        "spoolman": True,
        "nfc": False,
        "led": True,
        "cutter": False,
        "servo": True,
        "espooler": False,
    },
}


@dataclass
class VendorPreset:
    """Preset de un fabricante/MMU (equivalentes a ``installer/mmu_types``)."""

    vendor_id: str
    display_name: str
    happy_hare_vendor: str
    happy_hare_type: str
    topology_type: str
    gates_per_unit: int
    selector_type: str = "virtual"
    default_board: str = "generic_gear_per_gate"
    capabilities: Dict[str, bool] = field(default_factory=dict)
    limits: Dict[str, float] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)
    notes: str = ""

    def resolved_capabilities(self) -> Dict[str, bool]:
        """Capacidades derivadas de la topologia + overrides del vendor."""
        base = dict(_BASE_CAPABILITIES[self.topology_type])
        base.update(self.capabilities)
        return base

    def resolved_limits(self) -> Dict[str, float]:
        merged = dict(DEFAULT_LIMITS)
        merged.update(self.limits)
        return merged

    def as_dict(self) -> Dict[str, Any]:
        return {
            "vendor_id": self.vendor_id,
            "display_name": self.display_name,
            "happy_hare_vendor": self.happy_hare_vendor,
            "happy_hare_type": self.happy_hare_type,
            "topology_type": self.topology_type,
            "selector_type": self.selector_type,
            "gates_per_unit": self.gates_per_unit,
            "default_board": self.default_board,
            "capabilities": self.resolved_capabilities(),
            "aliases": list(self.aliases),
            "notes": self.notes,
        }


#: Catalogo de vendors (valores por defecto segun Happy Hare v4).
VENDORS: Dict[str, VendorPreset] = {
    "vvd": VendorPreset(
        vendor_id="vvd",
        display_name="BTT ViViD",
        happy_hare_vendor="VVD",
        happy_hare_type="VVD_1_0",
        topology_type=TOPOLOGY_SELECTOR,
        selector_type="linear",
        gates_per_unit=4,
        default_board="generic_selector",
        capabilities={"encoder": False, "led": True, "nfc": True, "espooler": False, "sync_feedback": False},
        aliases=["vivid", "btt_vivid"],
        notes="Selector indexado (un switch por gate). 4 gates fijos por unidad.",
    ),
    "box_turtle": VendorPreset(
        vendor_id="box_turtle",
        display_name="Box Turtle",
        happy_hare_vendor="BoxTurtle",
        happy_hare_type="BOX_TURTLE_1_0",
        topology_type=TOPOLOGY_GEAR_PER_GATE,
        selector_type="virtual",
        gates_per_unit=4,
        default_board="dogmatrix_unit_4coil",
        capabilities={"espooler": True, "sync_feedback": True, "gate_sensors": True},
        aliases=["boxturtle", "bt"],
        notes="Gear-per-gate tipo-B; 4 bobinas por unidad. Encadenable N unidades.",
    ),
    "ercf_1_1": VendorPreset(
        vendor_id="ercf_1_1",
        display_name="ERCF v1.1",
        happy_hare_vendor="ERCF",
        happy_hare_type="ERCF_1_1",
        topology_type=TOPOLOGY_SELECTOR,
        selector_type="linear",
        gates_per_unit=9,
        default_board="ercf_easy_brd_v1_1",
        capabilities={"servo": True, "led": True, "cutter": False},
        aliases=["ercf", "ercf_v1", "ercf_v1_1"],
        notes="Selector de carro + servo. 9 gates por unidad.",
    ),
    "ercf_2_0": VendorPreset(
        vendor_id="ercf_2_0",
        display_name="ERCF v2.0",
        happy_hare_vendor="ERCF",
        happy_hare_type="ERCF_2_0",
        topology_type=TOPOLOGY_SELECTOR,
        selector_type="linear",
        gates_per_unit=8,
        default_board="btt_mmb_can_v2_0",
        capabilities={"servo": True, "led": True},
        aliases=["ercf_v2", "ercf_v2_0"],
        notes="Community edition ERCFv2. 8 gates por unidad.",
    ),
    "emu": VendorPreset(
        vendor_id="emu",
        display_name="EMU",
        happy_hare_vendor="EMU",
        happy_hare_type="EMU_1_0",
        topology_type=TOPOLOGY_GEAR_PER_GATE,
        selector_type="virtual",
        gates_per_unit=5,
        default_board="generic_gear_per_gate",
        capabilities={"sync_feedback": True, "gate_sensors": True},
        aliases=["emu_1_0"],
        notes="Tipo-B con MCU por carril opcional; 5 gates por unidad.",
    ),
    "tradrack": VendorPreset(
        vendor_id="tradrack",
        display_name="Tradrack",
        happy_hare_vendor="Tradrack",
        happy_hare_type="TRADRACK_1_0",
        topology_type=TOPOLOGY_SELECTOR,
        selector_type="linear",
        gates_per_unit=10,
        default_board="mellow_fly_ercf_v1",
        capabilities={"servo": True, "encoder": False, "sync_feedback": False},
        aliases=["trad_rack"],
        notes="Selector lineal con servo; sensor de salida compartido. 10 gates/unidad.",
    ),
    "night_owl": VendorPreset(
        vendor_id="night_owl",
        display_name="Night Owl",
        happy_hare_vendor="NightOwl",
        happy_hare_type="NIGHT_OWL_1_0",
        topology_type=TOPOLOGY_GEAR_PER_GATE,
        selector_type="virtual",
        gates_per_unit=2,
        default_board="generic_gear_per_gate",
        aliases=["nightowl"],
        notes="Tipo-B compacto; 2 gates por unidad.",
    ),
    "angry_beaver": VendorPreset(
        vendor_id="angry_beaver",
        display_name="Angry Beaver",
        happy_hare_vendor="AngryBeaver",
        happy_hare_type="ANGRY_BEAVER_1_0",
        topology_type=TOPOLOGY_GEAR_PER_GATE,
        selector_type="virtual",
        gates_per_unit=4,
        default_board="btt_mmb_can_v2_0",
        aliases=["angrybeaver", "beaver"],
        notes="Tipo-B; 4 gates por unidad.",
    ),
    "3ms": VendorPreset(
        vendor_id="3ms",
        display_name="3MS",
        happy_hare_vendor="3MS",
        happy_hare_type="3MS_1_0",
        topology_type=TOPOLOGY_GEAR_PER_GATE,
        selector_type="virtual",
        gates_per_unit=4,
        default_board="generic_gear_per_gate",
        capabilities={"led": False},
        aliases=["three_ms"],
        notes="Diseno abierto tipo-B; 4 gates por unidad.",
    ),
    "quattrobox": VendorPreset(
        vendor_id="quattrobox",
        display_name="QuattroBox",
        happy_hare_vendor="QuattroBox",
        happy_hare_type="QUATTRO_BOX_1_0",
        topology_type=TOPOLOGY_GEAR_PER_GATE,
        selector_type="virtual",
        gates_per_unit=4,
        default_board="generic_gear_per_gate",
        capabilities={"encoder": True, "led": True},
        aliases=["quattro_box"],
        notes="Tipo-B; 4 gates por unidad.",
    ),
    "quattrobox_v2": VendorPreset(
        vendor_id="quattrobox_v2",
        display_name="QuattroBox V2",
        happy_hare_vendor="QuattroBox",
        happy_hare_type="QUATTRO_BOX_2_0",
        topology_type=TOPOLOGY_GEAR_PER_GATE,
        selector_type="virtual",
        gates_per_unit=4,
        default_board="generic_gear_per_gate",
        capabilities={"encoder": True, "led": True},
        aliases=["quattro_box_v2", "quattrobox_2"],
        notes="Revision V2 del QuattroBox; 4 gates por unidad.",
    ),
    "chameleon": VendorPreset(
        vendor_id="chameleon",
        display_name="3D Chameleon",
        happy_hare_vendor="3DChameleon",
        happy_hare_type="3D_CHAMELEON_1_0",
        topology_type=TOPOLOGY_SELECTOR,
        selector_type="rotary",
        gates_per_unit=4,
        default_board="generic_selector",
        capabilities={"servo": False, "encoder": False, "led": False},
        aliases=["3d_chameleon", "3dchameleon"],
        notes="Selector rotativo; selecciona gates opuestos. 4 gates por unidad.",
    ),
    "pico_mmu": VendorPreset(
        vendor_id="pico_mmu",
        display_name="PicoMMU",
        happy_hare_vendor="PicoMMU",
        happy_hare_type="PICO_MMU_1_0",
        topology_type=TOPOLOGY_SELECTOR,
        selector_type="linear",
        gates_per_unit=4,
        default_board="generic_selector",
        capabilities={"servo": True, "encoder": False},
        aliases=["pico", "picommu"],
        notes="Selector por servo; requiere calibrar angulos. 4 gates por unidad.",
    ),
    "kms": VendorPreset(
        vendor_id="kms",
        display_name="KMS",
        happy_hare_vendor="KMS",
        happy_hare_type="KMS_1_0",
        topology_type=TOPOLOGY_GEAR_PER_GATE,
        selector_type="virtual",
        gates_per_unit=4,
        default_board="generic_gear_per_gate",
        capabilities={"espooler": True, "encoder": True, "sync_feedback": True, "led": True},
        aliases=["biqu_kms"],
        notes="Tipo-B con buffer sync-feedback opcional; 4 gates por unidad.",
    ),
    "mmx": VendorPreset(
        vendor_id="mmx",
        display_name="MMX",
        happy_hare_vendor="MMX",
        happy_hare_type="MMX_1_0",
        topology_type=TOPOLOGY_SELECTOR,
        selector_type="linear",
        gates_per_unit=4,
        default_board="generic_selector",
        capabilities={"servo": True, "encoder": False, "led": True},
        aliases=["mmx_1_0"],
        notes="Selector por servo; 4 gates por unidad.",
    ),
    "qidi_box": VendorPreset(
        vendor_id="qidi_box",
        display_name="QIDI Box",
        happy_hare_vendor="QIDI",
        happy_hare_type="QIDI_BOX_1_0",
        topology_type=TOPOLOGY_GEAR_PER_GATE,
        selector_type="virtual",
        gates_per_unit=4,
        default_board="generic_gear_per_gate",
        capabilities={"sync_feedback": True, "toolhead_sensor": True, "encoder": False},
        aliases=["qidi"],
        notes="Tipo-B con sensor de tension; 4 gates por unidad.",
    ),
}

_ALIAS_INDEX: Dict[str, str] = {}
for _vendor_id, _preset in VENDORS.items():
    _ALIAS_INDEX[_vendor_id] = _vendor_id
    for _alias in _preset.aliases:
        _ALIAS_INDEX[_alias.lower()] = _vendor_id


class VendorError(Exception):
    """Vendor desconocido o configuracion invalida."""


def list_vendors() -> List[VendorPreset]:
    """Devuelve los presets ordenados por identificador."""
    return [VENDORS[key] for key in sorted(VENDORS)]


def get_vendor(vendor_id: str) -> VendorPreset:
    """Resuelve un vendor por id o alias (case-insensitive)."""
    key = _ALIAS_INDEX.get(str(vendor_id).strip().lower())
    if key is None:
        raise VendorError(f"vendor desconocido: {vendor_id!r}")
    return VENDORS[key]


def chained_gates(vendor: VendorPreset, units: int = 1) -> int:
    """Gates totales al encadenar ``units`` unidades iguales (contiguos)."""
    return max(1, int(units)) * int(vendor.gates_per_unit)


def gate_offsets(vendor: VendorPreset, units: int = 1) -> List[int]:
    """Offset global del primer gate de cada unidad."""
    return [index * vendor.gates_per_unit for index in range(max(1, int(units)))]


def vendor_to_profile(
    vendor_id: str,
    units: int = 1,
    board: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> MMUProfile:
    """Construye un perfil Dog Matrix completo a partir de un preset.

    ``units`` encadena N unidades iguales (numeracion contigua de gates).
    ``board`` permite forzar la placa; si no, se usa ``default_board``.
    """
    vendor = get_vendor(vendor_id)
    units = max(1, int(units))
    resolved_board = board or vendor.default_board
    raw: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "profile_id": f"dog_matrix.{vendor.vendor_id}.v1",
        "display_name": vendor.display_name if units == 1 else f"{vendor.display_name} x{units}",
        "topology": {
            "type": vendor.topology_type,
            "gates": chained_gates(vendor, units),
            "units": units,
        },
        "capabilities": vendor.resolved_capabilities(),
        "limits": vendor.resolved_limits(),
        "hardware": {
            "board": resolved_board,
            "coils_per_unit": vendor.gates_per_unit
            if vendor.topology_type == TOPOLOGY_GEAR_PER_GATE
            else 1,
            "mcu": [
                {
                    "name": "mmu" if units == 1 else f"mmu{index}",
                    "transport": "usb",
                    "serial_by_id": f"/dev/serial/by-id/usb-DogMatrix_MMU-{index}-if00",
                }
                for index in range(units)
            ],
        },
    }
    if vendor.topology_type == TOPOLOGY_SELECTOR:
        raw["topology"]["selector_type"] = vendor.selector_type
    if overrides:
        _deep_update(raw, overrides)
    return MMUProfile(
        schema_version=int(raw["schema_version"]),
        profile_id=str(raw["profile_id"]),
        display_name=str(raw["display_name"]),
        topology=dict(raw["topology"]),
        capabilities=dict(raw["capabilities"]),
        limits=dict(raw["limits"]),
        hardware=dict(raw["hardware"]),
        raw=raw,
    )


def _deep_update(target: Dict[str, Any], updates: Dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def build_machine_layout(vendor_id: str, units: int = 1) -> Dict[str, Any]:
    """Layout multi-unidad: unidades, gates contiguos y offsets."""
    vendor = get_vendor(vendor_id)
    units = max(1, int(units))
    offsets = gate_offsets(vendor, units)
    return {
        "vendor": vendor.vendor_id,
        "display_name": vendor.display_name,
        "units": units,
        "gates_per_unit": vendor.gates_per_unit,
        "total_gates": chained_gates(vendor, units),
        "layout": [
            {
                "unit": index,
                "name": f"unit{index}",
                "gate_offset": offsets[index],
                "gates": vendor.gates_per_unit,
            }
            for index in range(units)
        ],
    }


__all__ = [
    "SCHEMA_VERSION",
    "TOPOLOGY_GEAR_PER_GATE",
    "TOPOLOGY_SELECTOR",
    "DEFAULT_LIMITS",
    "VendorPreset",
    "VendorError",
    "VENDORS",
    "list_vendors",
    "get_vendor",
    "chained_gates",
    "gate_offsets",
    "vendor_to_profile",
    "build_machine_layout",
]
