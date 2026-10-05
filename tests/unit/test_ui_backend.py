"""Pruebas del backend de UI: panel KlipperScreen (F-34) y automap Moonraker (F-33)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from dog_matrix.klipperscreen_panel import KlipperScreenMMUPanel

ROOT = Path(__file__).resolve().parents[2]


def _load_moonraker_component():
    path = ROOT / "moonraker" / "components" / "dog_matrix.py"
    spec = importlib.util.spec_from_file_location("dm_moonraker_component", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def moonraker_component():
    return _load_moonraker_component()


def _status():
    return {
        "state": "IDLE",
        "gate": 1,
        "tool": 1,
        "gates": 3,
        "gate_status": ["available", "available", "empty"],
        "gate_filament": [
            {"material": "PLA", "color": "#ff0000", "spool_id": "1"},
            {"material": "ABS", "color": "#00ff00", "spool_id": "2"},
            {"material": "", "color": "", "spool_id": ""},
        ],
        "ttg_map": [0, 1, 2],
        "counters": {"toolchanges": 5},
        "counters_store": {"blade": {"value": 1, "limit": 10}},
        "toolchange_timings": {"total": 123.0},
        "flowguard": {"errors": 0},
        "environment": {"actual_temp": 40.0, "actual_humidity": 30.0, "drying": True},
    }


def test_panel_ttg_and_gate_editors():
    panel = KlipperScreenMMUPanel()
    ttg = panel.render_ttg_editor(_status())
    assert ttg["rows"][1]["gate"] == 1
    gates = panel.render_gate_editor(_status())
    assert gates["gates"][0]["color"] == "#ff0000"


def test_panel_maintenance_and_environment_views():
    panel = KlipperScreenMMUPanel()
    maintenance = panel.render_maintenance_view(_status())
    assert maintenance["counters"][0]["name"] == "blade"
    environment = panel.render_environment_view(_status())
    assert environment["temperature"] == 40.0
    assert environment["drying"] is True


def test_panel_stats_and_test_menu():
    panel = KlipperScreenMMUPanel()
    assert panel.render_stats_view(_status())["counters"]["toolchanges"] == 5
    assert "DM_TEST_MOVE" in panel.render_test_menu()["items"]


def test_moonraker_parse_and_automap(moonraker_component):
    gcode = "\n".join([
        "; filament_colour = #FF0000;#00FF00",
        "T0",
        "T1",
    ])
    parsed = moonraker_component.parse_gcode(gcode)
    assert parsed["referenced_tools"] == [0, 1]
    mapping = moonraker_component.build_automap({0: "#FF0000"}, ["#ff0000", "#0000ff"])
    assert mapping == {0: 0}


def test_moonraker_automap_avoids_reuse(moonraker_component):
    mapping = moonraker_component.build_automap(
        {0: "#FF0000", 1: "#FF0000"}, ["#ff0000", "#00ff00"]
    )
    assert mapping[0] != mapping[1]
