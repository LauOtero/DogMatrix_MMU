"""Backup de configuracion con snapshots verificables (informe 6.10, 9.4)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import List, Optional

from . import atomic_write_text, sha256_text

BACKUP_DIRNAME = ".dm_backups"
CONFIG_GLOBS = ("*.cfg", "*.conf", "*.json", "*.ini")


class BackupManager:
    """Crea, lista y restaura snapshots de los archivos de configuracion."""

    def __init__(self, dest: str) -> None:
        self.dest = Path(dest).expanduser()
        self.root = self.dest / BACKUP_DIRNAME

    def _iter_config_files(self) -> List[Path]:
        files: List[Path] = []
        if not self.dest.exists():
            return files
        for pattern in CONFIG_GLOBS:
            files.extend(sorted(self.dest.glob(pattern)))
        return [path for path in files if path.is_file()]

    def create(self, label: str = "") -> str:
        """Crea un snapshot. Devuelve su identificador."""
        self.root.mkdir(parents=True, exist_ok=True)
        snapshot_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        if label:
            snapshot_id = f"{snapshot_id}-{label}"
        snapshot = self.root / snapshot_id
        files_dir = snapshot / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"id": snapshot_id, "created_at": time.time(), "label": label, "files": {}}
        for path in self._iter_config_files():
            content = path.read_text(encoding="utf-8")
            shutil.copy2(path, files_dir / path.name)
            manifest["files"][path.name] = sha256_text(content)
        atomic_write_text(snapshot / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        return snapshot_id

    def list(self) -> List[str]:
        if not self.root.exists():
            return []
        return sorted(path.name for path in self.root.iterdir() if path.is_dir())

    def latest(self) -> Optional[str]:
        snapshots = self.list()
        return snapshots[-1] if snapshots else None

    def restore(self, snapshot_id: str) -> bool:
        snapshot = self.root / snapshot_id
        files_dir = snapshot / "files"
        if not files_dir.exists():
            return False
        for path in files_dir.iterdir():
            if path.is_file():
                shutil.copy2(path, self.dest / path.name)
        return True


__all__ = ["BackupManager", "BACKUP_DIRNAME"]
