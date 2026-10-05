"""Verifica la generacion de configuracion para TODOS los MMU/ERCF soportados.

- Vendors del catalogo (Happy Hare v4): 16 presets.
- Perfiles de ``profiles/*.yaml``: box_turtle, ercf, emu, night_owl, quattrobox, tradrack.

Cada caso genera la configuracion con el wizard (headless), comprueba los
ficheros, el contenido minimo, el snapshot del perfil y el determinismo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dog_matrix import vendors
from installer.simulator import REQUIRED_FILES, InstallerSimulator, list_profiles
from installer.validator import SystemValidator

VENDOR_IDS = [preset.vendor_id for preset in vendors.list_vendors()]
PROFILE_IDS = list_profiles()


def _assert_bundle(dest: Path, gates: int) -> None:
    for name in REQUIRED_FILES:
        assert (dest / name).exists(), f"falta {name} en {dest}"
    cfg = (dest / "dog_matrix_generated.cfg").read_text(encoding="utf-8")
    assert "[dm_pins]" in cfg
    assert "[dm_calibration]" in cfg
    assert "# Gates:" in cfg and str(gates) in cfg
    snapshot = json.loads((dest / "dog_matrix_profile.json").read_text(encoding="utf-8"))
    assert snapshot["topology"]["gates"] == gates
    manifest = json.loads((dest / "dog_matrix_manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_hash"].startswith("sha256:")
    assert set(manifest["files"]) >= {"dog_matrix_generated.cfg", "dog_matrix_macros.cfg"}


# --- Vendors (MMU/ERCF soportados) -----------------------------------------
@pytest.mark.parametrize("vendor_id", VENDOR_IDS)
def test_generate_config_for_each_vendor(tmp_path, vendor_id):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    payload = simulator.wizard(vendor=vendor_id, units=1)
    assert payload["success"], payload.get("error") or payload.get("message")
    preset = vendors.get_vendor(vendor_id)
    assert payload["gates"] == preset.gates_per_unit
    assert payload["board"] == preset.default_board
    _assert_bundle(Path(payload["dest"]), preset.gates_per_unit)


@pytest.mark.parametrize("vendor_id", VENDOR_IDS)
def test_vendor_default_board_plan_is_valid(vendor_id):
    preset = vendors.get_vendor(vendor_id)
    result = SystemValidator().validate_board_plan(preset.default_board)
    assert result.passed, f"{vendor_id}: {result.errors}"


def test_all_vendors_catalog_has_expected_erfc_and_mmus():
    ids = set(VENDOR_IDS)
    # Al menos un ERCF de cada version y los MMU mas comunes deben existir.
    assert {"ercf_1_1", "ercf_2_0", "box_turtle", "tradrack", "vvd", "emu"} <= ids
    assert len(ids) == 16


# --- Perfiles config/*.yaml -------------------------------------------------
@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_generate_config_for_each_profile(tmp_path, profile_id):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    payload = simulator.wizard(profile=profile_id, units=1)
    assert payload["success"], payload.get("error") or payload.get("message")
    dest = Path(payload["dest"])
    for name in REQUIRED_FILES:
        assert (dest / name).exists(), f"falta {name}"
    cfg = (dest / "dog_matrix_generated.cfg").read_text(encoding="utf-8")
    assert "[dm_pins]" in cfg and "[dm_calibration]" in cfg


# --- Multi-unidad -----------------------------------------------------------
@pytest.mark.parametrize("units", [2, 3])
def test_multi_unit_generation_emits_machine_block(tmp_path, units):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    payload = simulator.wizard(vendor="box_turtle", units=units)
    assert payload["success"]
    cfg = (Path(payload["dest"]) / "dog_matrix_generated.cfg").read_text(encoding="utf-8")
    assert "[dm_machine]" in cfg
    assert f"units: {units}" in cfg
    assert f"total_gates: {units * 4}" in cfg
    assert "[board_pins dogmatrix0]" in cfg


# --- Determinismo -----------------------------------------------------------
def test_generation_is_deterministic(tmp_path):
    first = InstallerSimulator(base_dir=str(tmp_path)).wizard(vendor="ercf_2_0")
    second = InstallerSimulator(base_dir=str(tmp_path)).wizard(vendor="ercf_2_0")
    assert first["config_hash"] == second["config_hash"]


# --- Simulacion masiva ------------------------------------------------------
def test_simulate_all_vendors_and_profiles(tmp_path):
    summary = InstallerSimulator(base_dir=str(tmp_path)).simulate_all()
    assert summary["ok"], summary["failures"]
    assert summary["count"] == len(VENDOR_IDS) + len(PROFILE_IDS)
    assert not summary["failures"]
