"""Simulador del instalador/wizard/configurador de Dog Matrix MMU.

Ejecuta de forma **interactiva o por script** el mismo flujo que el instalador
real (``preflight`` -> ``menuconfig``/``wizard`` -> generacion -> verificacion),
usando directorios temporales por defecto para no tocar la configuracion real.
Permite **ver** los archivos generados y **comprobar** que todo funciona.

Uso:
    py -3 -m installer.simulator
    py -3 -m installer.simulator --vendor box_turtle --units 2
    py -3 -m installer.simulator --all --json
    py -3 -m installer.simulator --script escenario.txt

Comandos disponibles en la consola: ``help``, ``list-vendors``, ``list-boards``,
``list-profiles``, ``preflight``, ``menuconfig``, ``wizard``, ``generate``,
``simulate-all``, ``verify``, ``ls``, ``show <fichero>``, ``dest``, ``exit``.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import project_root
from .configurator import (
    build_menus,
    configuration_profile,
    headless_configuration,
    resolve_configuration,
)
from .generator import ConfigGenerator
from .preflight import Preflight
from .validator import SystemValidator
from .wizard import InstallationWizard, WizardContext

SIM_GENERATED_AT = "2026-01-01T00:00:00Z"
REQUIRED_FILES = (
    "dog_matrix_generated.cfg",
    "dog_matrix_macros.cfg",
    "dog_matrix_profile.json",
    "moonraker_dog_matrix.conf",
    "dog_matrix_manifest.json",
)


def _dog_matrix_modules() -> Tuple[Any, Any]:
    """Importa ``boards`` y ``vendors`` del runtime (sin depender de Klipper)."""
    import sys

    extras = project_root() / "klippy" / "extras"
    if str(extras) not in sys.path:
        sys.path.insert(0, str(extras))
    from dog_matrix import boards, vendors  # type: ignore

    return boards, vendors


def list_profiles() -> List[str]:
    """Perfiles disponibles en ``profiles/*.yaml`` (excluye el de ejemplo)."""
    profiles = project_root() / "profiles"
    result = [path.stem for path in sorted(profiles.glob("*.yaml"))]
    return [name for name in result if "example" not in name]


class InstallerSimulator:
    """Orquesta y simula el instalador/wizard/configurador."""

    def __init__(
        self,
        base_dir: Optional[str] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.base_dir = Path(base_dir).expanduser() if base_dir else Path(
            tempfile.mkdtemp(prefix="dm_sim_")
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.log = log or (lambda message: None)
        self._counter = 0
        self._last_dest: Optional[Path] = None

    # -- Consultas ----------------------------------------------------------
    def list_vendors(self) -> List[Dict[str, Any]]:
        _, vendors = _dog_matrix_modules()
        return [preset.as_dict() for preset in vendors.list_vendors()]

    def list_boards(self) -> List[str]:
        boards, _ = _dog_matrix_modules()
        return list(boards.list_boards())

    def list_profiles(self) -> List[str]:
        return list_profiles()

    # -- Directorios --------------------------------------------------------
    def new_dest(self, label: str = "out") -> Path:
        """Crea un subdirectorio unico para una simulacion."""
        self._counter += 1
        target = self.base_dir / f"{label}_{self._counter:02d}"
        target.mkdir(parents=True, exist_ok=True)
        return target

    # -- Flujo del instalador ----------------------------------------------
    def preflight(self, dest: Optional[str] = None) -> Dict[str, Any]:
        report = Preflight(dest or str(self.base_dir)).run()
        return report.as_dict()

    def menuconfig(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Resuelve la configuracion como lo haria el menuconfig interactivo."""
        from .configurator import Configurator

        resolved = Configurator(build_menus()).resolve(overrides or {})
        return resolve_configuration(resolved).as_dict()

    def wizard(
        self,
        vendor: Optional[str] = None,
        profile: Optional[str] = None,
        board: Optional[str] = None,
        units: int = 1,
        overrides: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        dest: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ejecuta el wizard completo (seleccion -> generacion -> verificacion)."""
        target = Path(dest) if dest else self.new_dest(vendor or profile or "wizard")
        self._last_dest = target
        context = WizardContext(
            dest=str(target),
            profile_id=profile or vendor or "box_turtle",
            vendor=vendor,
            board=board,
            units=max(1, int(units)),
            headless=True,
            dry_run=dry_run,
            generated_at=SIM_GENERATED_AT,
            log=self.log,
        )
        wizard = InstallationWizard(context)
        try:
            if vendor:
                # ``select_mmu_profile`` resuelve el vendor y, ademas, permite
                # aplicar overrides del menuconfig.
                wizard.select_mmu_profile()
                if overrides:
                    resolved = headless_configuration(
                        vendor, units=max(1, int(units)), board=board, overrides=overrides
                    )
                    wizard._resolved = resolved
                    wizard._profile = configuration_profile(resolved)
            else:
                wizard.select_mmu_profile(profile or "box_turtle")
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
                "dest": str(target),
                "vendor": vendor,
                "profile": profile,
            }
        result = wizard.generate_and_install_configs(str(target))
        payload = result.as_dict()
        payload["dest"] = str(target)
        payload["vendor"] = vendor
        payload["profile"] = getattr(wizard._profile, "profile_id", None) or profile or vendor or "box_turtle"
        payload["units"] = max(1, int(units))
        payload["gates"] = getattr(wizard._profile, "gates", None)
        payload["board"] = board or getattr(wizard._profile, "hardware", {}).get("board")
        return payload

    def generate(self, **kwargs: Any) -> Dict[str, Any]:
        """Alias de ``wizard`` (generacion sin calibracion interactiva)."""
        return self.wizard(**kwargs)

    def verify(self, dest: Optional[str] = None) -> Dict[str, Any]:
        target = Path(dest) if dest else (self._last_dest or self.base_dir)
        wizard = InstallationWizard(
            WizardContext(dest=str(target), headless=True, generated_at=SIM_GENERATED_AT)
        )
        return wizard.verify_system_integrity(str(target)).as_dict()

    # -- Simulacion masiva --------------------------------------------------
    def simulate_all(self, dest: Optional[str] = None) -> Dict[str, Any]:
        """Genera configuracion para TODOS los vendors y perfiles soportados."""
        _, vendors = _dog_matrix_modules()
        results: List[Dict[str, Any]] = []
        failures: List[str] = []

        for preset in vendors.list_vendors():
            target = Path(dest) / "vendors" / preset.vendor_id if dest else self.new_dest(
                f"vendor_{preset.vendor_id}"
            )
            payload = self.wizard(vendor=preset.vendor_id, units=1, dest=str(target))
            payload["kind"] = "vendor"
            results.append(payload)
            if not payload.get("success"):
                failures.append(f"vendor:{preset.vendor_id}")

        for name in self.list_profiles():
            target = Path(dest) / "profiles" / name if dest else self.new_dest(f"profile_{name}")
            payload = self.wizard(profile=name, units=1, dest=str(target))
            payload["kind"] = "profile"
            results.append(payload)
            if not payload.get("success"):
                failures.append(f"profile:{name}")

        return {
            "ok": not failures,
            "count": len(results),
            "failures": failures,
            "results": results,
        }

    # -- Inspeccion de resultados ------------------------------------------
    def ls(self, dest: Optional[str] = None) -> List[str]:
        target = Path(dest) if dest else self.base_dir
        if not target.exists():
            return []
        return sorted(str(path.relative_to(target)) for path in target.rglob("*") if path.is_file())

    def show(self, name: str, dest: Optional[str] = None, max_lines: int = 400) -> str:
        target = Path(dest) if dest else self.base_dir
        candidates = sorted(target.rglob(Path(name).name))
        if not candidates:
            return f"(no encontrado: {name})"
        path = candidates[0]
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        header = f"===== {path} ====="
        body = lines[:max_lines]
        suffix = "" if len(lines) <= max_lines else f"\n... ({len(lines) - max_lines} lineas mas)"
        return "\n".join([header, *body]) + suffix

    # -- Consola / script ---------------------------------------------------
    def execute(self, line: str) -> List[str]:
        """Ejecuta una linea de la consola y devuelve las respuestas."""
        tokens = line.strip().split()
        if not tokens or tokens[0].startswith("#"):
            return []
        command = tokens[0].lower()
        kwargs, positional = _parse_args(tokens[1:])

        if command in ("exit", "quit"):
            return ["__EXIT__"]
        if command == "help":
            return [__doc__.split("Uso:", 1)[-1].strip()]
        if command == "list-vendors":
            return [
                f"{v['vendor_id']:<14} {v['display_name']:<18} gates/u={v['gates_per_unit']:<2} "
                f"topologia={v['topology_type']:<13} placa={v['default_board']}"
                for v in self.list_vendors()
            ]
        if command == "list-boards":
            return self.list_boards()
        if command == "list-profiles":
            return self.list_profiles()
        if command == "preflight":
            report = self.preflight(kwargs.get("dest"))
            return [f"[{'OK' if report.get('ok') else 'FAIL'}] preflight ({len(report.get('checks', []))} checks)"]
        if command == "menuconfig":
            return [json.dumps(self.menuconfig(kwargs), indent=2, sort_keys=True)]
        if command in ("wizard", "generate"):
            payload = self.wizard(**_wizard_kwargs(kwargs))
            return _format_deployment(payload)
        if command == "simulate-all":
            return _format_simulation(self.simulate_all(kwargs.get("dest")))
        if command == "verify":
            return [json.dumps(self.verify(kwargs.get("dest")), indent=2, sort_keys=True)]
        if command == "ls":
            return self.ls(kwargs.get("dest")) or ["(vacio)"]
        if command == "show":
            if not positional:
                return ["uso: show <fichero>"]
            return self.show(positional[0], kwargs.get("dest")).splitlines()
        if command == "dest":
            lines = [f"base   : {self.base_dir}"]
            if self._last_dest is not None:
                lines.append(f"ultimo : {self._last_dest}")
            return lines
        return [f"Comando desconocido: {command} (usa 'help')"]

    def run_console(self, stdin: Any = None, stdout: Any = None) -> None:
        """Bucle interactivo de consola."""
        import sys

        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        stdout.write("Dog Matrix — simulador del instalador. Escribe 'help' o 'exit'.\n")
        stdout.write(f"Directorio de trabajo: {self.base_dir}\n")
        for raw in stdin:
            for response in self.execute(raw):
                if response == "__EXIT__":
                    stdout.write("Saliendo.\n")
                    return
                stdout.write(response + "\n")
            stdout.flush()


def _parse_args(tokens: List[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Parsea argumentos ``key=value`` y sueltos (posicionales)."""
    kwargs: Dict[str, Any] = {}
    positional: List[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            key = token[2:]
            if "=" in key:
                key, _, value = key.partition("=")
                kwargs[key] = value
            elif index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
                kwargs[key] = tokens[index + 1]
                index += 1
            else:
                kwargs[key] = True
        elif "=" in token:
            key, _, value = token.partition("=")
            kwargs[key] = value
        else:
            positional.append(token)
        index += 1
    return kwargs, positional


def _wizard_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    for key in ("encoder", "gate_sensors", "dual_gate_sensors", "toolhead_sensor", "sync_feedback",
                "led", "espooler", "nfc", "cutter", "spoolman", "endless_spool", "connection"):
        if key in kwargs:
            overrides[key] = _as_bool(kwargs[key])
    for key in ("bowden_length_mm", "max_load_speed_mm_s", "max_unload_speed_mm_s"):
        if key in kwargs:
            overrides[key] = int(kwargs[key])
    return {
        "vendor": kwargs.get("vendor"),
        "profile": kwargs.get("profile"),
        "board": kwargs.get("board"),
        "units": int(kwargs.get("units", 1)),
        "overrides": overrides or None,
        "dry_run": _as_bool(kwargs.get("dry_run", False)),
        "dest": kwargs.get("dest"),
    }


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "si", "s")


def _format_deployment(payload: Dict[str, Any]) -> List[str]:
    if not payload.get("success"):
        return [f"FALLO: {payload.get('error') or payload.get('message')}"]
    return [
        f"OK -> {payload.get('dest')}",
        f"  perfil   : {payload.get('profile')} (vendor={payload.get('vendor')}, "
        f"gates={payload.get('gates')}, board={payload.get('board')})",
        f"  hash     : {payload.get('config_hash')}",
        f"  ficheros : {', '.join(payload.get('files', []))}",
    ]


def _format_simulation(summary: Dict[str, Any]) -> List[str]:
    lines = [f"Simulacion: {summary['count']} configuraciones, ok={summary['ok']}"]
    for item in summary["results"]:
        lines.append(
            f"  [{'OK ' if item.get('success') else 'FAIL'}] {item.get('kind')}:"
            f"{item.get('vendor') or item.get('profile')} -> {item.get('dest')} "
            f"({item.get('config_hash', '')})"
        )
    if summary["failures"]:
        lines.append(f"Fallos: {summary['failures']}")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="dm-sim", description="Simulador del instalador Dog Matrix")
    parser.add_argument("--vendor", default=None, help="preset de MMU/ERCF")
    parser.add_argument("--profile", default=None, help="perfil de profiles/*.yaml")
    parser.add_argument("--board", default=None, help="forzar placa")
    parser.add_argument("--units", type=int, default=1, help="unidades encadenadas")
    parser.add_argument("--dry-run", action="store_true", help="no escribir ficheros")
    parser.add_argument("--all", action="store_true", help="simular todos los vendors y perfiles")
    parser.add_argument("--dest", default=None, help="directorio base de la simulacion")
    parser.add_argument("--json", action="store_true", help="salida JSON")
    parser.add_argument("--command", action="append", default=[], help="comando de consola")
    parser.add_argument("--script", default=None, help="fichero con comandos (uno por linea)")
    args = parser.parse_args(argv)

    # En modo --json la salida debe ser JSON puro (sin logs intercalados).
    log = (lambda message: print(message)) if not args.json else (lambda message: None)
    simulator = InstallerSimulator(base_dir=args.dest, log=log)
    if args.all:
        summary = simulator.simulate_all(args.dest)
        if args.json:
            print(json.dumps(_json_safe(summary), indent=2, sort_keys=True))
        else:
            for line in _format_simulation(summary):
                print(line)
        return 0 if summary["ok"] else 1

    if args.script:
        try:
            lines = Path(args.script).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            print(f"No se pudo leer el script: {exc}")
            return 2
        output: List[str] = []
        for line in lines:
            output.extend(simulator.execute(line))
        _print_output(output, args.json)
        return 0

    if args.command:
        output = []
        for command in args.command:
            output.extend(simulator.execute(command))
        _print_output(output, args.json)
        return 0

    if args.vendor or args.profile:
        tokens = ["wizard"]
        if args.vendor:
            tokens.append(f"vendor={args.vendor}")
        if args.profile:
            tokens.append(f"profile={args.profile}")
        if args.board:
            tokens.append(f"board={args.board}")
        tokens.append(f"units={args.units}")
        if args.dry_run:
            tokens.append("dry_run=1")
        if args.dest:
            tokens.append(f"dest={args.dest}")
        _print_output(simulator.execute(" ".join(tokens)), args.json)
        return 0

    simulator.run_console()
    return 0


def _print_output(output: List[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps({"output": output}, indent=2))
        return
    for line in output:
        print(line)


def _json_safe(payload: Any) -> Any:
    return json.loads(json.dumps(payload, default=str))


if __name__ == "__main__":  # pragma: no cover - entrypoint manual
    raise SystemExit(main())


__all__ = ["InstallerSimulator", "main", "list_profiles", "REQUIRED_FILES"]
