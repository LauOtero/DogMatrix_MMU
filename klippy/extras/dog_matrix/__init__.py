"""Dog Matrix MMU - extension Klipper.

Punto de entrada del modulo ``[dog_matrix]``. Importar este paquete registra el
objeto principal en Klipper a traves de ``load_config``.

Documentacion maestra: ``docs/informe dogmatrix-mmu.md`` (INF-ENG-DM-006).
"""

from __future__ import annotations

from .core import DogMatrixCore, load_config

__all__ = ["DogMatrixCore", "load_config"]
__version__ = "0.1.0"
