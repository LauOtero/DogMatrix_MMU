"""Pruebas del preprocesador de G-code del componente Moonraker."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parents[2] / "moonraker" / "components" / "dog_matrix.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("dm_moonraker_component", _COMPONENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeRequest:
    def __init__(self, args):
        self._args = args

    def get_argument(self, key, default=None):
        return self._args.get(key, default)


class _FakeConfig:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)


# --- parse_gcode ------------------------------------------------------------
def test_parse_empty_gcode():
    module = _load_module()
    result = module.parse_gcode("")
    assert result == {"referenced_tools": [], "total_toolchanges": 0, "colors": [], "temperatures": []}


def test_parse_single_tool():
    module = _load_module()
    result = module.parse_gcode("G28\nT0\nG1 X10\n")
    assert result["referenced_tools"] == [0]
    assert result["total_toolchanges"] == 1


def test_parse_multiple_tools_and_toolchanges():
    module = _load_module()
    gcode = "T0\nG1 E5\nT1\nT2\nT0\n"
    result = module.parse_gcode(gcode)
    assert result["referenced_tools"] == [0, 1, 2]
    assert result["total_toolchanges"] == 4


def test_consecutive_same_tool_counts_once():
    module = _load_module()
    result = module.parse_gcode("T1\nT1\nT1\n")
    assert result["referenced_tools"] == [1]
    assert result["total_toolchanges"] == 1


def test_parse_temperatures():
    module = _load_module()
    result = module.parse_gcode("M104 S210\nM109 S215\nM104 S210\n")
    assert result["temperatures"] == [210, 215]


def test_parse_slicer_colors():
    module = _load_module()
    gcode = "; filament_colour = #FF0000;#00FF00;#0000FF\nT0\n"
    result = module.parse_gcode(gcode)
    assert result["colors"] == ["#FF0000", "#00FF00", "#0000FF"]


def test_inline_comments_ignored():
    module = _load_module()
    gcode = "G1 X0 ;T5 no es una herramienta\nT1 ; seleccion real\n"
    result = module.parse_gcode(gcode)
    assert result["referenced_tools"] == [1]
    assert result["total_toolchanges"] == 1


# --- handle_preprocess ------------------------------------------------------
def test_handle_preprocess_with_content():
    module = _load_module()
    component = object.__new__(module.DogMatrixComponent)
    request = _FakeRequest({"content": "T0\nT1\nM104 S200\n"})
    result = asyncio.run(component.handle_preprocess(request))
    assert result["ok"] is True
    assert result["referenced_tools"] == [0, 1]
    assert result["temperatures"] == [200]


def test_handle_preprocess_without_content_returns_error():
    module = _load_module()
    component = object.__new__(module.DogMatrixComponent)
    result = asyncio.run(component.handle_preprocess(_FakeRequest({})))
    assert result["ok"] is False
    assert "content" in result["error"]


def test_handle_preprocess_reads_path_within_root(tmp_path):
    module = _load_module()
    component = object.__new__(module.DogMatrixComponent)
    component.config = _FakeConfig({"gcode_root": str(tmp_path)})
    job = tmp_path / "job.gcode"
    job.write_text("T2\nT2\n", encoding="utf-8")
    result = asyncio.run(component.handle_preprocess(_FakeRequest({"path": str(job)})))
    assert result["ok"] is True
    assert result["referenced_tools"] == [2]


def test_handle_preprocess_rejects_path_outside_root(tmp_path):
    module = _load_module()
    component = object.__new__(module.DogMatrixComponent)
    root = tmp_path / "root"
    root.mkdir()
    component.config = _FakeConfig({"gcode_root": str(root)})
    outside_dir = tmp_path / "other"
    outside_dir.mkdir()
    outside = outside_dir / "job.gcode"
    outside.write_text("T1\n", encoding="utf-8")
    result = asyncio.run(component.handle_preprocess(_FakeRequest({"path": str(outside)})))
    assert result["ok"] is False
