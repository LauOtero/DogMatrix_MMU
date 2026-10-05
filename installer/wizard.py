"""Asistente de instalacion y configuracion (informe 6.14, fase H13).

Orquesta el despliegue completo: preflight, deteccion de hardware, seleccion de
perfil, calibracion guiada, generacion determinista de configuracion,
validacion, escritura atomica y rollback automatico ante fallo. Soporta modo
interactivo y desatendido (``headless``) y previsualizacion (``dry_run``).
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import project_root, resolve_dest
from .generator import ConfigBundle, ConfigGenerator
from .preflight import Preflight, PreflightReport
from .rollback import RollbackManager
from .validator import SystemValidator

# Catalogo de errores estandar (seccion 6.14).
ERR_HW_NOT_FOUND = "ERR_HW_NOT_FOUND"
ERR_INVALID_PROFILE = "ERR_INVALID_PROFILE"
ERR_CONFLICTING_PINS = "ERR_CONFLICTING_PINS"
ERR_DEPLOYMENT_TIMEOUT = "ERR_DEPLOYMENT_TIMEOUT"
ERR_ROLLBACK_EXECUTED = "ERR_ROLLBACK_EXECUTED"

DEFAULT_TIMEOUT_S = 300.0


@dataclass
class WizardContext:
    """Contexto de ejecucion del asistente."""

    dest: str = ""
    profile_id: str = "box_turtle"
    headless: bool = False
    dry_run: bool = False
    generated_at: Optional[str] = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    log: Callable[[str], None] = field(default=lambda message: print(message))

    def __post_init__(self) -> None:
        self.dest = str(resolve_dest(self.dest))


@dataclass
class MCUDevice:
    name: str
    transport: str = "usb"
    serial_by_id: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {"name": self.name, "transport": self.transport, "serial_by_id": self.serial_by_id}


@dataclass
class HardwareProbeResult:
    preflight: PreflightReport
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.preflight.ok


@dataclass
class CalibrationResult:
    ok: bool
    steps: Dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "steps": self.steps}


@dataclass
class VerificationReport:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


@dataclass
class DeploymentResult:
    success: bool
    files: List[str] = field(default_factory=list)
    config_hash: str = ""
    snapshot_id: Optional[str] = None
    error_code: Optional[str] = None
    message: str = ""
    dry_run: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "files": self.files,
            "config_hash": self.config_hash,
            "snapshot_id": self.snapshot_id,
            "error_code": self.error_code,
            "message": self.message,
            "dry_run": self.dry_run,
        }


class InstallationWizard:
    """Asistente de despliegue de Dog Matrix MMU."""

    def __init__(self, context: WizardContext) -> None:
        self.context = context
        self.validator = SystemValidator()
        self.rollback = RollbackManager(self.context.dest)
        self._profile: Any = None

    # -- Diagnostico --------------------------------------------------------
    def probe_hardware_environment(self) -> HardwareProbeResult:
        report = Preflight(self.context.dest).run()
        details = {
            "python": sys.version.split()[0],
            "dest": self.context.dest,
            "profiles": str(project_root() / "profiles"),
        }
        return HardwareProbeResult(preflight=report, details=details)

    def detect_mcu_devices(self) -> List[MCUDevice]:
        devices: List[MCUDevice] = []
        by_id = Path("/dev/serial/by-id")
        if by_id.exists():
            for entry in sorted(by_id.glob("*")):
                name = entry.name
                if "DogMatrix" in name or "dogmatrix" in name.lower():
                    devices.append(MCUDevice(name=name.split("-")[-1], transport="usb", serial_by_id=str(entry)))
        if not devices and self._profile is not None:
            for mcu in self._profile.hardware.get("mcu", []):
                devices.append(
                    MCUDevice(
                        name=str(mcu.get("name", "mmu")),
                        transport=str(mcu.get("transport", "usb")),
                        serial_by_id=str(mcu.get("serial_by_id", "")),
                    )
                )
        return devices

    # -- Perfil -------------------------------------------------------------
    def select_mmu_profile(self, profile_id: Optional[str] = None) -> Any:
        extras = project_root() / "klippy" / "extras"
        if str(extras) not in sys.path:
            sys.path.insert(0, str(extras))
        from dog_matrix.capabilities import Capabilities, ProfileError, find_profile  # type: ignore

        name = profile_id or self.context.profile_id
        try:
            capabilities = Capabilities(str(find_profile(name)))
            self._profile = capabilities.load()
        except ProfileError as exc:
            raise ValueError(f"{ERR_INVALID_PROFILE}: {exc}") from exc
        return self._profile

    def run_hardware_calibration_steps(self, steps: List[str]) -> CalibrationResult:
        results: Dict[str, bool] = {}
        for step in steps:
            self.context.log(f"  [calibracion] {step}...")
            # Pasos simulados: en HIL cada paso valida un sensor/mecanismo real.
            results[step] = True
        return CalibrationResult(all(results.values()), results)

    # -- Despliegue ---------------------------------------------------------
    def generate_and_install_configs(self, target_dir: Optional[str] = None) -> DeploymentResult:
        started = time.monotonic()
        target = str(Path(target_dir).expanduser() if target_dir else Path(self.context.dest))
        self.context.log(f"[{self.context.profile_id}] generando configuracion en {target}")

        if self._profile is None:
            try:
                self.select_mmu_profile()
            except ValueError as exc:
                return DeploymentResult(False, error_code=ERR_INVALID_PROFILE, message=str(exc))

        generator = ConfigGenerator(generated_at=self.context.generated_at)
        bundle: ConfigBundle = generator.build_bundle(self._profile)

        # Validacion estatica antes de escribir.
        schema_result = self.validator.validate_schema(self._profile.raw)
        pins = self._pin_map(bundle.files.get("dog_matrix_generated.cfg", ""))
        conflicts = self.validator.validate_pin_conflicts(pins)
        if schema_result.errors:
            return DeploymentResult(
                False, error_code=ERR_INVALID_PROFILE, message="; ".join(schema_result.errors)
            )
        if conflicts:
            message = "; ".join(f"{c.pin}: {c.sources}" for c in conflicts)
            return DeploymentResult(False, error_code=ERR_CONFLICTING_PINS, message=message)

        if (time.monotonic() - started) > self.context.timeout_s:
            return DeploymentResult(False, error_code=ERR_DEPLOYMENT_TIMEOUT, message="timeout de despliegue")

        files = sorted(bundle.files.keys())
        if self.context.dry_run:
            self.context.log("[dry-run] no se escriben archivos")
            return DeploymentResult(
                True, files=files, config_hash=bundle.config_hash, message="previsualizacion", dry_run=True
            )

        snapshot_id = self.rollback.prepare()
        try:
            generator.write_atomic_config_bundle(bundle, target)
        except OSError as exc:
            self.rollback.rollback(snapshot_id)
            return DeploymentResult(
                False, error_code=ERR_ROLLBACK_EXECUTED, message=f"fallo de escritura: {exc}", snapshot_id=snapshot_id
            )

        verification = self.verify_system_integrity(target)
        if not verification.ok:
            self.rollback.rollback(snapshot_id)
            return DeploymentResult(
                False,
                error_code=ERR_ROLLBACK_EXECUTED,
                message="; ".join(verification.errors),
                snapshot_id=snapshot_id,
            )
        self.rollback.commit()
        self.context.log(f"[OK] desplegado (hash {bundle.config_hash})")
        return DeploymentResult(
            True, files=files, config_hash=bundle.config_hash, snapshot_id=snapshot_id, message="despliegue correcto"
        )

    def verify_system_integrity(self, target_dir: Optional[str] = None) -> VerificationReport:
        target = Path(target_dir).expanduser() if target_dir else Path(self.context.dest)
        errors: List[str] = []
        warnings: List[str] = []
        for name in ("dog_matrix_generated.cfg", "dog_matrix_macros.cfg", "dog_matrix_profile.json"):
            if not (target / name).exists():
                errors.append(f"falta {name}")
        if not self.validator.verify_cffi_bindings_integrity():
            warnings.append("bindings nativos no verificados (fallback Python)")
        health = self.validator.perform_realtime_health_check()
        if not health.healthy:
            errors.extend(name for name, ok in health.checks.items() if not ok)
        return VerificationReport(not errors, errors, warnings)

    def execute_atomic_rollback(self, snapshot_id: Optional[str] = None) -> bool:
        return self.rollback.rollback(snapshot_id)

    # -- Utilidades ---------------------------------------------------------
    @staticmethod
    def _pin_map(generated_cfg: str) -> Dict[str, List[str]]:
        """Extrae el mapa pin -> usos desde la seccion [dm_pins] generada."""
        pins: Dict[str, List[str]] = {}
        in_pins = False
        for raw in generated_cfg.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_pins = line == "[dm_pins]"
                continue
            if not in_pins or ":" not in line:
                continue
            key, _, value = line.partition(":")
            pin = value.strip()
            pins.setdefault(pin, []).append(key.strip())
        return pins


__all__ = [
    "InstallationWizard",
    "WizardContext",
    "HardwareProbeResult",
    "MCUDevice",
    "CalibrationResult",
    "VerificationReport",
    "DeploymentResult",
    "ERR_HW_NOT_FOUND",
    "ERR_INVALID_PROFILE",
    "ERR_CONFLICTING_PINS",
    "ERR_DEPLOYMENT_TIMEOUT",
    "ERR_ROLLBACK_EXECUTED",
]
