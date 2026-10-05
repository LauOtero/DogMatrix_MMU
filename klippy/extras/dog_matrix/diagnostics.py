"""Logs estructurados (JSON Lines) y generacion de evidence bundles firmados.

Implementa el patron Observer en su version mas simple: los modulos emiten
eventos y este componente los serializa, rota y empaqueta para auditoria.
El formato es compatible con stacks ELK/Loki (un objeto JSON por linea).

Requisitos (informe 6.11): latencia de log < 1 ms, rotacion automatica,
formato compatible con ELK.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUPS = 3


class Diagnostics:
    """Registro estructurado y empaquetado de evidencias."""

    def __init__(self, config: Any = None) -> None:
        cfg = self._as_dict(config)
        self.level = str(cfg.get("log_level", os.environ.get("DM_LOG_LEVEL", "info"))).lower()
        log_path = cfg.get(
            "log_path",
            os.environ.get("DM_LOG_PATH", "~/printer_data/logs/dog_matrix.jsonl"),
        )
        self.log_path = Path(log_path).expanduser()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_dir = Path(
            cfg.get("evidence_dir", os.environ.get("DM_EVIDENCE_DIR", "~/printer_data/evidence"))
        ).expanduser()
        self.max_bytes = int(cfg.get("log_max_bytes", DEFAULT_MAX_BYTES))
        self.backups = int(cfg.get("log_backups", DEFAULT_BACKUPS))
        self._recent_errors: List[Dict[str, Any]] = []
        self._secret = os.environ.get("DM_EVIDENCE_KEY", "")

    @staticmethod
    def _as_dict(config: Any) -> Dict[str, Any]:
        if config is None:
            return {}
        if isinstance(config, dict):
            return config
        getter = getattr(config, "get", None)
        if callable(getter):
            out: Dict[str, Any] = {}
            for key in ("log_level", "log_path", "evidence_dir", "log_max_bytes", "log_backups"):
                try:
                    out[key] = getter(key, None)
                except TypeError:
                    pass
            return {k: v for k, v in out.items() if v is not None}
        return {}

    def _enabled(self, level: str) -> bool:
        return LOG_LEVELS.get(level, 20) >= LOG_LEVELS.get(self.level, 20)

    def _rotate_if_needed(self) -> None:
        try:
            if not self.log_path.exists() or self.log_path.stat().st_size < self.max_bytes:
                return
        except OSError:
            return
        stamp = time.strftime("%Y%m%dT%H%M%S")
        rotated = self.log_path.with_name(f"{self.log_path.name}.{stamp}")
        try:
            os.replace(self.log_path, rotated)
        except OSError:
            return
        backups = sorted(
            self.log_path.parent.glob(f"{self.log_path.name}.*"), key=lambda p: p.stat().st_mtime
        )
        for stale in backups[: -self.backups] if self.backups else backups:
            try:
                stale.unlink()
            except OSError:
                pass

    def log_event(self, level: str, component: str, event: str, **kwargs: Any) -> Dict[str, Any]:
        """Registra un evento estructurado. Devuelve el registro emitido."""
        record: Dict[str, Any] = {
            "ts": round(time.time(), 6),
            "level": level,
            "component": component,
            "event": event,
        }
        record.update(kwargs)
        if level in ("error", "critical"):
            self._recent_errors.append(record)
            if len(self._recent_errors) > 100:
                self._recent_errors.pop(0)
        if not self._enabled(level):
            return record
        self._rotate_if_needed()
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            pass
        return record

    def get_recent_errors(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._recent_errors[-max(0, int(limit)):]

    def create_evidence_bundle(self, output_dir: Optional[str] = None) -> str:
        """Empaqueta logs + resumen + hashes en un ZIP firmado."""
        out_dir = Path(output_dir).expanduser() if output_dir else self.evidence_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        bundle_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        bundle_path = out_dir / f"dog_matrix_evidence_{bundle_id}.zip"

        manifest: Dict[str, Any] = {
            "evidence_bundle": {
                "id": bundle_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "software_version": "0.1.0",
                "recent_errors": self.get_recent_errors(50),
                "hashes": {},
                "signature": "",
            }
        }

        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
            if self.log_path.exists():
                archive.write(self.log_path, arcname="logs/dog_matrix.jsonl")
                manifest["evidence_bundle"]["hashes"]["logs/dog_matrix.jsonl"] = self._sha256_file(
                    self.log_path
                )
            manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
            archive.writestr("manifest.json", manifest_bytes)
            if self._secret:
                signature = hmac.new(
                    self._secret.encode("utf-8"), manifest_bytes, hashlib.sha256
                ).hexdigest()
                archive.writestr("manifest.sig", signature)
        return str(bundle_path)

    def sign_evidence(self, bundle_path: str) -> str:
        """Firma un bundle existente. Devuelve la firma (hex) o cadena vacia."""
        path = Path(bundle_path)
        if not path.exists():
            raise FileNotFoundError(bundle_path)
        digest = self._sha256_file(path)
        if not self._secret:
            return ""
        return hmac.new(self._secret.encode("utf-8"), digest.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _sha256_file(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                hasher.update(chunk)
        return "sha256:" + hasher.hexdigest()


__all__ = ["Diagnostics", "LOG_LEVELS"]
