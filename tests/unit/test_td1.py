"""Pruebas del modulo TD-1 (escaner de densidad optica / color)."""

from __future__ import annotations

import re

from dog_matrix.td1 import TD1Device, TD1Manager, TD1Reading, td_to_color


class _FakeConfigWrapper:
    """ConfigWrapper minimo compatible con ``.get(key, default)``."""

    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


# --- Estado deshabilitado ---------------------------------------------------
def test_disabled_manager_has_no_devices():
    manager = TD1Manager({}, profile=None)
    assert manager.enabled is False
    assert manager.count == 0
    assert manager.available is False
    assert manager.devices == []

    status = manager.get_status()
    assert status["enabled"] is False
    assert status["count"] == 0
    assert status["devices"] == []
    assert status["gates"] == {}


def test_enabled_manager_creates_devices():
    manager = TD1Manager({"td1_enabled": True, "td1_count": 2}, profile=None)
    assert manager.enabled is True
    assert manager.count == 2
    assert manager.available is True
    assert [device.name for device in manager.devices] == ["td1_0", "td1_1"]


def test_config_wrapper_getter_path():
    wrapper = _FakeConfigWrapper({"td1_enabled": True, "td1_count": 1})
    manager = TD1Manager(wrapper, profile=None)
    assert manager.enabled is True
    assert manager.count == 1
    assert manager.available is True


# --- TD1Device --------------------------------------------------------------
def test_simulate_then_read_returns_reading():
    device = TD1Device()
    reading = device.simulate(0.5, "#123456", "gris")
    assert isinstance(reading, TD1Reading)
    assert reading.td == 0.5
    assert reading.color_hex == "#123456"
    assert reading.color_name == "gris"
    assert reading.timestamp > 0.0

    assert device.read() is reading
    assert device.reads == 1
    assert device.get_status()["last"] == reading.as_dict()


def test_device_reader_is_used():
    expected = TD1Reading(td=0.9, color_hex="#D9D9D9", color_name="gris claro", timestamp=1.0)
    device = TD1Device(reader=lambda: expected)
    assert device.read() is expected
    assert device.last is expected
    assert device.available is True


def test_reader_exception_does_not_propagate():
    def _boom():
        raise RuntimeError("reader roto")

    device = TD1Device(reader=_boom)
    assert device.read() is None  # sin lectura previa -> None
    assert device.reads == 1

    # Con una lectura previa simulada, degrada a la ultima conocida.
    device.simulate(0.4, "#4A4A4A", "gris oscuro")
    assert isinstance(device.read(), TD1Reading)


# --- TD1Manager: asociacion a gates ----------------------------------------
def test_read_gate_associates_and_status_reflects():
    manager = TD1Manager({"td1_enabled": True, "td1_count": 1}, profile=None)
    reading = manager.devices[0].simulate(0.75, "#8C8C8C", "gris")

    got = manager.read_gate(3, device_index=0)
    assert got is reading
    assert manager.gate_readings[3] is reading

    status = manager.get_status()
    assert status["gates"]["3"] == reading.as_dict()


def test_read_gate_without_device_returns_none():
    manager = TD1Manager({"td1_enabled": True, "td1_count": 1}, profile=None)
    assert manager.read_gate(0, device_index=5) is None


def test_assign_to_gate_directly():
    manager = TD1Manager({"td1_enabled": True, "td1_count": 1}, profile=None)
    reading = TD1Reading(td=0.2, color_hex="#1A1A1A", color_name="negro", timestamp=2.0)
    manager.assign_to_gate(1, reading)
    assert manager.gate_readings[1] is reading


def test_scan_all_returns_entry_per_gate():
    manager = TD1Manager({"td1_enabled": True, "td1_count": 1}, profile=None)
    reading = manager.devices[0].simulate(0.8, "#D9D9D9", "gris claro")

    result = manager.scan_all([0, 1, 2])
    assert set(result.keys()) == {0, 1, 2}
    assert all(value is reading for value in result.values())


def test_scan_all_disabled_returns_none_per_gate():
    manager = TD1Manager({}, profile=None)
    result = manager.scan_all([0, 1])
    assert result == {0: None, 1: None}


# --- td_to_color ------------------------------------------------------------
def test_td_to_color_valid_for_several_values():
    for td in (0.0, 0.15, 0.45, 0.75, 1.0, 1.5):
        hex_color, name = td_to_color(td)
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", hex_color), hex_color
        assert isinstance(name, str) and name != ""


def test_td_to_color_low_is_darker_than_high():
    low_hex, _ = td_to_color(0.0)
    high_hex, _ = td_to_color(2.0)
    assert int(low_hex[1:], 16) < int(high_hex[1:], 16)
