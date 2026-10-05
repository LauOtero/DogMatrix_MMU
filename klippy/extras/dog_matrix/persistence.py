"""Persistencia atomica de estado con checksum y backup rotativo.

Implementa el patron Repository + Snapshot: toda escritura es atomica
(fichero temporal + ``fsync`` + renombrado atomico), el estado va envuelto en
un sobre con checksum SHA-256 y las instantaneas permiten restauracion
verificable tras un fallo o corte de energia.

Requisitos (informe 6.10): escritura atomica con fsync, checksum SHA-256,
tolerancia a cortes de energia.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_SCHEMA_VERSION = 1


class PersistenceError(Exception):
    """Error base de persistencia."""


class ChecksumError(PersistenceError):
    """El checksum del estado o de una instantanea no coincide."""


class Persistence:
    """Gestiona el estado persistente del MMU con atomicidad y snapshots."""

    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser()
        self.snapshot_dir = self.path.parent / f"{self.path.name}.snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    # -- Utilidades internas ------------------------------------------------
    @staticmethod
    def _checksum(payload: Dict[str, Any]) -> str:
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @staticmethod
    def _atomic_write(target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _wrap(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "saved_at": time.time(),
            "checksum": self._checksum(data),
            "data": data,
        }

    @staticmethod
    def _unwrap(envelope: Dict[str, Any]) -> Dict[str, Any]:
        data = envelope.get("data", {})
        expected = envelope.get("checksum")
        actual = Persistence._checksum(data)
        if expected != actual:
            raise ChecksumError(
                f"checksum mismatch: esperado {expected}, calculado {actual}"
            )
        return data

    # -- API publica --------------------------------------------------------
    def load(self) -> Dict[str, Any]:
        """Carga el estado. Devuelve ``{}`` si no existe; error si corrupto."""
        if not self.path.exists():
            return {}
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"estado ilegible: {exc}") from exc
        return self._unwrap(envelope)

    def save(self, data: Dict[str, Any]) -> None:
        """Persiste el estado de forma atomica con checksum."""
        envelope = self._wrap(data)
        text = json.dumps(envelope, indent=2, sort_keys=True)
        self._atomic_write(self.path, text)

    def create_snapshot(self) -> str:
        """Crea una instantanea del estado actual y devuelve su id."""
        snapshot_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        suffix = 0
        target = self.snapshot_dir / f"{snapshot_id}.json"
        while target.exists():
            suffix += 1
            target = self.snapshot_dir / f"{snapshot_id}-{suffix}.json"
        if self.path.exists():
            payload = self.path.read_text(encoding="utf-8")
        else:
            payload = json.dumps(self._wrap({}), indent=2)
        self._atomic_write(target, payload)
        return target.stem

    def _snapshot_path(self, snapshot_id: str) -> Path:
        candidate = self.snapshot_dir / f"{snapshot_id}.json"
        if not candidate.exists():
            raise PersistenceError(f"instantanea inexistente: {snapshot_id}")
        return candidate

    def list_snapshots(self) -> List[str]:
        return sorted(p.stem for p in self.snapshot_dir.glob("*.json"))

    def validate_checksum(self, snapshot_id: str) -> bool:
        """Valida el checksum de una instantanea."""
        path = self._snapshot_path(snapshot_id)
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            self._unwrap(envelope)
            return True
        except (OSError, json.JSONDecodeError, ChecksumError):
            return False

    def restore_snapshot(self, snapshot_id: str) -> Dict[str, Any]:
        """Restaura una instantanea sobre el estado activo (verificando checksum)."""
        path = self._snapshot_path(snapshot_id)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        data = self._unwrap(envelope)
        self.save(data)
        return data

    def rotate_backups(self, keep_count: int) -> None:
        """Conserva las ``keep_count`` instantaneas mas recientes."""
        keep_count = max(0, int(keep_count))
        snapshots = sorted(self.snapshot_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for stale in snapshots[:-keep_count] if keep_count else snapshots:
            try:
                stale.unlink()
            except OSError:
                pass

    def backup_file(self, dest_dir: str) -> Optional[str]:
        """Copia el estado activo a ``dest_dir`` (para el instalador)."""
        if not self.path.exists():
            return None
        dest = Path(dest_dir).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f"{self.path.name}.{int(time.time())}.bak"
        shutil.copy2(self.path, target)
        return str(target)


__all__ = ["Persistence", "PersistenceError", "ChecksumError", "STATE_SCHEMA_VERSION"]
