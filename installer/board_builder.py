"""Constructor guiado de definiciones de placa para Dog Matrix MMU.

Objetivo: que **cualquier usuario, sin conocimientos tecnicos**, pueda anadir una
placa base nueva o una unidad MMU de forma intuitiva y obtener un YAML valido.

- Formato **YAML**: legible, admite comentarios y es el ya usado en el proyecto
  (JSON se mantiene como alternativa legible por maquina). Ver
  ``docs/CONFIGURATION_FORMAT.md`` para la justificacion.
- **Interactivo** (paso a paso) y **headless** (``--from-json``).
- **Validacion** por JSON Schema + reglas semanticas (pines duplicados, etc.).
- Escritura determinista (misma entrada -> mismo fichero).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import project_root

SCHEMA_VERSION = 1
_BOARD_ID_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
_VALID_TOPOLOGIES = ("selector", "gear_per_gate", "both")

#: Preguntas del asistente interactivo (id, prompt, por defecto).
DRIVER_ROLES = ("step", "dir", "enable", "uart", "diag")
PIN_ROLES = (
    ("selector_endstop", "Endstop del selector (vacio = omitir)"),
    ("encoder", "Pin del encoder (vacio = omitir)"),
    ("gate_sensor", "Sensor de gate (vacio = omitir)"),
    ("servo", "Servo del selector (vacio = omitir)"),
    ("cut_servo", "Servo de corte (vacio = omitir)"),
    ("neopixel", "Neopixel/RGB (vacio = omitir)"),
    ("i2c_scl", "I2C SCL (vacio = omitir)"),
    ("i2c_sda", "I2C SDA (vacio = omitir)"),
)


class BoardBuilderError(Exception):
    """Documento de placa invalido."""


# ---------------------------------------------------------------------------
# Serializacion YAML determinista (sin dependencia de PyYAML)
# ---------------------------------------------------------------------------
def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    needs_quote = (
        text == ""
        or text.strip() != text
        or any(char in text for char in ":#,[]{}&*!|>'\"%@`")
        or text.lower() in ("true", "false", "null", "yes", "no", "on", "off", "none")
    )
    if needs_quote:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def dump_yaml(data: Any, indent: int = 0) -> str:
    """Serializa dict/list/escalares a YAML determinista."""
    pad = " " * indent
    lines: List[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict) and value:
                lines.append(f"{pad}{key}:")
                lines.append(dump_yaml(value, indent + 2))
            elif isinstance(value, list) and value:
                lines.append(f"{pad}{key}:")
                lines.append(dump_yaml(value, indent + 2))
            elif isinstance(value, dict):
                lines.append(f"{pad}{key}: {{}}")
            elif isinstance(value, list):
                lines.append(f"{pad}{key}: []")
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(value)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item:
                rendered = dump_yaml(item, indent + 2).splitlines()
                lines.append(f"{pad}- {rendered[0].lstrip()}")
                lines.extend(rendered[1:])
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Modelo de respuestas
# ---------------------------------------------------------------------------
@dataclass
class BoardAnswers:
    board_id: str
    display_name: str
    topology: str = "selector"
    mcu_name: str = "mmu"
    mcu_transport: str = "usb"
    mcu_arch: str = ""
    max_gates: int = 12
    coils_per_unit: int = 1
    drivers: List[Dict[str, Any]] = field(default_factory=list)
    pins: Dict[str, Any] = field(default_factory=dict)
    pre_gate: List[str] = field(default_factory=list)
    dual_gate_sensors: bool = False
    i2c_bus: str = ""
    i2c_devices: List[str] = field(default_factory=list)
    capabilities: Dict[str, bool] = field(default_factory=dict)
    source_url: str = ""


def answers_from_dict(data: Dict[str, Any]) -> BoardAnswers:
    """Construye respuestas desde un dict (headless / --from-json)."""
    if not isinstance(data, dict):
        raise BoardBuilderError("las respuestas deben ser un mapa")
    drivers = data.get("drivers") or []
    if not isinstance(drivers, list):
        raise BoardBuilderError("drivers debe ser una lista")
    pins = data.get("pins") or {}
    if not isinstance(pins, dict):
        raise BoardBuilderError("pins debe ser un mapa")
    return BoardAnswers(
        board_id=str(data.get("board_id", "")).strip(),
        display_name=str(data.get("display_name", "")).strip(),
        topology=str(data.get("topology", "selector")),
        mcu_name=str((data.get("mcu") or {}).get("name", "mmu")),
        mcu_transport=str((data.get("mcu") or {}).get("transport", "usb")),
        mcu_arch=str((data.get("mcu") or {}).get("arch", "")),
        max_gates=int(data.get("max_gates", 12)),
        coils_per_unit=int(data.get("coils_per_unit", 1)),
        drivers=[dict(driver) for driver in drivers if isinstance(driver, dict)],
        pins=dict(pins),
        pre_gate=list(data.get("pre_gate") or []),
        dual_gate_sensors=bool(data.get("dual_gate_sensors", False)),
        i2c_bus=str(data.get("i2c_bus", "")),
        i2c_devices=list((data.get("i2c") or {}).get("devices", []) if isinstance(data.get("i2c"), dict) else []),
        capabilities={str(k): bool(v) for k, v in (data.get("capabilities") or {}).items()},
        source_url=str(data.get("source_url", "")),
    )


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------
class BoardBuilder:
    """Genera y valida un documento de placa (``config/boards/<id>.yaml``)."""

    def __init__(self, answers: BoardAnswers) -> None:
        self.answers = answers

    # -- Documento ----------------------------------------------------------
    def build(self) -> Dict[str, Any]:
        answers = self.answers
        document: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "board_id": answers.board_id,
            "display_name": answers.display_name,
            "happy_hare_board_type": "OTHER",
            "topology": answers.topology,
            "coils_per_unit": max(1, answers.coils_per_unit),
            "max_gates": max(1, answers.max_gates),
        }
        if answers.dual_gate_sensors:
            document["dual_gate_sensors"] = True
        document["mcu"] = {
            "name": answers.mcu_name,
            "transport": answers.mcu_transport,
            "arch": answers.mcu_arch,
        }
        if answers.i2c_bus:
            document["i2c_bus"] = answers.i2c_bus
        if answers.source_url:
            document["source_url"] = answers.source_url

        pins: Dict[str, Any] = {}
        for role, value in answers.pins.items():
            if value in ("", None):
                continue
            pins[role] = value
        if answers.pre_gate:
            pins["pre_gate"] = list(answers.pre_gate)
        # Orden estable para determinismo.
        document["drivers"] = [dict(driver) for driver in answers.drivers]
        document["pins"] = {key: pins[key] for key in sorted(pins)}

        if answers.i2c_bus or answers.i2c_devices:
            document["i2c"] = {"bus": answers.i2c_bus}
            for role, value in answers.pins.items():
                if role in ("i2c_scl", "i2c_sda") and value:
                    document["i2c"][role.split("_")[1]] = value
            if answers.i2c_devices:
                document["i2c"]["devices"] = list(answers.i2c_devices)

        capabilities = dict(answers.capabilities)
        if not capabilities:
            capabilities = {
                "selector": answers.topology in ("selector", "both"),
                "gear_per_gate": answers.topology in ("gear_per_gate", "both"),
                "encoder": "encoder" in pins,
                "gate_sensors": bool(answers.pre_gate) or "gate_sensor" in pins,
                "servo": "servo" in pins,
                "cutter": "cut_servo" in pins,
                "led": "neopixel" in pins,
                "canbus": answers.mcu_transport == "can",
                "i2c": bool(answers.i2c_bus),
            }
        document["capabilities"] = {key: capabilities[key] for key in sorted(capabilities)}
        return document

    def validate(self) -> List[str]:
        return validate_board_document(self.build())

    def to_yaml(self) -> str:
        header = (
            "# Dog Matrix MMU - Definicion de placa (generada por el asistente).\n"
            f"# Placa: {self.answers.display_name}\n"
            f"# Topologia: {self.answers.topology}\n"
        )
        return header + dump_yaml(self.build()) + "\n"

    def write(self, directory: Optional[str] = None) -> Path:
        errors = self.validate()
        if errors:
            raise BoardBuilderError("; ".join(errors))
        target_dir = Path(directory).expanduser() if directory else _boards_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{self.answers.board_id}.yaml"
        if path.exists():
            raise BoardBuilderError(f"la placa ya existe: {path}")
        path.write_text(self.to_yaml(), encoding="utf-8", newline="\n")
        return path


def _boards_dir() -> Path:
    return project_root() / "config" / "boards"


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------
def validate_board_document(document: Dict[str, Any]) -> List[str]:
    """Valida un documento de placa (JSON Schema + reglas semanticas)."""
    errors: List[str] = []
    board_id = str(document.get("board_id", ""))
    if not _BOARD_ID_RE.match(board_id):
        errors.append(f"board_id invalido (usar minusculas y guion bajo): {board_id!r}")
    if not str(document.get("display_name", "")).strip():
        errors.append("display_name es obligatorio")
    if int(document.get("schema_version", 0)) != SCHEMA_VERSION:
        errors.append(f"schema_version debe ser {SCHEMA_VERSION}")
    if document.get("topology") not in _VALID_TOPOLOGIES:
        errors.append(f"topology invalido: {document.get('topology')!r}")
    if int(document.get("coils_per_unit", 1)) < 1:
        errors.append("coils_per_unit debe ser >= 1")

    errors.extend(_schema_errors(document))
    errors.extend(_conflict_errors(document))
    return errors


def _boards_module() -> Any:
    extras = project_root() / "klippy" / "extras"
    if str(extras) not in sys.path:
        sys.path.insert(0, str(extras))
    from dog_matrix import boards  # type: ignore

    return boards


def _conflict_errors(document: Dict[str, Any]) -> List[str]:
    """Detecta pines duplicados respetando ``shared_pins`` (via dog_matrix.boards)."""
    if not document.get("drivers") and not document.get("pins"):
        return []  # arquetipos genericos sin pinout (se completan luego)
    try:
        boards = _boards_module()
        board = boards._board_from_document(document, str(document.get("board_id", "board")))
        return [
            f"pin duplicado {conflict.pin}: {', '.join(conflict.aliases)}"
            for conflict in boards.validate_board_definition(board)
        ]
    except Exception:  # noqa: BLE001 - validacion defensiva
        return []


def _schema_errors(document: Dict[str, Any]) -> List[str]:
    schema_path = _boards_dir() / "schema.json"
    if not schema_path.exists():
        return []
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft7Validator(schema)
        return [
            f"schema: {'/'.join(map(str, err.path))}: {err.message}"
            for err in sorted(validator.iter_errors(document), key=lambda e: list(e.path))
        ]
    except (OSError, ValueError):  # pragma: no cover - defensivo
        return []


# ---------------------------------------------------------------------------
# Asistente interactivo
# ---------------------------------------------------------------------------
def collect_answers(
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> BoardAnswers:
    """Recoge las respuestas paso a paso (usuario sin conocimientos tecnicos)."""
    output_fn("== Asistente para anadir una placa base ==")
    board_id = input_fn("Identificador (minusculas, guion bajo; p.ej. mi_placa): ").strip()
    display_name = input_fn("Nombre visible: ").strip()
    topology = input_fn(f"Topologia {_VALID_TOPOLOGIES} [selector]: ").strip() or "selector"

    mcu_name = input_fn("Nombre del MCU [mmu]: ").strip() or "mmu"
    transport = input_fn("Conexion (usb/can) [usb]: ").strip() or "usb"
    arch = input_fn("Arquitectura (p.ej. rp2040, stm32g0b1) [vacío]: ").strip()

    pins: Dict[str, Any] = {}
    for role, prompt in PIN_ROLES:
        value = input_fn(f"{prompt}: ").strip()
        if value:
            pins[role] = value

    n_drivers = int(input_fn("Numero de drivers [2]: ").strip() or "2")
    drivers: List[Dict[str, Any]] = []
    for index in range(max(0, n_drivers)):
        output_fn(f"  Driver {index}:")
        driver: Dict[str, Any] = {}
        for role in DRIVER_ROLES:
            value = input_fn(f"    {role}: ").strip()
            if value:
                driver[role] = value
        drivers.append(driver)

    answers = BoardAnswers(
        board_id=board_id,
        display_name=display_name,
        topology=topology,
        mcu_name=mcu_name,
        mcu_transport=transport,
        mcu_arch=arch,
        drivers=drivers,
        pins=pins,
        i2c_bus=str(pins.get("i2c_bus", "")),
    )
    return answers


__all__ = [
    "SCHEMA_VERSION",
    "DRIVER_ROLES",
    "PIN_ROLES",
    "BoardBuilderError",
    "BoardAnswers",
    "BoardBuilder",
    "answers_from_dict",
    "validate_board_document",
    "collect_answers",
    "dump_yaml",
]
