"""Pruebas del sistema de configuracion de pines y registro de placas.

Las definiciones de placa se cargan una sola vez por sesion via ``lru_cache``
(son inmutables y de solo lectura), evitando re-parsear el YAML en cada test.
"""

from __future__ import annotations

from functools import lru_cache

import pytest

from dog_matrix import boards
from dog_matrix.boards import BoardDefinition

REQUIRED_BOARDS = [
    "mellow_fly_mmu",
    "mellow_fly_ercf_v1",
    "mellow_fly_ercf_v2",
    "ercf_easy_brd_v1_1",
    "fysetc_erb_v1",
    "fysetc_erb_v2",
]

SELECTOR_BOARDS = [
    "mellow_fly_ercf_v1",
    "mellow_fly_ercf_v2",
    "ercf_easy_brd_v1_1",
    "fysetc_erb_v1",
    "fysetc_erb_v2",
]

MMB_BOARDS = ["btt_mmb_can_v1_0", "btt_mmb_can_v1_1", "btt_mmb_can_v2_0"]


@lru_cache(maxsize=None)
def _load(board_id: str) -> BoardDefinition:
    return boards.load_board(board_id)


@lru_cache(maxsize=None)
def _load_all() -> dict:
    return boards.load_all_boards()


def test_all_required_boards_present():
    available = boards.list_boards()
    for board_id in REQUIRED_BOARDS:
        assert board_id in available, f"falta la placa {board_id}"


def test_all_boards_load_and_define_no_conflicts():
    for board_id, board in _load_all().items():
        assert board.board_id == board_id
        assert boards.validate_board_definition(board) == [], f"conflicto en {board_id}"


def test_all_boards_build_conflict_free_plans():
    for board in _load_all().values():
        topology = board.default_topology()
        plan = boards.build_pin_plan(board, topology=topology)
        assert boards.validate_pin_plan(plan) == [], f"conflicto de plan en {board.board_id}"


def test_mellow_fly_mmu_selector_aliases_match_happy_hare():
    board = _load("mellow_fly_mmu")
    plan = boards.build_pin_plan(board, topology=boards.TOPOLOGY_SELECTOR)
    expected = {
        "MMU_SEL_STEP": "PE14",
        "MMU_SEL_DIR": "PE13",
        "MMU_SEL_ENABLE": "!PE12",
        "MMU_SEL_UART": "PE11",
        "MMU_GEAR_STEP": "PC5",
        "MMU_GEAR_DIR": "PB1",
        "MMU_GEAR_ENABLE": "!PB0",
        "MMU_GEAR_UART": "PE7",
        "MMU_SEL_ENDSTOP": "PE2",
        "MMU_ENCODER": "^PE3",
        "MMU_GATE_SENSOR": "^PC3",
        "MMU_SERVO": "PA3",
        "MMU_CUT_SERVO": "PA2",
        "MMU_NEOPIXEL": "PE10",
        "MMU_PRE_GATE_0": "PC6",
        "MMU_PRE_GATE_7": "PD9",
    }
    for alias, pin in expected.items():
        assert plan.resolve(alias) == pin, f"{alias}: {plan.resolve(alias)!r} != {pin!r}"


def test_mirror_dm_aliases_are_emitted():
    plan = boards.build_pin_plan(_load("mellow_fly_mmu"))
    assert plan.resolve("DM_GEAR_STEP") == plan.resolve("MMU_GEAR_STEP")
    assert "DM_SEL_STEP" in plan.aliases


def test_dogmatrix_unit_reserves_four_coils():
    board = _load("mellow_fly_mmu")
    plan = boards.build_pin_plan(board, topology=boards.TOPOLOGY_GEAR_PER_GATE)
    assert plan.coils_per_unit == 4
    assert plan.gates == 4
    coils = plan.coils()
    assert len(coils) == 4
    for index, coil in enumerate(coils):
        for role in ("gear_step", "gear_dir", "gear_enable", "gear_uart"):
            assert role in coil, f"bobina {index} sin {role}"
    # Sufijos con coherencia Happy Hare: primero sin sufijo, resto indexado.
    assert plan.resolve("MMU_GEAR_STEP") == "PE14"
    assert plan.resolve("MMU_GEAR_STEP_1") == "PC5"
    assert plan.resolve("MMU_GEAR_STEP_3") == "PE4"
    control = plan.resource_summary()["control"]
    assert sum(1 for alias in control if alias.startswith("MMU_GEAR_STEP") and "UART" not in alias) == 4


def test_selector_gates_are_configurable():
    board = _load("fysetc_erb_v2")
    for gates in (4, 9, 12):
        plan = boards.build_pin_plan(board, gates=gates)
        assert plan.gates == gates
        assert boards.validate_pin_plan(plan) == []


def test_alias_uniformity_across_selector_boards():
    canonical = {"MMU_GEAR_STEP", "MMU_GEAR_DIR", "MMU_GEAR_ENABLE", "MMU_SEL_STEP", "MMU_SEL_DIR", "MMU_SEL_ENABLE"}
    for board_id in SELECTOR_BOARDS:
        board = _load(board_id)
        plan = boards.build_pin_plan(board, topology=boards.TOPOLOGY_SELECTOR)
        for alias in canonical:
            assert alias in plan.aliases, f"{board_id} no expone {alias}"


def test_shared_pins_are_not_reported_as_conflicts():
    # Mellow FLY-ERCF V2 comparte SEL_DIAG/SEL_ENDSTOP y SHARED_EXIT/PRE_GATE_2.
    plan = boards.build_pin_plan(_load("mellow_fly_ercf_v2"), gates=12)
    assert boards.validate_pin_plan(plan) == []


def test_duplicate_pin_is_detected():
    board = BoardDefinition(
        board_id="dupe",
        display_name="Dupe",
        topology=boards.TOPOLOGY_SELECTOR,
        drivers=[
            {"step": "PA0", "dir": "PA1", "enable": "PA2"},
            {"step": "PA0", "dir": "PA3", "enable": "PA4"},
        ],
    )
    conflicts = boards.validate_board_definition(board)
    assert len(conflicts) == 1
    assert conflicts[0].pin == "mmu:PA0"


def test_render_board_pins_block():
    plan = boards.build_pin_plan(_load("mellow_fly_mmu"))
    text = boards.render_board_pins(plan)
    assert "[board_pins dogmatrix]" in text
    assert "mcu: mmu" in text
    assert "MMU_GEAR_STEP=PC5" in text


def test_happy_hare_catalog_archetypes_exist():
    available = set(boards.list_boards())
    catalog = boards.happy_hare_board_catalog()
    types = {entry["board_type"] for entry in catalog}
    for required in ("EASY_BRD", "ERB_1", "ERB_2", "OTHER", "MANUAL"):
        assert required in types
    assert any(board_type.startswith("MELLOW_EASY_BRD") for board_type in types)
    for entry in catalog:
        assert entry["archetype"] in available, f"arquetipo inexistente: {entry['archetype']}"


def test_unsupported_schema_version_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("schema_version: 99\nboard_id: bad\ndisplay_name: bad\n", encoding="utf-8")
    with pytest.raises(boards.BoardError):
        boards.load_board(str(path))


def test_mmb_boards_expose_four_drivers_and_i2c():
    for board_id in MMB_BOARDS:
        board = _load(board_id)
        assert board.driver_count() == 4, board_id
        plan = boards.build_pin_plan(board, topology=boards.TOPOLOGY_GEAR_PER_GATE)
        assert len(plan.coils()) == 4, board_id
        assert plan.resolve("MMU_I2C_SCL") is not None
        assert plan.resolve("MMU_I2C_SDA") is not None
        assert plan.resolve("DM_I2C_SCL") == plan.resolve("MMU_I2C_SCL")
        assert boards.validate_pin_plan(plan) == [], board_id


def test_mmb_i2c_pins_by_version():
    v10 = boards.build_pin_plan(_load("btt_mmb_can_v1_0"))
    v11 = boards.build_pin_plan(_load("btt_mmb_can_v1_1"))
    v20 = boards.build_pin_plan(_load("btt_mmb_can_v2_0"))
    assert (v10.resolve("MMU_I2C_SCL"), v10.resolve("MMU_I2C_SDA")) == ("PB3", "PB4")
    assert (v11.resolve("MMU_I2C_SCL"), v11.resolve("MMU_I2C_SDA")) == ("PB3", "PB4")
    assert (v20.resolve("MMU_I2C_SCL"), v20.resolve("MMU_I2C_SDA")) == ("PC0", "PC1")
    assert v10.i2c_bus == "i2c3" and v20.i2c_bus == "i2c3"


def test_mmb_v1_0_vs_v1_1_driver0_enable_differs():
    v10 = boards.build_pin_plan(_load("btt_mmb_can_v1_0"))
    v11 = boards.build_pin_plan(_load("btt_mmb_can_v1_1"))
    assert v10.resolve("MMU_SEL_ENABLE") == "!PA8"
    assert v11.resolve("MMU_SEL_ENABLE") == "!PB8"


def test_i2c_only_exposed_by_supporting_boards():
    for board_id in MMB_BOARDS:
        assert _load(board_id).capabilities.get("i2c") is True
    for board_id in REQUIRED_BOARDS + SELECTOR_BOARDS:
        board = _load(board_id)
        assert board.capabilities.get("i2c") is False, board_id
        plan = boards.build_pin_plan(board)
        assert "MMU_I2C_SCL" not in plan.aliases, board_id


def test_pre_gate_is_dual_usable_as_post_gate():
    plan = boards.build_pin_plan(_load("mellow_fly_mmu"))
    assert plan.resolve("MMU_PRE_GATE_0") == "PC6"
    assert plan.resolve("MMU_POST_GATE_0") == "PC6"
    assert plan.resolve("MMU_POST_GATE_7") == plan.resolve("MMU_PRE_GATE_7")
    assert boards.validate_pin_plan(plan) == []


def test_boards_without_dual_sensors_have_no_post_gate():
    plan = boards.build_pin_plan(_load("ercf_easy_brd_v1_1"))
    assert "MMU_POST_GATE_0" not in plan.aliases


def test_mmb_i2c_devices_metadata():
    board = _load("btt_mmb_can_v2_0")
    assert board.dual_gate_sensors is True
    assert {"humidity_temperature", "multiplexer", "rfid"}.issubset(set(board.i2c_devices))
