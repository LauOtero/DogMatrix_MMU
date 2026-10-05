"""Dog Matrix MMU - instalador y asistente de despliegue.

Paquete de herramientas CLI para preflight, backup, generacion determinista de
configuracion, validacion, aplicacion y rollback atomico. Reune los modulos
descritos en las secciones 6.14-6.17 y 9.4 del informe maestro.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

VERSION = "0.1.0"
DEFAULT_DEST = "~/printer_data/config"


def project_root() -> Path:
    """Raiz del repositorio (override con DM_PROJECT_ROOT)."""
    env = os.environ.get("DM_PROJECT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def profiles_dir() -> Path:
    return project_root() / "profiles"


def load_yaml_document(path: Path) -> Dict[str, Any]:
    """Carga YAML (PyYAML si esta; si no, el parser minimo del runtime)."""
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        extras = project_root() / "klippy" / "extras"
        if str(extras) not in sys.path:
            sys.path.insert(0, str(extras))
        from dog_matrix.capabilities import _mini_yaml_load  # type: ignore

        return _mini_yaml_load(text)


def atomic_write_text(target: Path, text: str) -> None:
    """Escritura atomica: temporal + fsync + renombrado atomico."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_dest(dest: Optional[str]) -> Path:
    return Path(dest or os.environ.get("DM_DEST", DEFAULT_DEST)).expanduser()


__all__ = [
    "VERSION",
    "DEFAULT_DEST",
    "project_root",
    "templates_dir",
    "profiles_dir",
    "load_yaml_document",
    "atomic_write_text",
    "sha256_text",
    "resolve_dest",
]
