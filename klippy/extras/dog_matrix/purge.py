"""Purga inteligente: matriz de volumenes, Blobifier y secuencia fisica.

Calcula el volumen de purga necesario al cambiar de material/color, genera una
matriz completa (gate -> gate), ensena la secuencia G-code de purga (z-hop,
extrusion, wipe) y soporta el modo Blobifier (blobs compactos que reducen el
volumen). Emite los movimientos via un emisor inyectado: determinista, no
bloqueante y testeable sin hardware.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

FILAMENT_DIAMETER_MM = 1.75
FILAMENT_AREA_MM2 = math.pi * (FILAMENT_DIAMETER_MM / 2.0) ** 2

# Factores de purga segun transicion de material.
MATERIAL_SAME = 1.0
MATERIAL_SIMILAR = 1.2
MATERIAL_DIFFERENT = 1.6

_DEFAULT_LENGTH_MM = 120.0


def _cfg_get(config: Any, key: str, default: Any) -> Any:
    """Lee un valor de un ConfigWrapper o de un dict de forma segura."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            return default
    return default


@dataclass
class PurgeVolumeResult:
    """Resultado del calculo de volumen de purga."""

    volume_mm3: float
    blobifier_mode: bool = False
    tower_height_mm: float = 0.0
    z_hop_height_mm: float = 0.0
    stringing_reduction: bool = False
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "volume_mm3": round(self.volume_mm3, 2),
            "blobifier_mode": self.blobifier_mode,
            "tower_height_mm": self.tower_height_mm,
            "z_hop_height_mm": self.z_hop_height_mm,
            "stringing_reduction": self.stringing_reduction,
            "message": self.message,
        }


@dataclass
class PurgeConfig:
    """Configuracion de purga."""

    default_length_mm: float = _DEFAULT_LENGTH_MM
    z_hop_height_mm: float = 4.0
    z_hop_speed_mm_s: float = 40.0
    purge_speed_mm_s: float = 5.0
    wipe_length_mm: float = 5.0
    ooze_reduction: bool = True
    use_blobifier: bool = False
    blobifier_factor: float = 0.8
    material_factor_similar: float = MATERIAL_SIMILAR
    material_factor_different: float = MATERIAL_DIFFERENT


class PurgeManager:
    """Gestiona operaciones de purga para toolchanges."""

    def __init__(
        self,
        config: Any = None,
        profile: Any = None,
        emit: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self._emit_fn = emit
        self.scripts: List[str] = []
        self.default_config = self._load_config(config, profile)

    # -- Configuracion ------------------------------------------------------
    def _load_config(self, config: Any, profile: Any) -> PurgeConfig:
        hardware = getattr(profile, "hardware", {}) if profile is not None else {}
        section = hardware.get("purge", {}) if isinstance(hardware, dict) else {}
        get = lambda key, default: _cfg_get(config, key, section.get(key, default))  # noqa: E731
        return PurgeConfig(
            default_length_mm=float(get("purge_length_mm", _DEFAULT_LENGTH_MM)),
            z_hop_height_mm=float(get("purge_z_hop_mm", 4.0)),
            z_hop_speed_mm_s=float(get("purge_z_hop_speed_mm_s", 40.0)),
            purge_speed_mm_s=float(get("purge_speed_mm_s", 5.0)),
            wipe_length_mm=float(get("purge_wipe_length_mm", 5.0)),
            ooze_reduction=bool(get("purge_ooze_reduction", True)),
            use_blobifier=bool(get("purge_use_blobifier", False)),
            blobifier_factor=float(get("purge_blobifier_factor", 0.8)),
        )

    # -- Calculo de volumen -------------------------------------------------
    @staticmethod
    def _material_factor(source: Optional[str], target: Optional[str], cfg: PurgeConfig) -> float:
        a = (source or "").strip().upper()
        b = (target or "").strip().upper()
        if not a or not b or a == b:
            return MATERIAL_SAME
        # Materiales de la misma familia (p. ej. PLA/PLA+, PETG/PETG-CF).
        if a.split("+")[0].split("-")[0] == b.split("+")[0].split("-")[0]:
            return cfg.material_factor_similar
        return cfg.material_factor_different

    def calculate_volume(
        self,
        toolchange_count: int = 0,
        from_material: Optional[str] = None,
        to_material: Optional[str] = None,
        from_color: Optional[str] = None,
        to_color: Optional[str] = None,
        config: Optional[PurgeConfig] = None,
    ) -> PurgeVolumeResult:
        """Calcula el volumen de purga segun transicion material/color.

        Volumen = longitud * area_filamento * factor_material. El modo Blobifier
        reduce el volumen. El color distinto anade un 10 % adicional.
        """
        cfg = config or self.default_config
        length = cfg.default_length_mm
        length *= self._material_factor(from_material, to_material, cfg)
        if from_color and to_color and from_color.strip().lower() != to_color.strip().lower():
            length *= 1.1
        volume = length * FILAMENT_AREA_MM2
        result = PurgeVolumeResult(
            volume_mm3=round(volume, 2),
            blobifier_mode=cfg.use_blobifier,
            tower_height_mm=round(length * 0.05, 1),
            z_hop_height_mm=cfg.z_hop_height_mm,
            stringing_reduction=cfg.ooze_reduction,
            message=f"Purga calculada: {volume:.1f} mm3 ({length:.1f} mm)",
        )
        if cfg.use_blobifier:
            result.volume_mm3 = round(result.volume_mm3 * cfg.blobifier_factor, 2)
            result.message += " (modo Blobifier activo)"
        return result

    def build_matrix(
        self,
        materials: Optional[List[str]] = None,
        colors: Optional[List[str]] = None,
        gates: Optional[int] = None,
        config: Optional[PurgeConfig] = None,
    ) -> List[List[float]]:
        """Matriz gate->gate de volumenes de purga (mm3)."""
        count = gates if gates is not None else (getattr(self.profile, "gates", 0) or 0)
        mats = list(materials or [""] * count)
        cols = list(colors or [""] * count)
        matrix: List[List[float]] = []
        for i in range(count):
            row: List[float] = []
            for j in range(count):
                result = self.calculate_volume(
                    0,
                    from_material=mats[i] if i < len(mats) else "",
                    to_material=mats[j] if j < len(mats) else "",
                    from_color=cols[i] if i < len(cols) else "",
                    to_color=cols[j] if j < len(cols) else "",
                    config=config,
                )
                row.append(result.volume_mm3)
            matrix.append(row)
        return matrix

    def calculate_purge_volumes(
        self,
        materials: Optional[List[str]] = None,
        colors: Optional[List[str]] = None,
        gates: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Resumen de la matriz de purga (total y por par)."""
        matrix = self.build_matrix(materials=materials, colors=colors, gates=gates)
        total = round(sum(sum(row) for row in matrix), 2)
        return {
            "matrix": matrix,
            "gates": len(matrix),
            "total_mm3": total,
            "blobifier": self.default_config.use_blobifier,
        }

    # -- Secuencia fisica ---------------------------------------------------
    def purge_sequence(
        self,
        volume_mm3: float,
        from_gate: Optional[int] = None,
        to_gate: Optional[int] = None,
        config: Optional[PurgeConfig] = None,
    ) -> List[str]:
        """Genera y emite la secuencia G-code de purga; devuelve los scripts."""
        cfg = config or self.default_config
        length = max(0.0, float(volume_mm3)) / FILAMENT_AREA_MM2
        scripts: List[str] = [
            f"G91",  # posicionamiento relativo
            f"G1 Z{cfg.z_hop_height_mm:.2f} F{cfg.z_hop_speed_mm_s * 60:.2f}",
        ]
        if cfg.use_blobifier:
            scripts.append(f"G1 E{length:.3f} F{cfg.purge_speed_mm_s * 60:.2f}")
        else:
            scripts.append(f"G1 E{length:.3f} F{cfg.purge_speed_mm_s * 60:.2f}")
            scripts.append(f"G1 E{cfg.wipe_length_mm:.2f} F{cfg.purge_speed_mm_s * 60:.2f}")
        scripts.append(f"G1 Z-{cfg.z_hop_height_mm:.2f} F{cfg.z_hop_speed_mm_s * 60:.2f}")
        scripts.append("G90")
        for script in scripts:
            self._emit(script)
        self.scripts = scripts
        return scripts

    def _emit(self, script: str) -> None:
        if self._emit_fn is None:
            return
        try:
            self._emit_fn(script)
        except Exception:  # noqa: BLE001 - el emisor nunca debe romper el flujo
            pass

    def get_config(self) -> PurgeConfig:
        """Obtener configuracion actual."""
        return self.default_config

    def get_status(self) -> Dict[str, Any]:
        return {
            "length_mm": self.default_config.default_length_mm,
            "blobifier": self.default_config.use_blobifier,
            "z_hop_mm": self.default_config.z_hop_height_mm,
            "filament_area_mm2": round(FILAMENT_AREA_MM2, 4),
        }


__all__ = [
    "PurgeManager", "PurgeVolumeResult", "PurgeConfig",
    "FILAMENT_DIAMETER_MM", "FILAMENT_AREA_MM2",
]
