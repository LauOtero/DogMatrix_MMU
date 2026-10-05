"""Validacion estatica y en tiempo real (informe 6.16).

Comprueba esquemas de configuracion, conflictos de pines GPIO, compatibilidad de
versiones y salud del entorno; incluye la verificacion de integridad de los
bindings nativos (CFFI). Encadena reglas al estilo Chain of Responsibility.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import profiles_dir

# Mensajes de salida estandar (seccion 6.16).
VALIDATION_PASS = "VALIDATION_PASS"
VALIDATION_WARNING = "VALIDATION_WARNING"
VALIDATION_FAIL = "VALIDATION_FAIL"

# Versiones minimas soportadas.
MIN_VERSIONS = {"klipper": "0.10.0", "moonraker": "0.7.0", "dog_matrix": "0.1.0"}


@dataclass
class ValidationResult:
    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return VALIDATION_FAIL
        if self.warnings:
            return VALIDATION_WARNING
        return VALIDATION_PASS

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "errors": self.errors, "warnings": self.warnings}


@dataclass
class PinConflict:
    pin: str
    sources: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {"pin": self.pin, "sources": self.sources}


@dataclass
class CompatibilityReport:
    compatible: bool
    details: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"compatible": self.compatible, "details": self.details}


@dataclass
class HealthStatus:
    healthy: bool
    checks: Dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"healthy": self.healthy, "checks": self.checks}


def _version_tuple(version: str) -> tuple:
    parts: List[int] = []
    for token in str(version).replace("-", ".").split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


class SystemValidator:
    """Validador del sistema Dog Matrix."""

    def __init__(self, printer_config: Any = None) -> None:
        self.printer_config = printer_config

    # -- Schema -------------------------------------------------------------
    def validate_schema(self, config_dict: Dict[str, Any], schema_version: int = 1) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []
        if not isinstance(config_dict, dict):
            return ValidationResult(False, ["la configuracion debe ser un mapa"])
        if int(config_dict.get("schema_version", schema_version)) != schema_version:
            errors.append(
                f"schema_version inesperada: {config_dict.get('schema_version')!r}"
            )
        schema_path = profiles_dir() / "schema.json"
        if schema_path.exists():
            try:
                import jsonschema  # type: ignore

                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                validator = jsonschema.Draft7Validator(schema)
                for err in sorted(validator.iter_errors(config_dict), key=lambda e: list(e.path)):
                    errors.append(f"schema: {'/'.join(map(str, err.path))}: {err.message}")
            except ImportError:
                warnings.append("jsonschema no disponible; validacion de esquema omitida")
        else:
            warnings.append("profiles/schema.json no encontrado")
        return ValidationResult(not errors, errors, warnings)

    # -- Pines --------------------------------------------------------------
    def validate_pin_conflicts(self, mcu_pin_map: Dict[str, Any]) -> List[PinConflict]:
        """Detecta pines asignados a mas de un uso en el mismo MCU."""
        conflicts: List[PinConflict] = []
        if not isinstance(mcu_pin_map, dict):
            return conflicts
        for pin, usage in mcu_pin_map.items():
            sources = usage if isinstance(usage, list) else [usage]
            sources = [str(item) for item in sources if item]
            if len(sources) > 1:
                conflicts.append(PinConflict(pin=str(pin), sources=sources))
        return conflicts

    # -- Versiones ----------------------------------------------------------
    def validate_version_compatibility(self, env_versions: Dict[str, str]) -> CompatibilityReport:
        details: Dict[str, str] = {}
        compatible = True
        for component, minimum in MIN_VERSIONS.items():
            current = str(env_versions.get(component, ""))
            if not current:
                details[component] = "no detectado"
                compatible = False
                continue
            if _version_tuple(current) < _version_tuple(minimum):
                details[component] = f"{current} < minimo {minimum}"
                compatible = False
            else:
                details[component] = f"{current} OK"
        return CompatibilityReport(compatible, details)

    # -- Salud / CFFI -------------------------------------------------------
    def perform_realtime_health_check(self) -> HealthStatus:
        checks = {
            "python_supported": sys.version_info[:2] >= (3, 8),
            "profiles_dir": profiles_dir().exists(),
            "state_writable": self._state_writable(),
        }
        return HealthStatus(all(checks.values()), checks)

    @staticmethod
    def _state_writable() -> bool:
        try:
            import tempfile

            with tempfile.NamedTemporaryFile(delete=True):
                return True
        except OSError:
            return False

    def verify_cffi_bindings_integrity(self) -> bool:
        """Verifica que la capa nativa cargue correctamente (o el fallback)."""
        extras = Path(__file__).resolve().parent.parent / "klippy" / "extras"
        if str(extras) not in sys.path:
            sys.path.insert(0, str(extras))
        try:
            from dog_matrix import _native  # type: ignore

            # Un kernel de prueba valida la integridad funcional del binding.
            value = _native.iir_step(0.0, 10.0, 1.0)
            return abs(value - 10.0) < 1e-6
        except Exception:  # noqa: BLE001 - binding corrupto o ausente
            return False


__all__ = [
    "SystemValidator",
    "ValidationResult",
    "PinConflict",
    "CompatibilityReport",
    "HealthStatus",
    "VALIDATION_PASS",
    "VALIDATION_WARNING",
    "VALIDATION_FAIL",
    "MIN_VERSIONS",
]
