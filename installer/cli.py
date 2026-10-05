"""CLI del instalador Dog Matrix MMU (informe 9.4, fase H13).

Subcomandos: ``preflight``, ``wizard``, ``generate``, ``validate``, ``apply``,
``rollback``, ``migrate`` y ``doctor``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import VERSION
from .generator import ConfigGenerator
from .migrator import ConfigMigrator
from .preflight import Preflight
from .rollback import RollbackManager
from .validator import SystemValidator
from .wizard import InstallationWizard, WizardContext

EXIT_OK = 0
EXIT_ERROR = 1


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dest", default=None, help="directorio destino de configuracion")
    parser.add_argument("--json", action="store_true", help="salida en JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dog-matrix", description="Dog Matrix MMU installer")
    parser.add_argument("--version", action="version", version=f"dog-matrix {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_pre = sub.add_parser("preflight", help="comprobar el entorno")
    _add_common(p_pre)

    p_wiz = sub.add_parser("wizard", help="asistente de despliegue completo")
    _add_common(p_wiz)
    p_wiz.add_argument("--profile", default="box_turtle")
    p_wiz.add_argument("--headless", action="store_true")
    p_wiz.add_argument("--dry-run", action="store_true")

    p_gen = sub.add_parser("generate", help="generar configuracion sin calibrar")
    _add_common(p_gen)
    p_gen.add_argument("--profile", default="box_turtle")
    p_gen.add_argument("--dry-run", action="store_true")

    p_val = sub.add_parser("validate", help="validar configuracion generada")
    _add_common(p_val)

    p_app = sub.add_parser("apply", help="aplicar configuracion (con rollback)")
    _add_common(p_app)
    p_app.add_argument("--profile", default="box_turtle")

    p_rb = sub.add_parser("rollback", help="revertir a un snapshot")
    _add_common(p_rb)
    p_rb.add_argument("--snapshot", default=None)

    p_mig = sub.add_parser("migrate", help="migrar desde Happy Hare/AFC")
    _add_common(p_mig)
    p_mig.add_argument("--source", required=True, help="ruta a mmu_vars.cfg")
    p_mig.add_argument("--dry-run", action="store_true")

    p_doc = sub.add_parser("doctor", help="diagnostico del entorno y de los bindings")
    _add_common(p_doc)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {
        "preflight": _cmd_preflight,
        "wizard": _cmd_wizard,
        "generate": _cmd_generate,
        "validate": _cmd_validate,
        "apply": _cmd_apply,
        "rollback": _cmd_rollback,
        "migrate": _cmd_migrate,
        "doctor": _cmd_doctor,
    }[args.command]
    return handler(args)


def _emit(args: argparse.Namespace, payload: dict, text: str = "") -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif text:
        print(text)


def _cmd_preflight(args: argparse.Namespace) -> int:
    report = Preflight(args.dest).run()
    lines = [f"[{'OK ' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in report.checks]
    _emit(args, report.as_dict(), "\n".join(lines))
    return EXIT_OK if report.ok else EXIT_ERROR


def _cmd_wizard(args: argparse.Namespace) -> int:
    context = WizardContext(
        dest=args.dest or "",
        profile_id=args.profile,
        headless=args.headless,
        dry_run=args.dry_run,
    )
    wizard = InstallationWizard(context)
    probe = wizard.probe_hardware_environment()
    if not probe.ok:
        print("Preflight fallido; revise los checks fallidos.", file=sys.stderr)
        return EXIT_ERROR
    wizard.select_mmu_profile()
    devices = wizard.detect_mcu_devices()
    context.log(f"MCUs detectados: {[d.as_dict() for d in devices]}")
    wizard.run_hardware_calibration_steps(["bowden", "toolhead", "encoder"])
    result = wizard.generate_and_install_configs(args.dest)
    _emit(args, result.as_dict(), json.dumps(result.as_dict(), indent=2))
    return EXIT_OK if result.success else EXIT_ERROR


def _cmd_generate(args: argparse.Namespace) -> int:
    context = WizardContext(dest=args.dest or "", profile_id=args.profile, dry_run=args.dry_run)
    wizard = InstallationWizard(context)
    wizard.select_mmu_profile()
    result = wizard.generate_and_install_configs(args.dest)
    _emit(args, result.as_dict(), json.dumps(result.as_dict(), indent=2))
    return EXIT_OK if result.success else EXIT_ERROR


def _cmd_apply(args: argparse.Namespace) -> int:
    context = WizardContext(dest=args.dest or "", profile_id=args.profile)
    wizard = InstallationWizard(context)
    wizard.select_mmu_profile()
    result = wizard.generate_and_install_configs(args.dest)
    _emit(args, result.as_dict(), json.dumps(result.as_dict(), indent=2))
    return EXIT_OK if result.success else EXIT_ERROR


def _cmd_validate(args: argparse.Namespace) -> int:
    wizard = InstallationWizard(WizardContext(dest=args.dest or "", headless=True))
    report = wizard.verify_system_integrity(args.dest)
    _emit(args, report.as_dict(), json.dumps(report.as_dict(), indent=2))
    return EXIT_OK if report.ok else EXIT_ERROR


def _cmd_rollback(args: argparse.Namespace) -> int:
    manager = RollbackManager(args.dest or ".")
    ok = manager.rollback(args.snapshot)
    payload = {"success": ok, "snapshot": args.snapshot or "pending"}
    _emit(args, payload, "rollback OK" if ok else "rollback fallido")
    return EXIT_OK if ok else EXIT_ERROR


def _cmd_migrate(args: argparse.Namespace) -> int:
    migrator = ConfigMigrator()
    bundle = migrator.migrate_from_happy_hare(args.source)
    payload = {"config_hash": bundle.config_hash, "files": sorted(bundle.files)}
    if args.dry_run:
        _emit(args, payload, json.dumps(payload, indent=2))
        return EXIT_OK
    if args.dest:
        ConfigGenerator().write_atomic_config_bundle(bundle, args.dest)
    _emit(args, payload, json.dumps(payload, indent=2))
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    validator = SystemValidator()
    payload = {
        "health": validator.perform_realtime_health_check().as_dict(),
        "cffi_integrity": validator.verify_cffi_bindings_integrity(),
        "versions": validator.validate_version_compatibility(
            {"klipper": "0.12.0", "moonraker": "0.9.0", "dog_matrix": VERSION}
        ).as_dict(),
    }
    _emit(args, payload, json.dumps(payload, indent=2))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
