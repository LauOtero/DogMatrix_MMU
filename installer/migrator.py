"""Migracion de esquemas y configuracion legacy (informe 6.17).

Detecta configuraciones previas (Happy Hare / AFC), convierte la calibracion
preservando las mediciones del usuario y produce un bundle Dog Matrix junto con
un informe de diferencias. Soporta ``--dry-run`` (previsualizacion).
"""

from __future__ import annotations

import ast
import configparser
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import project_root, sha256_text
from .backup import BackupManager
from .generator import ConfigBundle, ConfigGenerator

# Mensajes de migracion (seccion 6.17).
MIGRATE_START = "MIGRATE_START"
MIGRATE_BACKUP = "MIGRATE_BACKUP"
MIGRATE_SUCCESS = "MIGRATE_SUCCESS"
MIGRATE_ROLLBACK = "MIGRATE_ROLLBACK"
MIGRATE_SKIPPED = "MIGRATE_SKIPPED"

SUPPORTED_SCHEMA_VERSIONS = (1,)

# Claves conocidas de mmu_vars.cfg de Happy Hare -> campos Dog Matrix.
_KEY_MAP = {
    "num_gates": ("topology.gates", int),
    "gate_map": ("ttg_map", "literal"),
    "bowden": ("limits.bowden_length_mm", float),
    "toolhead": ("limits.toolhead_distance_mm", float),
    "encoder_resolution": ("limits.encoder_resolution", float),
    "purge": ("limits.purge_length_mm", float),
}


@dataclass
class LegacySystem:
    kind: str
    path: str
    files: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "files": self.files}


class ConfigMigrator:
    """Migra configuraciones legacy al esquema Dog Matrix v1."""

    def __init__(self, schema_registry: Any = None) -> None:
        self.schema_registry = schema_registry

    # -- Deteccion ----------------------------------------------------------
    def detect_legacy_configuration(self, config_dir: str) -> Optional[LegacySystem]:
        base = Path(config_dir).expanduser()
        if not base.exists():
            return None
        markers = {
            "happy_hare": ["mmu_vars.cfg", "mmu_parameters.cfg", "mmu_hardware.cfg"],
            "afc": ["AFC.cfg", "AFC_Macro_Vars.cfg"],
        }
        for kind, names in markers.items():
            found = [name for name in names if (base / name).exists()]
            if found:
                return LegacySystem(kind=kind, path=str(base), files=found)
        return None

    def create_migration_backup(self, config_dir: str) -> str:
        return BackupManager(config_dir).create(label="pre-migrate")

    # -- Migracion ----------------------------------------------------------
    def migrate_from_happy_hare(self, mmu_vars_path: str) -> ConfigBundle:
        source = Path(mmu_vars_path).expanduser()
        values = self._parse_saved_variables(source)
        migrated = self._map_values(values)
        profile = self._build_profile(migrated)

        generator = ConfigGenerator()
        bundle = generator.build_bundle(profile)
        diff = self._build_diff(migrated)
        bundle.files["dog_matrix_migration_diff.json"] = json.dumps(diff, indent=2, sort_keys=True)
        bundle.config_hash = sha256_text(
            "\n".join(bundle.files[name] for name in sorted(bundle.files))
        )
        return bundle

    def migrate_schema_version(self, config_dict: Dict[str, Any], from_v: int, to_v: int) -> Dict[str, Any]:
        if from_v not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"schema_version origen no soportada: {from_v}")
        if to_v not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"schema_version destino no soportada: {to_v}")
        if from_v == to_v:
            return dict(config_dict)
        # No hay migraciones entre versiones distintas registradas actualmente.
        result = dict(config_dict)
        result["schema_version"] = to_v
        result["migrated_from"] = from_v
        result["migrated_at"] = time.time()
        return result

    # -- Utilidades ---------------------------------------------------------
    @staticmethod
    def _parse_saved_variables(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(path)
        parser = configparser.ConfigParser()
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error:
            return {}
        if not parser.has_section("Variables"):
            return {}
        values: Dict[str, Any] = {}
        for key, raw in parser.items("Variables"):
            values[key] = ConfigMigrator._parse_value(raw)
        return values

    @staticmethod
    def _parse_value(raw: str) -> Any:
        token = raw.strip()
        try:
            return ast.literal_eval(token)
        except (ValueError, SyntaxError):
            return token

    @staticmethod
    def _map_values(values: Dict[str, Any]) -> Dict[str, Any]:
        migrated: Dict[str, Any] = {}
        for key, (target, kind) in _KEY_MAP.items():
            if key not in values:
                continue
            raw = values[key]
            try:
                if kind is int:
                    migrated[target] = int(raw)
                elif kind is float:
                    migrated[target] = float(raw)
                else:
                    migrated[target] = raw
            except (TypeError, ValueError):
                continue
        return migrated

    @staticmethod
    def _build_profile(migrated: Dict[str, Any]) -> Any:
        extras = project_root() / "klippy" / "extras"
        if str(extras) not in sys.path:
            sys.path.insert(0, str(extras))
        from dog_matrix.capabilities import MMUProfile  # type: ignore

        gates = int(migrated.get("topology.gates", 8))
        ttg = migrated.get("ttg_map")
        raw = {
            "schema_version": 1,
            "profile_id": "dog_matrix.migrated.v1",
            "display_name": "Migrated from Happy Hare",
            "topology": {"type": "gear_per_gate", "gates": gates, "units": 1},
            "capabilities": {
                "encoder": True,
                "gate_sensors": True,
                "toolhead_sensor": True,
                "endless_spool": True,
                "spoolman": True,
            },
            "limits": {
                "max_load_speed_mm_s": 80,
                "max_unload_speed_mm_s": 100,
                "max_distance_mm": 1500,
                "bowden_length_mm": float(migrated.get("limits.bowden_length_mm", 600)),
                "toolhead_distance_mm": float(migrated.get("limits.toolhead_distance_mm", 80)),
                "encoder_resolution": float(migrated.get("limits.encoder_resolution", 0.45)),
                "purge_length_mm": float(migrated.get("limits.purge_length_mm", 25)),
            },
            "hardware": {"mcu": [{"name": "mmu_main", "transport": "usb"}]},
        }
        return MMUProfile(
            schema_version=1,
            profile_id=raw["profile_id"],
            display_name=raw["display_name"],
            topology=raw["topology"],
            capabilities=raw["capabilities"],
            limits=raw["limits"],
            hardware=raw["hardware"],
            raw=raw,
        )

    @staticmethod
    def _build_diff(migrated: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "migrated_fields": migrated,
            "notes": "Se preservan longitudes de bowden y resolucion de encoder del sistema origen.",
        }


__all__ = [
    "ConfigMigrator",
    "LegacySystem",
    "MIGRATE_START",
    "MIGRATE_BACKUP",
    "MIGRATE_SUCCESS",
    "MIGRATE_ROLLBACK",
    "MIGRATE_SKIPPED",
]
