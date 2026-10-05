"""Motor de configuracion tipo menuconfig (Kconfig) para Dog Matrix MMU.

Reproduce la experiencia del ``menuconfig`` de Happy Hare v4 (elegir MMU,
placa, conexion, sensores, extras) pero:

- Sin DSL propietario ni arboles de >130 ficheros: el menu es una estructura
  declarativa Python, inspeccionable y testeable.
- Con **derivacion de capacidades** (los vendors fijan topologia/selector y el
  resto se deduce) y **visibilidad condicional** (``depends_on``).
- **Headless** (respuestas por CLI) e **interactivo** (texto), con
  **búsqueda** de opciones.
- Salida determinista y validable contra ``profiles/schema.json``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Modelo declarativo
# ---------------------------------------------------------------------------
KIND_BOOL = "bool"
KIND_INT = "int"
KIND_STRING = "string"
KIND_CHOICE = "choice"


@dataclass
class Option:
    """Una opcion/pregunta del menu (equivalente a un simbolo Kconfig)."""

    key: str
    prompt: str
    kind: str = KIND_BOOL
    default: Any = None
    choices: List[str] = field(default_factory=list)
    depends_on: Optional[str] = None
    help: str = ""

    def is_visible(self, answers: Dict[str, Any]) -> bool:
        if self.depends_on is None:
            return True
        return bool(answers.get(self.depends_on))


@dataclass
class Menu:
    """Agrupacion de opciones (equivalente a un bloque ``menu`` de Kconfig)."""

    key: str
    title: str
    options: List[Option] = field(default_factory=list)


class Configurator:
    """Arbol de menus + resolucion de respuestas."""

    def __init__(self, menus: List[Menu]) -> None:
        self.menus = menus

    # -- Consultas ----------------------------------------------------------
    def all_options(self) -> List[Option]:
        return [option for menu in self.menus for option in menu.options]

    def defaults(self) -> Dict[str, Any]:
        return {option.key: option.default for option in self.all_options()}

    def visible_options(self, answers: Dict[str, Any]) -> List[Tuple[Menu, Option]]:
        result: List[Tuple[Menu, Option]] = []
        for menu in self.menus:
            for option in menu.options:
                if option.is_visible(answers):
                    result.append((menu, option))
        return result

    def search(self, term: str) -> List[Option]:
        """Busca opciones por clave o texto de ayuda (feature tipo grepconfig)."""
        needle = str(term).lower()
        return [
            option
            for option in self.all_options()
            if needle in option.key.lower() or needle in option.prompt.lower() or needle in option.help.lower()
        ]

    # -- Resolucion ---------------------------------------------------------
    def resolve(self, answers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Combina defaults + respuestas y elimina opciones no visibles."""
        merged = self.defaults()
        merged.update(answers or {})
        resolved: Dict[str, Any] = {}
        for option in self.all_options():
            if not option.is_visible(merged):
                continue
            value = merged.get(option.key, option.default)
            resolved[option.key] = _coerce(option, value)
        return resolved

    # -- Interactivo --------------------------------------------------------
    def run(
        self,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> Dict[str, Any]:
        """menuconfig de texto: recorre los menus y recoge respuestas."""
        answers = self.defaults()
        output_fn("== Dog Matrix MMU — configuracion (menuconfig) ==")
        output_fn("Escriba el numero de opcion, o 'd' para aceptar el valor por defecto.")
        for menu in self.menus:
            output_fn(f"\n[{menu.title}]")
            for index, option in enumerate(menu.options, start=1):
                if not option.is_visible(answers):
                    continue
                current = answers.get(option.key, option.default)
                prompt = f"  {index}. {option.prompt} [{current}]"
                if option.kind == KIND_CHOICE:
                    prompt += f" (opciones: {', '.join(option.choices)})"
                try:
                    raw = input_fn(prompt + ": ").strip()
                except EOFError:
                    raw = ""
                if raw in ("", "d", "D"):
                    continue
                answers[option.key] = _coerce(option, raw)
        return self.resolve(answers)


def _coerce(option: Option, value: Any) -> Any:
    if value is None:
        return option.default
    try:
        if option.kind == KIND_INT:
            return int(value)
        if option.kind == KIND_BOOL:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "si", "s")
    except (TypeError, ValueError):
        return option.default
    return value


# ---------------------------------------------------------------------------
# Definicion del arbol de menus de Dog Matrix
# ---------------------------------------------------------------------------
def _dog_matrix_module() -> Any:
    from pathlib import Path
    import sys as _sys

    extras = Path(__file__).resolve().parent.parent / "klippy" / "extras"
    if str(extras) not in _sys.path:
        _sys.path.insert(0, str(extras))
    from dog_matrix import boards, vendors  # type: ignore

    return boards, vendors


def build_menus() -> List[Menu]:
    """Construye el arbol de menus a partir del catalogo de vendors y placas."""
    boards, vendors = _dog_matrix_module()
    vendor_ids = sorted(vendors.VENDORS)
    board_ids = boards.list_boards()
    return [
        Menu(
            "mmu_type",
            "MMU Type (fabricante)",
            [
                Option(
                    "vendor",
                    "Fabricante / modelo de MMU",
                    KIND_CHOICE,
                    default="box_turtle",
                    choices=vendor_ids,
                    help="Presets: BTT ViViD, Box Turtle, ERCF, EMU, Tradrack, Night Owl, "
                    "Angry Beaver, 3MS, QuattroBox, 3D Chameleon, PicoMMU, KMS, MMX, QIDI Box.",
                )
            ],
        ),
        Menu(
            "machine",
            "Machine (conexion y multi-unidad)",
            [
                Option("units", "Numero de unidades encadenadas", KIND_INT, default=1, help="Multiplica los gates."),
                Option(
                    "connection",
                    "Conexion del MCU",
                    KIND_CHOICE,
                    default="usb",
                    choices=["usb", "can"],
                ),
            ],
        ),
        Menu(
            "board",
            "Board type (placa controladora)",
            [
                Option(
                    "board",
                    "Placa (vacio = placa por defecto del fabricante)",
                    KIND_CHOICE,
                    default="",
                    choices=["", *board_ids],
                )
            ],
        ),
        Menu(
            "sensors",
            "Sensors & feedback",
            [
                Option("encoder", "Encoder de movimiento", KIND_BOOL, default=True),
                Option("gate_sensors", "Sensores por gate", KIND_BOOL, default=True),
                Option(
                    "dual_gate_sensors",
                    "Reutilizar pre-gate como post-gate (carga hasta post-gate)",
                    KIND_BOOL,
                    default=False,
                ),
                Option("toolhead_sensor", "Sensor de toolhead", KIND_BOOL, default=True),
                Option("sync_feedback", "Buffer sync-feedback", KIND_BOOL, default=False),
            ],
        ),
        Menu(
            "extras",
            "Extras",
            [
                Option("led", "Neopixel / LEDs", KIND_BOOL, default=True),
                Option("espooler", "eSpooler (buffer activo)", KIND_BOOL, default=False),
                Option("nfc", "Lector NFC/RFID", KIND_BOOL, default=False),
                Option("i2c", "Perifericos I2C (humedad/T, mux, RFID)", KIND_BOOL, default=False),
                Option("cutter", "Cortador (servo)", KIND_BOOL, default=False),
                Option("spoolman", "Integracion Spoolman", KIND_BOOL, default=True),
                Option("endless_spool", "EndlessSpool", KIND_BOOL, default=True),
            ],
        ),
        Menu(
            "calibration",
            "Limites de calibracion",
            [
                Option("bowden_length_mm", "Longitud de bowden (mm)", KIND_INT, default=600),
                Option("max_load_speed_mm_s", "Velocidad de carga (mm/s)", KIND_INT, default=80),
                Option("max_unload_speed_mm_s", "Velocidad de descarga (mm/s)", KIND_INT, default=100),
            ],
        ),
    ]


@dataclass
class ResolvedConfiguration:
    """Configuracion resuelta lista para generar el perfil y el hardware_map."""

    answers: Dict[str, Any]
    vendor: str
    board: str
    units: int
    gates: int
    machine: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "vendor": self.vendor,
            "board": self.board,
            "units": self.units,
            "gates": self.gates,
            "machine": self.machine,
            "answers": dict(self.answers),
        }


def resolve_configuration(answers: Dict[str, Any]) -> ResolvedConfiguration:
    """Resuelve respuestas -> configuracion concreta (vendor/placa/gates)."""
    _, vendors = _dog_matrix_module()
    configurator = Configurator(build_menus())
    resolved = configurator.resolve(answers)
    vendor = vendors.get_vendor(str(resolved.get("vendor", "box_turtle")))
    board = str(resolved.get("board") or vendor.default_board)
    units = max(1, int(resolved.get("units", 1)))
    return ResolvedConfiguration(
        answers=resolved,
        vendor=vendor.vendor_id,
        board=board,
        units=units,
        gates=vendors.chained_gates(vendor, units),
        machine=vendors.build_machine_layout(vendor.vendor_id, units),
    )


def headless_configuration(
    vendor_id: str,
    units: int = 1,
    board: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> ResolvedConfiguration:
    """Configuracion no interactiva (CLI) a partir de un vendor."""
    answers: Dict[str, Any] = {"vendor": vendor_id, "units": units}
    if board:
        answers["board"] = board
    answers.update(overrides or {})
    return resolve_configuration(answers)


def configuration_profile(config: ResolvedConfiguration) -> Any:
    """Genera el perfil ``MMUProfile`` a partir de la configuracion resuelta."""
    _, vendors = _dog_matrix_module()
    answers = config.answers
    overrides: Dict[str, Any] = {
        "hardware": {"board": config.board},
        "capabilities": {
            key: answers[key]
            for key in ("encoder", "gate_sensors", "toolhead_sensor", "sync_feedback", "led", "espooler", "nfc", "cutter", "spoolman", "endless_spool")
            if key in answers
        },
        "limits": {
            key: answers[key]
            for key in ("bowden_length_mm", "max_load_speed_mm_s", "max_unload_speed_mm_s")
            if key in answers
        },
    }
    return vendors.vendor_to_profile(config.vendor, units=config.units, board=config.board, overrides=overrides)


def format_vendors() -> str:
    """Tabla de vendors para ``--list-vendors``."""
    _, vendors = _dog_matrix_module()
    lines = ["Fabricante                          | id            | gates/u | topologia   | placa por defecto"]
    lines.append("-" * 108)
    for preset in vendors.list_vendors():
        lines.append(
            f"{preset.display_name:<35} | {preset.vendor_id:<13} | "
            f"{preset.gates_per_unit:>7} | {preset.topology_type:<11} | {preset.default_board}"
        )
    return "\n".join(lines)


__all__ = [
    "Option",
    "Menu",
    "Configurator",
    "ResolvedConfiguration",
    "build_menus",
    "resolve_configuration",
    "headless_configuration",
    "configuration_profile",
    "format_vendors",
    "KIND_BOOL",
    "KIND_INT",
    "KIND_STRING",
    "KIND_CHOICE",
]
