"""Rollback atomico de despliegues (Snapshot & Restore, informe 6.14).

Reserva un snapshot antes de aplicar cambios y permite revertir a el si algo
falla. El estado pendiente se persiste para sobrevivir a un reinicio del
instalador.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import atomic_write_text
from .backup import BackupManager

PENDING_FILENAME = ".dm_pending_snapshot"


class RollbackManager:
    """Coordina snapshots previos a cambios y su restauracion."""

    def __init__(self, dest: str) -> None:
        self.dest = Path(dest).expanduser()
        self.backups = BackupManager(dest)
        self._pending_path = self.dest / PENDING_FILENAME

    def prepare(self, label: str = "pre-apply") -> str:
        """Crea un snapshot y lo marca como pendiente. Devuelve su id."""
        snapshot_id = self.backups.create(label=label)
        atomic_write_text(self._pending_path, json.dumps({"snapshot_id": snapshot_id}))
        return snapshot_id

    def pending(self) -> Optional[str]:
        if not self._pending_path.exists():
            return None
        try:
            return json.loads(self._pending_path.read_text(encoding="utf-8")).get("snapshot_id")
        except (OSError, ValueError):
            return None

    def rollback(self, snapshot_id: Optional[str] = None) -> bool:
        """Restaura el snapshot indicado (o el pendiente) y limpia el estado."""
        target = snapshot_id or self.pending()
        if target is None:
            return False
        restored = self.backups.restore(target)
        if restored:
            self.commit()
        return restored

    def commit(self) -> None:
        """Confirma la aplicacion: elimina la marca de pendiente."""
        try:
            if self._pending_path.exists():
                self._pending_path.unlink()
        except OSError:
            pass


__all__ = ["RollbackManager", "PENDING_FILENAME"]
