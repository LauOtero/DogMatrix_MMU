"""Pruebas de los envoltorios finos sobre objetos de Klipper."""

from __future__ import annotations

from types import SimpleNamespace

from dog_matrix.klipper_wrappers import (
    ExtruderWrapper,
    ToolheadWrapper,
    build_extruder,
    build_toolhead,
)


# --- ToolheadWrapper -------------------------------------------------------
def test_toolhead_wrapper_position():
    toolhead = SimpleNamespace(get_position=lambda: (1.0, 2.0, 3.0, 4.0))
    wrapper = ToolheadWrapper(toolhead=toolhead)
    assert wrapper.position() == (1.0, 2.0, 3.0, 4.0)


def test_toolhead_wrapper_position_without_toolhead():
    assert ToolheadWrapper().position() is None
    assert ToolheadWrapper(toolhead=SimpleNamespace()).position() is None


def test_toolhead_wrapper_is_homed_from_status():
    toolhead = SimpleNamespace(get_status=lambda _eventtime: {"homed_axes": "xyz"})
    assert ToolheadWrapper(toolhead=toolhead).is_homed() is True


def test_toolhead_wrapper_is_homed_false_by_default():
    toolhead = SimpleNamespace(get_status=lambda _eventtime: {"homed_axes": ""})
    assert ToolheadWrapper(toolhead=toolhead).is_homed() is False
    assert ToolheadWrapper().is_homed() is False


def test_toolhead_wrapper_wait_moves():
    calls = []
    toolhead = SimpleNamespace(wait_moves=lambda: calls.append(True))
    ToolheadWrapper(toolhead=toolhead).wait_moves()
    assert calls == [True]
    ToolheadWrapper().wait_moves()  # no debe lanzar


def test_toolhead_wrapper_defends_against_broken_toolhead():
    def boom():
        raise RuntimeError("toolhead roto")

    toolhead = SimpleNamespace(get_position=boom, wait_moves=boom)
    wrapper = ToolheadWrapper(toolhead=toolhead)
    assert wrapper.position() is None
    wrapper.wait_moves()  # no debe lanzar


# --- ExtruderWrapper -------------------------------------------------------
def test_extruder_wrapper_position_and_temperature():
    extruder = SimpleNamespace(get_position=lambda: (0.0, 0.0, 0.0, 12.5))
    heater = SimpleNamespace(get_temp=lambda: (210.0, 205.0))
    wrapper = ExtruderWrapper(extruder=extruder, heater=heater)
    assert wrapper.position_mm() == 12.5
    assert wrapper.temperature() == 210.0


def test_extruder_wrapper_defaults():
    wrapper = ExtruderWrapper()
    assert wrapper.position_mm() == 0.0
    assert wrapper.temperature() == 0.0


# --- Factorias -------------------------------------------------------------
def test_build_helpers_resolve_objects():
    toolhead = SimpleNamespace(get_position=lambda: (1.0, 2.0, 3.0, 4.0))
    extruder = SimpleNamespace(get_position=lambda: (0.0, 0.0, 0.0, 5.0))
    objects = {"toolhead": toolhead, "extruder": extruder}
    printer = SimpleNamespace(
        lookup_object=lambda name, default=None: objects.get(name, default)
    )
    assert isinstance(build_toolhead(printer), ToolheadWrapper)
    assert build_toolhead(printer).position() == (1.0, 2.0, 3.0, 4.0)
    assert isinstance(build_extruder(printer), ExtruderWrapper)
    assert build_extruder(printer).position_mm() == 5.0


def test_build_helpers_degrade_on_exception():
    class BadPrinter:
        def lookup_object(self, name, default=None):
            raise RuntimeError("printer no listo")

    assert build_toolhead(BadPrinter()).position() is None
    assert build_toolhead(None).position() is None
    assert build_extruder(BadPrinter()).position_mm() == 0.0
    assert build_extruder(None).temperature() == 0.0
