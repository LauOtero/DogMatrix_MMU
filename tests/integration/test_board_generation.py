"""Pruebas de integracion del sistema de pines con generador, validador y unidades."""

from __future__ import annotations

import pytest

from dog_matrix.capabilities import Capabilities, find_profile
from dog_matrix.units import DeviceScanner, DiscoveredDevice, DogMatrixController
from installer.generator import ConfigGenerator
from installer.validator import SystemValidator

pytestmark = pytest.mark.slow

REQUIRED_BOARDS = [
    "mellow_fly_mmu",
    "mellow_fly_ercf_v1",
    "mellow_fly_ercf_v2",
    "ercf_easy_brd_v1_1",
    "fysetc_erb_v1",
    "fysetc_erb_v2",
]


def _profile(name="ercf"):
    return Capabilities(str(find_profile(name))).load()


def test_generator_emits_board_pin_aliases():
    generator = ConfigGenerator(generated_at="2026-01-01T00:00:00Z")
    bundle = generator.build_bundle(_profile("ercf"), {"board": "mellow_fly_ercf_v2"})
    cfg = bundle.files["dog_matrix_generated.cfg"]
    assert "[board_pins dogmatrix]" in cfg
    assert "MMU_GEAR_STEP=gpio7" in cfg
    assert "DM_GEAR_STEP=gpio7" in cfg
    assert "MMU_SEL_STEP=gpio4" in cfg
    assert "${" not in cfg


def test_generator_without_board_uses_direct_pins():
    generator = ConfigGenerator(generated_at="2026-01-01T00:00:00Z")
    bundle = generator.build_bundle(_profile("box_turtle"))
    cfg = bundle.files["dog_matrix_generated.cfg"]
    assert "[board_pins dogmatrix]" not in cfg
    assert "filament_gate_0" in cfg


def test_generator_board_output_is_deterministic():
    generator = ConfigGenerator(generated_at="2026-01-01T00:00:00Z")
    first = generator.build_bundle(_profile("ercf"), {"board": "mellow_fly_ercf_v2"})
    second = generator.build_bundle(_profile("ercf"), {"board": "mellow_fly_ercf_v2"})
    assert first.config_hash == second.config_hash


def test_validator_accepts_all_required_boards():
    validator = SystemValidator()
    for board_id in REQUIRED_BOARDS:
        result = validator.validate_board_plan(board_id)
        assert result.passed, f"{board_id}: {result.errors}"


class _StaticScanner(DeviceScanner):
    def __init__(self, devices):
        self._devices = list(devices)

    def scan(self):
        return list(self._devices)


def test_dogmatrix_unit_defaults_to_four_coils(tmp_path):
    scanner = _StaticScanner(
        [
            DiscoveredDevice("usb:dogmatrix-1", "usb"),
            DiscoveredDevice("usb:dogmatrix-2", "usb"),
        ]
    )
    controller = DogMatrixController(
        scanners=[scanner],
        store_path=str(tmp_path / "units.json"),
        default_gates=0,
        coils_per_unit=4,
    )
    controller.poll()
    units = controller.list_units()
    assert [unit.gates for unit in units] == [4, 4]
    assert [unit.gate_offset for unit in units] == [0, 4]
    assert units[0].as_dict()["coils_per_unit"] == 4


def test_cli_generate_with_vendor_multi_unit(tmp_path):
    from installer.cli import main

    code = main(
        [
            "generate",
            "--vendor",
            "box_turtle",
            "--units",
            "2",
            "--dest",
            str(tmp_path),
            "--dry-run",
            "--json",
        ]
    )
    assert code == 0


def test_cli_list_vendors():
    from installer.cli import main

    assert main(["list-vendors"]) == 0


def test_wizard_vendor_generates_machine_and_pins(tmp_path):
    from installer.wizard import InstallationWizard, WizardContext

    wizard = InstallationWizard(
        WizardContext(dest=str(tmp_path), vendor="box_turtle", board="mellow_fly_mmu", units=2)
    )
    wizard.select_mmu_profile()
    result = wizard.generate_and_install_configs(str(tmp_path))
    assert result.success
    cfg = (tmp_path / "dog_matrix_generated.cfg").read_text(encoding="utf-8")
    assert "[dm_machine]" in cfg
    assert "total_gates: 8" in cfg
    assert "[board_pins dogmatrix0]" in cfg
    assert "[board_pins dogmatrix1]" in cfg
