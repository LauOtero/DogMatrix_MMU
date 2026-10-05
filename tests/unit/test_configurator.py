"""Pruebas del motor de configuracion tipo menuconfig (Kconfig)."""

from __future__ import annotations

from dog_matrix import boards, vendors
from installer.configurator import (
    Configurator,
    build_menus,
    configuration_profile,
    format_vendors,
    headless_configuration,
    resolve_configuration,
)


def test_vendor_menu_lists_all_vendors():
    configurator = Configurator(build_menus())
    vendor_option = next(option for option in configurator.all_options() if option.key == "vendor")
    for preset in vendors.list_vendors():
        assert preset.vendor_id in vendor_option.choices


def test_defaults_are_complete():
    configurator = Configurator(build_menus())
    defaults = configurator.defaults()
    assert defaults["vendor"] == "box_turtle"
    assert defaults["units"] == 1
    assert defaults["board"] == ""


def test_headless_resolves_vendor_board_and_gates():
    for vendor_id in ("box_turtle", "ercf_1_1", "vvd", "chameleon"):
        config = headless_configuration(vendor_id, units=1)
        assert config.vendor == vendor_id
        assert config.board in boards.list_boards()
        preset = vendors.get_vendor(vendor_id)
        assert config.gates == preset.gates_per_unit


def test_headless_multi_unit_chain():
    config = headless_configuration("box_turtle", units=3)
    assert config.units == 3
    assert config.gates == 12
    assert config.machine["total_gates"] == 12


def test_board_override_is_respected():
    config = headless_configuration("box_turtle", board="mellow_fly_mmu")
    assert config.board == "mellow_fly_mmu"


def test_configuration_profile_is_valid_and_has_board():
    config = headless_configuration("ercf_2_0")
    profile = configuration_profile(config)
    assert profile.hardware["board"] == config.board
    assert profile.gates == vendors.get_vendor("ercf_2_0").gates_per_unit


def test_search_finds_options():
    configurator = Configurator(build_menus())
    keys = {option.key for option in configurator.search("encoder")}
    assert "encoder" in keys
    assert configurator.search("multiplicar") or configurator.search("unidades")


def test_resolve_drops_unknown_and_keeps_defaults():
    configurator = Configurator(build_menus())
    resolved = configurator.resolve({"vendor": "kms", "garbage": 1})
    assert resolved["vendor"] == "kms"
    assert "garbage" not in resolved


def test_run_interactive_with_scripted_input():
    configurator = Configurator(build_menus())
    answers = iter(["kms", "2", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""])

    def fake_input(_prompt: str) -> str:
        try:
            return next(answers)
        except StopIteration:
            return ""

    resolved = configurator.run(input_fn=fake_input, output_fn=lambda _line: None)
    assert resolved["vendor"] == "kms"
    assert resolved["units"] == 2


def test_format_vendors_lists_ids():
    text = format_vendors()
    for vendor_id in ("box_turtle", "ercf_1_1", "qidi_box"):
        assert vendor_id in text
