"""Generacion determinista de configuracion (informe 6.15).

Renderiza ``dog_matrix_generated.cfg``, ``dog_matrix_macros.cfg``, un snapshot
JSON del perfil y un fragmento de ``moonraker.conf``. La salida es determinista
(misma entrada => mismo hash) y la escritura es atomica.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional

from . import VERSION, atomic_write_text, project_root, sha256_text, templates_dir

HASH_PLACEHOLDER = "__CONFIG_HASH__"

# Mensajes de salida estandar (seccion 6.15).
GEN_SUCCESS = "GEN_SUCCESS"
GEN_CONFLICT = "GEN_CONFLICT"
GEN_VERIFIED = "GEN_VERIFIED"
GEN_ERROR = "GEN_ERROR"


@dataclass
class ConfigBundle:
    """Conjunto de archivos de configuracion generados."""

    files: Dict[str, str] = field(default_factory=dict)
    config_hash: str = ""
    generated_at: str = ""
    profile_id: str = ""

    def manifest(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "config_hash": self.config_hash,
            "generated_at": self.generated_at,
            "files": {name: sha256_text(content) for name, content in sorted(self.files.items())},
        }


def _gate_pins(gates: int) -> str:
    lines: List[str] = ["encoder: main:PA1", "filament_toolhead: main:PA2"]
    port_cycle = ["PB", "PC", "PD"]
    for index in range(gates):
        port = port_cycle[(index // 8) % len(port_cycle)]
        pin = index % 8
        lines.append(f"filament_gate_{index}: main:{port}{pin}")
    return "\n".join(lines)


class ConfigGenerator:
    """Genera el bundle de configuracion a partir de perfil y mapa de hardware."""

    def __init__(self, template_engine: Any = None, generated_at: Optional[str] = None) -> None:
        self.template_engine = template_engine
        self.generated_at = generated_at  # None => hora actual en build_bundle

    # -- Render -------------------------------------------------------------
    def _render_template(self, filename: str, variables: Dict[str, Any]) -> str:
        if self.template_engine is not None and hasattr(self.template_engine, "render"):
            return str(self.template_engine.render(filename, variables))
        template_path = templates_dir() / filename
        template = Template(template_path.read_text(encoding="utf-8"))
        return template.safe_substitute(**variables)

    # -- Pines / placas -----------------------------------------------------
    @staticmethod
    def _boards_module() -> Any:
        extras = project_root() / "klippy" / "extras"
        if str(extras) not in sys.path:
            sys.path.insert(0, str(extras))
        from dog_matrix import boards  # type: ignore

        return boards

    def resolve_pin_plan(self, profile: Any, hardware_map: Optional[Dict[str, Any]] = None) -> Any:
        """Resuelve el plan de pines de la placa seleccionada (o ``None``)."""
        hardware_map = dict(hardware_map or {})
        board_id = hardware_map.get("board") or (
            profile.hardware.get("board") if hasattr(profile, "hardware") else None
        )
        if not board_id:
            return None
        boards = self._boards_module()
        board = boards.load_board(str(board_id), hardware_map.get("boards_dir"))
        topology = hardware_map.get("topology") or board.default_topology()
        units = int(hardware_map.get("units") or (profile.topology.get("units", 1) if hasattr(profile, "topology") else 1))
        gates = hardware_map.get("gates")
        if gates is None and topology == boards.TOPOLOGY_SELECTOR:
            gates = getattr(profile, "gates", None)
        coils = hardware_map.get("coils_per_unit") or board.coils_per_unit
        return boards.build_pin_plan(
            board,
            gates=gates,
            units=units,
            topology=topology,
            coils_per_unit=coils,
            mcu_name=hardware_map.get("mcu_name") or None,
        )

    def _render_board_pins(self, profile: Any, hardware_map: Dict[str, Any]) -> str:
        units = int(
            hardware_map.get("units")
            or (profile.topology.get("units", 1) if hasattr(profile, "topology") else 1)
        )
        if units <= 1:
            plan = self.resolve_pin_plan(profile, hardware_map)
            if plan is None:
                return ""
            return str(self._boards_module().render_board_pins(plan))
        # Multi-unidad: un bloque [board_pins] por unidad (MCU independiente).
        blocks: List[str] = []
        for index in range(units):
            unit_map = dict(hardware_map)
            unit_map["units"] = 1
            unit_map["mcu_name"] = f"mmu{index}"
            plan = self.resolve_pin_plan(profile, unit_map)
            if plan is not None:
                blocks.append(str(self._boards_module().render_board_pins(plan, section=f"dogmatrix{index}")))
        return "\n".join(blocks)

    @staticmethod
    def _machine_block(hardware_map: Dict[str, Any]) -> str:
        """Seccion [dm_machine] con el layout multi-unidad (gates contiguos)."""
        machine = hardware_map.get("machine")
        if not isinstance(machine, dict) or not machine.get("units"):
            return ""
        lines = [
            "[dm_machine]",
            f"vendor: {machine.get('vendor', '')}",
            f"units: {machine.get('units', 1)}",
            f"gates_per_unit: {machine.get('gates_per_unit', 0)}",
            f"total_gates: {machine.get('total_gates', 0)}",
        ]
        for entry in machine.get("layout", []):
            lines.append(
                f"# {entry.get('name')}: gates {entry.get('gate_offset')}-"
                f"{int(entry.get('gate_offset', 0)) + int(entry.get('gates', 0)) - 1}"
            )
        return "\n".join(lines)

    @staticmethod
    def _pins_block_from_plan(plan: Any) -> str:
        """Seccion [dm_pins] basada en alias nativos DM_* del plan."""
        lines: List[str] = []
        for alias in plan.aliases:
            if not alias.startswith("DM_"):
                continue
            lines.append(f"{alias[3:].lower()}: {alias}")
        return "\n".join(lines)

    def render_mmu_config(self, profile: Any, hardware_map: Dict[str, Any]) -> str:
        limits = profile.limits
        plan = self.resolve_pin_plan(profile, hardware_map)
        pins_block = hardware_map.get("pins_block")
        if not pins_block:
            pins_block = self._pins_block_from_plan(plan) if plan is not None else _gate_pins(profile.gates)
        board_pins_block = hardware_map.get("board_pins_block")
        if board_pins_block is None:
            board_pins_block = self._render_board_pins(profile, hardware_map)
        machine_block = hardware_map.get("machine_block")
        if machine_block is None:
            machine_block = self._machine_block(hardware_map)
        variables = {
            "display_name": profile.display_name or profile.profile_id,
            "topology_type": profile.topology_type,
            "gates": profile.gates,
            "version": VERSION,
            "generated_at": hardware_map.get("generated_at", ""),
            "config_hash": HASH_PLACEHOLDER,
            "board_pins_block": board_pins_block,
            "machine_block": machine_block,
            "pins_block": pins_block,
            "encoder_resolution": limits.get("encoder_resolution", 0.45),
            "bowden_length": limits.get("bowden_length_mm", 600),
            "purge_length": limits.get("purge_length_mm", 25),
            "tip_form_length": limits.get("tip_form_length_mm", 8),
            "max_load_speed": limits.get("max_load_speed_mm_s", 80),
            "max_unload_speed": limits.get("max_unload_speed_mm_s", 100),
            "max_distance": limits.get("max_distance_mm", 1500),
            "sensor_timeout": limits.get("sensor_timeout_ms", 500),
            "encoder_error": limits.get("encoder_error_mm", 5),
        }
        rendered = self._render_template("dog_matrix_generated.cfg.tmpl", variables)
        digest = self.calculate_config_hash(rendered)
        return rendered.replace(HASH_PLACEHOLDER, digest)

    def render_macros_config(self, capabilities: Any) -> str:
        profile = capabilities if hasattr(capabilities, "display_name") else None
        variables = {
            "version": VERSION,
            "display_name": getattr(profile, "display_name", "Dog Matrix MMU"),
        }
        return self._render_template("dog_matrix_macros.cfg.tmpl", variables)

    def render_profile_snapshot(self, profile: Any) -> str:
        raw = profile.raw if hasattr(profile, "raw") else dict(profile)
        return json.dumps(raw, indent=2, sort_keys=True) + "\n"

    def render_moonraker_extension(self, options: Dict[str, Any]) -> str:
        lines = ["[dog_matrix]"]
        defaults = {
            "enable_file_preprocessor": True,
            "enable_toolchange_next_pos": True,
            "update_spoolman_location": True,
        }
        merged = {**defaults, **options}
        for key, value in merged.items():
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            lines.append(f"{key}: {rendered}")
        return "\n".join(lines) + "\n"

    def calculate_config_hash(self, content: str) -> str:
        return sha256_text(content)

    # -- Bundle -------------------------------------------------------------
    def build_bundle(self, profile: Any, hardware_map: Optional[Dict[str, Any]] = None) -> ConfigBundle:
        hardware_map = dict(hardware_map or {})
        generated_at = self.generated_at or hardware_map.get("generated_at")
        if not generated_at:
            generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        hardware_map["generated_at"] = generated_at

        mmu_cfg = self.render_mmu_config(profile, hardware_map)
        macros_cfg = self.render_macros_config(profile)
        snapshot = self.render_profile_snapshot(profile)
        moonraker_conf = self.render_moonraker_extension({})

        bundle = ConfigBundle(
            files={
                "dog_matrix_generated.cfg": mmu_cfg,
                "dog_matrix_macros.cfg": macros_cfg,
                "dog_matrix_profile.json": snapshot,
                "moonraker_dog_matrix.conf": moonraker_conf,
            },
            profile_id=getattr(profile, "profile_id", "unknown"),
            generated_at=generated_at,
        )
        combined = "\n".join(bundle.files[name] for name in sorted(bundle.files))
        bundle.config_hash = self.calculate_config_hash(combined)
        return bundle

    def write_atomic_config_bundle(self, bundle: ConfigBundle, dest_dir: str) -> bool:
        dest = Path(dest_dir).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        for name, content in bundle.files.items():
            atomic_write_text(dest / name, content)
        manifest = bundle.manifest()
        atomic_write_text(dest / "dog_matrix_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        return True


__all__ = [
    "ConfigGenerator",
    "ConfigBundle",
    "GEN_SUCCESS",
    "GEN_CONFLICT",
    "GEN_VERIFIED",
    "GEN_ERROR",
]
