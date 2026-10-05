"""Configuracion comun de pytest y fixtures compartidas."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for candidate in (ROOT, ROOT / "klippy" / "extras", ROOT / ".dm_pylibs"):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from tests.fixtures.fakes import FakeConfig, FakePrinter  # noqa: E402


@pytest.fixture()
def fake_printer() -> FakePrinter:
    return FakePrinter()


@pytest.fixture()
def make_core(tmp_path):
    """Fabrica un DogMatrixCore con hardware simulado y rutas temporales.

    Notas de rendimiento/determinismo:
    - ``log_level="warning"`` evita la E/S de disco por cada transicion de la FSM
      (los tests que validan logs de ``debug``/``info`` configuran su propio
      ``log_level`` explicitamente).
    - El estado persistente y los logs viven en ``tmp_path`` (sin tocar disco
      compartido) para que la suite sea reproducible y aislada.
    """
    from dog_matrix.core import DogMatrixCore

    def _make(profile: str = "box_turtle", toolhead_present: bool = False, **overrides):
        values = {
            "profile": profile,
            "state_store": str(tmp_path / "dog_matrix_state.json"),
            "log_path": str(tmp_path / "logs" / "dog_matrix.jsonl"),
            "evidence_dir": str(tmp_path / "evidence"),
            "log_level": "warning",
            "enable_led": False,
            "enable_spoolman": False,
            "enable_nfc": False,
            "enable_purge": False,
            "auto_recover": False,
        }
        values.update(overrides)
        printer = FakePrinter()
        config = FakeConfig(printer=printer, values=values)
        core = DogMatrixCore(config)
        printer.objects["dog_matrix"] = core
        if toolhead_present:
            core.sensors.set_simulated_state("toolhead", True)
        return core, printer

    return _make


@pytest.fixture()
def dest_dir(tmp_path):
    target = tmp_path / "config"
    target.mkdir(parents=True, exist_ok=True)
    return target
