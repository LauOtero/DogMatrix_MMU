"""Pruebas del catalogo de proveedores y de la configuracion multi-unidad."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dog_matrix import boards, vendors

LISTED_VENDORS = [
    "vvd",
    "box_turtle",
    "ercf_1_1",
    "ercf_2_0",
    "emu",
    "tradrack",
    "night_owl",
    "angry_beaver",
    "3ms",
    "quattrobox",
    "quattrobox_v2",
    "chameleon",
    "pico_mmu",
    "kms",
    "mmx",
    "qidi_box",
]


def _schema():
    path = Path(__file__).resolve().parents[2] / "profiles" / "schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_listed_vendors_present():
    for vendor_id in LISTED_VENDORS:
        assert vendors.get_vendor(vendor_id).vendor_id == vendor_id


def test_vendors_sorted_and_aliases_resolve():
    ids = [preset.vendor_id for preset in vendors.list_vendors()]
    assert ids == sorted(ids)
    assert vendors.get_vendor("BTT_VIVID").vendor_id == "vvd"
    assert vendors.get_vendor("ercf").vendor_id == "ercf_1_1"
    assert vendors.get_vendor("quattro_box_v2").vendor_id == "quattrobox_v2"


def test_unknown_vendor_raises():
    with pytest.raises(vendors.VendorError):
        vendors.get_vendor("does_not_exist")


def test_chained_gates_and_offsets():
    box = vendors.get_vendor("box_turtle")
    assert vendors.chained_gates(box, 1) == 4
    assert vendors.chained_gates(box, 3) == 12
    assert vendors.gate_offsets(box, 3) == [0, 4, 8]
    assert vendors.chained_gates(vendors.get_vendor("ercf_1_1"), 2) == 18


def test_machine_layout_is_contiguous():
    layout = vendors.build_machine_layout("box_turtle", 3)
    assert layout["total_gates"] == 12
    offsets = [entry["gate_offset"] for entry in layout["layout"]]
    assert offsets == [0, 4, 8]
    assert all(entry["gates"] == 4 for entry in layout["layout"])


def test_every_vendor_default_board_exists():
    available = set(boards.list_boards())
    for preset in vendors.list_vendors():
        assert preset.default_board in available, f"{preset.vendor_id}: {preset.default_board}"


def test_vendor_profiles_validate_against_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = _schema()
    validator = jsonschema.Draft7Validator(schema)
    for vendor_id in LISTED_VENDORS:
        profile = vendors.vendor_to_profile(vendor_id, units=1)
        errors = sorted(validator.iter_errors(profile.raw), key=lambda e: list(e.path))
        assert not errors, f"{vendor_id}: {[e.message for e in errors]}"


def test_vendor_profile_multi_unit_scales_gates():
    profile = vendors.vendor_to_profile("box_turtle", units=2)
    assert profile.topology["units"] == 2
    assert profile.gates == 8
    assert len(profile.hardware["mcu"]) == 2


def test_capabilities_are_derived_from_topology():
    box = vendors.get_vendor("box_turtle").resolved_capabilities()
    assert box["selector"] is False
    ercf = vendors.get_vendor("ercf_1_1").resolved_capabilities()
    assert ercf["selector"] is True
    assert ercf["servo"] is True
