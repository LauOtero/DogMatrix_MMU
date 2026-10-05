"""Módulo de purga inteligente para Dog Matrix MMU.

Implementa cálculo de volúmenes de purga, soporte para Blobifier y
control de ooze reduction/stringing durante toolchanges.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from dataclasses import dataclass, field


@dataclass
class PurgeVolumeResult:
    """Resultado del cálculo de volumen de purga."""

    volume_mm3: float
    blobifier_mode: bool = False
    tower_height_mm: float = 0.0
    z_hop_height_mm: float = 0.0
    stringing_reduction: bool = False
    message: str = ""


@dataclass
class PurgeConfig:
    """Configuración de purga."""
    default_length_mm: float = 120.0
    z_hop_height_mm: float = 4.0
    z_hop_speed_mm_s: float = 40.0
    ooze_reduction: bool = True
    use_blobifier: bool = False
    blobifier_factor: float = 0.8  # Factor de reducción de volumen Blobifier


class PurgeManager:
    """Gestiona operaciones de purga para toolchanges."""

    def __init__(self, config: Any = None, profile: Any = None) -> None:
        self.config = config
        self.profile = profile
        self.default_config = (
            PurgeConfig() if config is None else self._load_config(config)
        )

    def _load_config(self, config: Any) -> PurgeConfig:
        """Cargar configuración de purga."""
        # Intentar leer del perfil YAML
        # Por ahora, usar valores por defecto
        return PurgeConfig()

    def calculate_volume(self, toolchange_count: int, config: Optional[PurgeConfig] = None) -> PurgeVolumeResult:
        """Calcular volumen de purga basado en historial y configuración."""
        cfg = config if config else self.default_config
        # Base: longitud_default * área_sección_transversal_aproximada
        # Asumiendo filamento de 1.75mm: area = π * (0.175)^2 ≈ 0.0962 mm²
        # volume = length_mm * area * 1000 (para mm³)
        base_volume = cfg.default_length_mm * 0.0962 * 1000

        result = PurgeVolumeResult(
            volume_mm3=round(base_volume, 2),
            blobifier_mode=cfg.use_blobifier,
            tower_height_mm=round(cfg.default_length_mm * 0.05, 1),  # Estima altura torre
            z_hop_height_mm=cfg.z_hop_height_mm,
            stringing_reduction=cfg.ooze_reduction,
            message=f"Purga calculada: {base_volume:.1f} mm³",
        )

        # Ajustar si se usa Blobifier (reduce volumen al crear blobs compactos)
        if cfg.use_blobifier:
            result.volume_mm3 = result.volume_mm3 * cfg.blobifier_factor
            result.message += " (modo Blobifier activo)"

        return result

    def get_config(self) -> PurgeConfig:
        """Obtener configuración actual."""
        return self.default_config


__all__ = ["PurgeManager", "PurgeVolumeResult", "PurgeConfig"]