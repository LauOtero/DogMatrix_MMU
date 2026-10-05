"""Pruebas de integracion del sistema multi-unidad con DogMatrixCore."""

from __future__ import annotations

import pytest

from tests.fixtures.fakes import FakeGcodeError

pytestmark = pytest.mark.slow

DEVICE_1 = "usb:usb-DogMatrix_MMU-1-if00"
DEVICE_2 = "usb:usb-DogMatrix_MMU-2-if00"


def _make_scan_dir(tmp_path, names):
    scan_dir = tmp_path / "by-id"
    scan_dir.mkdir()
    for name in names:
        (scan_dir / name).write_text("")
    return scan_dir


def test_multi_unit_disabled_by_default(make_core):
    core, printer = make_core()
    assert core.controller is None
    assert core.get_status(0.0)["units"]["enabled"] is False
    with pytest.raises(FakeGcodeError):
        printer.gcode.run("DM_UNIT")


def test_multi_unit_listing_assigns_sequential_and_offsets(make_core, tmp_path):
    scan_dir = _make_scan_dir(tmp_path, ["usb-DogMatrix_MMU-1-if00", "usb-DogMatrix_MMU-2-if00"])
    core, printer = make_core(
        enable_multi_unit=True,
        usb_scan_dir=str(scan_dir),
        units_store=str(tmp_path / "units.json"),
        num_gates=4,
    )
    printer.fire("klippy:ready")  # dispara el descubrimiento inicial

    gcmd = printer.gcode.run("DM_UNIT", {"ACTION": "LIST"})
    assert any("spool=0" in response for response in gcmd.responses)
    assert any("spool=1" in response for response in gcmd.responses)

    units = core.get_status(0.0)["units"]
    assert units["enabled"] is True
    assert units["count"] == 2
    assert units["units"][0]["spool_number"] == 0
    assert units["units"][0]["gate_offset"] == 0
    assert units["units"][1]["spool_number"] == 1
    assert units["units"][1]["gate_offset"] == 4


def test_multi_unit_manual_assign_and_clear(make_core, tmp_path):
    scan_dir = _make_scan_dir(tmp_path, ["usb-DogMatrix_MMU-1-if00"])
    core, printer = make_core(
        enable_multi_unit=True,
        usb_scan_dir=str(scan_dir),
        units_store=str(tmp_path / "units.json"),
        num_gates=4,
    )
    printer.fire("klippy:ready")

    printer.gcode.run("DM_UNIT", {"ACTION": "ASSIGN", "DEVICE": DEVICE_1, "SPOOL": 7})
    assert core.controller.store.get(DEVICE_1) == 7

    printer.gcode.run("DM_UNIT", {"ACTION": "CLEAR"})
    assert core.controller.store.get(DEVICE_1) == 0


def test_multi_unit_manual_override_from_config(make_core, tmp_path):
    scan_dir = _make_scan_dir(tmp_path, ["usb-DogMatrix_MMU-1-if00", "usb-DogMatrix_MMU-2-if00"])
    core, printer = make_core(
        enable_multi_unit=True,
        usb_scan_dir=str(scan_dir),
        units_store=str(tmp_path / "units.json"),
        num_gates=4,
        unit_assignments={DEVICE_2: 5},
    )
    printer.fire("klippy:ready")
    assert core.controller.store.get(DEVICE_2) == 5


def test_multi_unit_persistence_across_core_restart(make_core, tmp_path):
    scan_dir = _make_scan_dir(tmp_path, ["usb-DogMatrix_MMU-1-if00", "usb-DogMatrix_MMU-2-if00"])
    store = str(tmp_path / "units.json")

    core1, printer1 = make_core(
        enable_multi_unit=True,
        usb_scan_dir=str(scan_dir),
        units_store=store,
        num_gates=4,
    )
    printer1.fire("klippy:ready")
    assert core1.controller.store.get(DEVICE_2) == 1

    # "Reinicio" del sistema: nuevo core con el mismo almacen persistente.
    core2, _ = make_core(
        enable_multi_unit=True,
        usb_scan_dir=str(scan_dir),
        units_store=store,
        num_gates=4,
    )
    assert core2.controller.store.get(DEVICE_1) == 0
    assert core2.controller.store.get(DEVICE_2) == 1


def test_multi_unit_disconnect_does_not_reassign(make_core, tmp_path):
    scan_dir = _make_scan_dir(tmp_path, ["usb-DogMatrix_MMU-1-if00", "usb-DogMatrix_MMU-2-if00"])
    core, printer = make_core(
        enable_multi_unit=True,
        usb_scan_dir=str(scan_dir),
        units_store=str(tmp_path / "units.json"),
        num_gates=4,
    )
    printer.fire("klippy:ready")

    # Se desconecta el dispositivo 1 (se elimina del directorio de escaneo).
    (scan_dir / "usb-DogMatrix_MMU-1-if00").unlink()
    printer.gcode.run("DM_UNIT", {"ACTION": "RESCAN"})

    # El dispositivo 2 conserva su numero original.
    assert core.controller.store.get(DEVICE_2) == 1
