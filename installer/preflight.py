"""Preflight: comprobacion del entorno antes de desplegar (informe 9.4).

Requisitos: tiempo de ejecucion de preflight < 2.0 s.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import profiles_dir, project_root

MIN_PYTHON = (3, 8)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = True

    def as_dict(self) -> Dict[str, object]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "fatal": self.fatal}


@dataclass
class PreflightReport:
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if check.fatal)

    def as_dict(self) -> Dict[str, object]:
        return {"ok": self.ok, "checks": [c.as_dict() for c in self.checks]}


class Preflight:
    """Ejecuta el conjunto de comprobaciones previas al despliegue."""

    def __init__(self, dest: Optional[str] = None) -> None:
        self.dest = Path(dest).expanduser() if dest else None

    def run(self) -> PreflightReport:
        report = PreflightReport()
        report.checks.append(self._check_python())
        report.checks.append(self._check_profiles())
        report.checks.append(self._check_dest())
        report.checks.append(self._check_pyyaml())
        report.checks.append(self._check_jsonschema())
        report.checks.append(self._check_native())
        report.checks.append(self._check_klipper())
        report.checks.append(self._check_moonraker())
        report.checks.append(self._check_serial())
        return report

    # -- Comprobaciones -----------------------------------------------------
    def _check_python(self) -> CheckResult:
        ok = sys.version_info[:2] >= MIN_PYTHON
        return CheckResult("python", ok, platform.python_version())

    def _check_profiles(self) -> CheckResult:
        path = profiles_dir()
        return CheckResult("profiles", path.exists() and (path / "schema.json").exists(), str(path))

    def _check_dest(self) -> CheckResult:
        if self.dest is None:
            return CheckResult("dest", True, "no especificado", fatal=False)
        try:
            self.dest.mkdir(parents=True, exist_ok=True)
            probe = self.dest / ".dm_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return CheckResult("dest", True, str(self.dest))
        except OSError as exc:
            return CheckResult("dest", False, f"no escribible: {exc}")

    def _check_pyyaml(self) -> CheckResult:
        try:
            import yaml  # type: ignore  # noqa: F401

            return CheckResult("pyyaml", True, "disponible", fatal=False)
        except ImportError:
            return CheckResult("pyyaml", False, "no instalado (se usara parser minimo)", fatal=False)

    def _check_jsonschema(self) -> CheckResult:
        try:
            import jsonschema  # type: ignore  # noqa: F401

            return CheckResult("jsonschema", True, "disponible", fatal=False)
        except ImportError:
            return CheckResult("jsonschema", False, "no instalado (validacion de esquema omitida)", fatal=False)

    def _check_native(self) -> CheckResult:
        try:
            extras = project_root() / "klippy" / "extras"
            if str(extras) not in sys.path:
                sys.path.insert(0, str(extras))
            from dog_matrix import _native  # type: ignore

            native = _native.native_available()
            return CheckResult(
                "cffi_native",
                True,
                "biblioteca nativa cargada" if native else "fallback Python activo",
                fatal=False,
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult("cffi_native", False, f"no verificable: {exc}", fatal=False)

    def _check_klipper(self) -> CheckResult:
        candidates = [Path.home() / "klipper", Path("/opt/klipper")]
        found = next((path for path in candidates if path.exists()), None)
        return CheckResult("klipper", found is not None, str(found) if found else "no detectado", fatal=False)

    def _check_moonraker(self) -> CheckResult:
        candidates = [Path.home() / "moonraker", Path("/opt/moonraker")]
        found = next((path for path in candidates if path.exists()), None)
        return CheckResult("moonraker", found is not None, str(found) if found else "no detectado", fatal=False)

    def _check_serial(self) -> CheckResult:
        by_id = Path("/dev/serial/by-id")
        if os.name == "nt":
            return CheckResult("serial_devices", True, "no aplica en Windows", fatal=False)
        ok = by_id.exists()
        count = len(list(by_id.glob("*"))) if ok else 0
        return CheckResult("serial_devices", True, f"{count} dispositivo(s)", fatal=False)


__all__ = ["Preflight", "PreflightReport", "CheckResult"]
