"""Carga, validacion y exposicion de perfiles de hardware (capacidades).

Un perfil es un documento YAML/JSON versionado (``schema_version``) que declara
la topologia, las capacidades y los limites fisicos de un MMU. Este modulo es
autocontenido (no depende de Klipper) para poder reutilizarse desde el
instalador.

Requisitos (informe 6.3): carga < 100 ms, validacion < 50 ms.

Para el runtime de Klipper (que no incluye PyYAML) se implementa un parser
minimo del subconjunto YAML usado por los perfiles; si PyYAML esta disponible
se prefiere, y el formato JSON siempre es soportado via stdlib.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

SUPPORTED_SCHEMA_VERSION = 1
PROFILES_DIRNAME = "profiles"
SCHEMA_FILENAME = "schema.json"


class ProfileError(Exception):
    """Perfil ausente, ilegible o invalido."""


# --- Parser YAML minimo (subconjunto) --------------------------------------
def _strip_comment(line: str) -> str:
    in_single = in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def _parse_scalar(text: str) -> Any:
    token = text.strip()
    if token == "":
        return None
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    low = token.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", "none"):
        return None
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _mini_yaml_load(text: str) -> Dict[str, Any]:
    """Parser del subconjunto YAML usado por los perfiles."""
    lines: List[tuple] = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))
    if not lines:
        return {}

    pos = 0

    def parse_block(indent: int) -> Any:
        nonlocal pos
        if pos >= len(lines):
            return None
        if lines[pos][1].startswith("- "):
            return parse_list(indent)
        return parse_map(indent)

    def parse_map(indent: int) -> Dict[str, Any]:
        nonlocal pos
        result: Dict[str, Any] = {}
        while pos < len(lines):
            cur_indent, content = lines[pos]
            if cur_indent < indent or content.startswith("- "):
                break
            if cur_indent > indent:
                pos += 1
                continue
            key, _, value = content.partition(":")
            key = key.strip()
            value = value.strip()
            pos += 1
            if value == "":
                if pos < len(lines) and lines[pos][0] > indent:
                    result[key] = parse_block(lines[pos][0])
                else:
                    result[key] = None
            else:
                result[key] = _parse_scalar(value)
        return result

    def parse_list(indent: int) -> List[Any]:
        nonlocal pos
        result: List[Any] = []
        while pos < len(lines):
            cur_indent, content = lines[pos]
            if cur_indent < indent or not content.startswith("- "):
                break
            item = content[2:].strip()
            pos += 1
            if ":" in item and item[0] not in "\"'":
                key, _, value = item.partition(":")
                entry: Dict[str, Any] = {key.strip(): _parse_scalar(value)}
                while (
                    pos < len(lines)
                    and lines[pos][0] > indent
                    and not lines[pos][1].startswith("- ")
                ):
                    k2, _, v2 = lines[pos][1].partition(":")
                    entry[k2.strip()] = _parse_scalar(v2)
                    pos += 1
                result.append(entry)
            else:
                result.append(_parse_scalar(item))
        return result

    return parse_block(lines[0][0])


def _load_document(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:  # Preferir PyYAML cuando este disponible (instalador).
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        return _mini_yaml_load(text)


# --- Modelo ----------------------------------------------------------------
@dataclass
class MMUProfile:
    """Perfil de hardware ya cargado y normalizado."""

    schema_version: int
    profile_id: str
    display_name: str
    topology: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    limits: Dict[str, Any] = field(default_factory=dict)
    hardware: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def gates(self) -> int:
        return int(self.topology.get("gates", 0))

    @property
    def topology_type(self) -> str:
        return str(self.topology.get("type", "gear_per_gate"))

    @property
    def selector_type(self) -> str:
        return str(self.topology.get("selector_type", "virtual"))

    def has_capability(self, name: str) -> bool:
        return bool(self.capabilities.get(name, False))


class Capabilities:
    """Acceso tipado al perfil de hardware activo."""

    def __init__(self, profile_path: str) -> None:
        self.profile_path = Path(profile_path).expanduser()
        self.profile: Optional[MMUProfile] = None
        self._raw: Dict[str, Any] = {}
        self.errors: List[str] = []

    def load(self) -> MMUProfile:
        """Carga y valida el perfil. Lanza ``ProfileError`` si es invalido."""
        if not self.profile_path.exists():
            raise ProfileError(f"perfil no encontrado: {self.profile_path}")
        try:
            document = _load_document(self.profile_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ProfileError(f"perfil ilegible: {exc}") from exc
        if not isinstance(document, dict):
            raise ProfileError("el perfil debe ser un mapa de nivel superior")
        self._raw = document
        self.errors = self._semantic_errors(document)
        if self.errors:
            raise ProfileError("; ".join(self.errors))
        self.profile = MMUProfile(
            schema_version=int(document.get("schema_version", 0)),
            profile_id=str(document.get("profile_id", self.profile_path.stem)),
            display_name=str(document.get("display_name", document.get("profile_id", ""))),
            topology=dict(document.get("topology", {})),
            capabilities=dict(document.get("capabilities", {})),
            limits=dict(document.get("limits", {})),
            hardware=dict(document.get("hardware", {})),
            raw=document,
        )
        return self.profile

    def validate(self) -> List[str]:
        """Valida contra el JSON Schema (si esta disponible) y reglas semanticas."""
        if not self._raw:
            try:
                document = _load_document(self.profile_path)
            except (OSError, ValueError) as exc:  # pragma: no cover - defensivo
                return [f"perfil ilegible: {exc}"]
            self._raw = document if isinstance(document, dict) else {}
        errors = list(self._semantic_errors(self._raw))
        errors.extend(self._schema_errors(self._raw))
        return errors

    def get(self, key: str, default: Any = None) -> Any:
        """Obtiene un valor por ruta punteada (p. ej. ``limits.max_distance_mm``)."""
        document: Any = self._raw
        for part in key.split("."):
            if isinstance(document, dict) and part in document:
                document = document[part]
            else:
                return default
        return document

    def has_capability(self, name: str) -> bool:
        if self.profile is not None:
            return self.profile.has_capability(name)
        return bool(self._raw.get("capabilities", {}).get(name, False))

    # -- Validacion ---------------------------------------------------------
    @staticmethod
    def _semantic_errors(document: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        if int(document.get("schema_version", 0)) != SUPPORTED_SCHEMA_VERSION:
            errors.append(
                f"schema_version no soportada: {document.get('schema_version')!r} "
                f"(esperada {SUPPORTED_SCHEMA_VERSION})"
            )
        topology = document.get("topology") or {}
        if not isinstance(topology, dict):
            errors.append("topology debe ser un mapa")
            topology = {}
        gates = topology.get("gates")
        if not isinstance(gates, int) or not 1 <= gates <= 64:
            errors.append(f"topology.gates fuera de rango: {gates!r}")
        topo_type = topology.get("type")
        valid_types = {"gear_per_gate", "selector", "modular", "hybrid", "virtual"}
        if topo_type not in valid_types:
            errors.append(f"topology.type invalido: {topo_type!r}")
        capabilities = document.get("capabilities") or {}
        if topo_type == "selector" and not capabilities.get("selector", False):
            errors.append("topology.type=selector requiere capabilities.selector=true")
        if topo_type == "selector" and topology.get("selector_type") not in (
            "linear",
            "rotary",
            "virtual",
            "servo",
            "indexed",
            "multi_gear",
            "macro",
            "linear_mg",
            "linear_servo",
            "linear_mg_servo",
        ):
            errors.append("topology.type=selector requiere selector_type valido")
        limits = document.get("limits") or {}
        for key in ("max_load_speed_mm_s", "max_unload_speed_mm_s", "max_distance_mm"):
            value = limits.get(key)
            if not isinstance(value, (int, float)) or value <= 0:
                errors.append(f"limits.{key} debe ser > 0 (actual: {value!r})")
        return errors

    @staticmethod
    def _schema_errors(document: Dict[str, Any]) -> List[str]:
        schema_path = _find_schema(Path(__file__).resolve().parent)
        if schema_path is None:
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
                for err in sorted(validator.iter_errors(document), key=lambda e: e.path)
            ]
        except (OSError, ValueError):  # pragma: no cover - defensivo
            return []


def _find_schema(start: Path) -> Optional[Path]:
    """Localiza ``profiles/schema.json`` subiendo por el arbol del proyecto."""
    for base in [start, *start.parents]:
        candidate = base / PROFILES_DIRNAME / SCHEMA_FILENAME
        if candidate.exists():
            return candidate
    root = Path(__file__).resolve().parents[3] / PROFILES_DIRNAME / SCHEMA_FILENAME
    return root if root.exists() else None


def find_profile(name_or_path: str, profiles_dir: Optional[str] = None) -> Path:
    """Resuelve un perfil por ruta directa o por nombre (``box_turtle``)."""
    candidate = Path(name_or_path).expanduser()
    if candidate.exists():
        return candidate
    base = Path(profiles_dir).expanduser() if profiles_dir else Path(
        __file__
    ).resolve().parents[3] / PROFILES_DIRNAME
    for suffix in (".yaml", ".yml", ".json"):
        resolved = base / f"{name_or_path}{suffix}"
        if resolved.exists():
            return resolved
    raise ProfileError(f"perfil no encontrado: {name_or_path} (buscado en {base})")


__all__ = [
    "Capabilities",
    "MMUProfile",
    "ProfileError",
    "find_profile",
    "SUPPORTED_SCHEMA_VERSION",
]
