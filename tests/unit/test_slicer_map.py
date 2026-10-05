"""Pruebas del mapa de herramientas del slicer (TTG maps + automap)."""

from __future__ import annotations

from dog_matrix.slicer_map import (
    SLICER_MAP_UNSET,
    SlicerToolMap,
    colors_match,
    normalize_color,
)


def test_identity_map_and_out_of_range_tool():
    tool_map = SlicerToolMap(4)
    assert tool_map.as_tool_map() == [0, 1, 2, 3]
    assert tool_map.map_tool(0) == 0
    assert tool_map.map_tool(3) == 3
    assert tool_map.map_tool(4) == SLICER_MAP_UNSET
    assert tool_map.map_tool(-1) == SLICER_MAP_UNSET


def test_set_map_normalizes_invalid_entries():
    tool_map = SlicerToolMap(4)
    assert tool_map.set_map([2, -5, 99, "1", "x"]) == [2, SLICER_MAP_UNSET, SLICER_MAP_UNSET, 1, SLICER_MAP_UNSET]
    assert tool_map.map_tool(0) == 2
    assert tool_map.map_tool(3) == 1
    # Una entrada no-lista no altera el mapa vigente.
    assert tool_map.set_map("no-lista") == [2, SLICER_MAP_UNSET, SLICER_MAP_UNSET, 1, SLICER_MAP_UNSET]


def test_config_dict_and_configwrapper():
    from tests.fixtures.fakes import FakeConfig

    assert SlicerToolMap(3, {"slicer_tool_map": [2, 0]}).as_tool_map() == [2, 0]
    config = FakeConfig(values={"slicer_tool_map": [1, 2, 0]})
    assert SlicerToolMap(3, config).as_tool_map() == [1, 2, 0]


def test_automap_matches_color_exact_with_and_without_hash():
    tool_map = SlicerToolMap(3)
    assigned = tool_map.automap(
        {0: "#FF0000", 1: "00ff00", 2: "0000FF"},
        ["ff0000", "#00FF00", "0000ff"],
    )
    assert assigned == {0: 0, 1: 1, 2: 2}
    # El match no depende del orden: T0 cae en el gate que coincide por color.
    other = SlicerToolMap(2).automap({0: "ff0000"}, ["00ff00", "#FF0000"])
    assert other == {0: 1}


def test_automap_material_as_tie_break():
    tool_map = SlicerToolMap(2)
    assigned = tool_map.automap(
        {0: "#ffffff"},
        ["#ffffff", "#ffffff"],
        tool_materials={0: "PETG"},
        gate_materials=["PLA", "PETG"],
    )
    assert assigned == {0: 1}


def test_automap_material_as_fallback():
    tool_map = SlicerToolMap(2)
    assigned = tool_map.automap(
        {0: "#00ff00"},
        ["#ff0000", "#0000ff"],
        tool_materials={0: "PETG"},
        gate_materials=["PLA", "PETG"],
    )
    assert assigned == {0: 1}


def test_automap_does_not_reuse_gate():
    tool_map = SlicerToolMap(3)
    assigned = tool_map.automap(
        {0: "#ff0000", 1: "#ff0000"},
        ["#ff0000", "#00ff00", "#0000ff"],
    )
    assert assigned == {0: 0, 1: 1}
    assert len(set(assigned.values())) == len(assigned)


def test_get_status_mapped_and_unmapped():
    tool_map = SlicerToolMap(3)
    status = tool_map.get_status()
    assert status["map"] == [0, 1, 2]
    assert status["mapped"] == {"0": 0, "1": 1, "2": 2}
    assert status["unmapped"] == []

    tool_map.set_map([0, SLICER_MAP_UNSET, 2])
    status = tool_map.get_status()
    assert status["mapped"] == {"0": 0, "2": 2}
    assert status["unmapped"] == [1]


def test_reset_returns_to_identity():
    tool_map = SlicerToolMap(3)
    tool_map.set_map([2, 1, 0])
    tool_map.reset()
    assert tool_map.as_tool_map() == [0, 1, 2]


def test_normalize_color_and_colors_match():
    assert normalize_color("#FF0000") == "ff0000"
    assert normalize_color("FF0000") == "ff0000"
    assert normalize_color("#fff") == "fff"
    assert normalize_color("white") == "WHITE"
    assert normalize_color(" Light Blue ") == "LIGHTBLUE"
    assert normalize_color("#xyz") == "XYZ"

    assert colors_match("#fff", "FFF") is True
    assert colors_match("white", "WHITE") is True
    assert colors_match("red", "blue") is False
    assert colors_match("", "") is False
