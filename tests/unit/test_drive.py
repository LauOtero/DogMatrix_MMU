"""Pruebas del modulo ``drive``: motor de arrastre y su gestor."""

from __future__ import annotations

from dog_matrix.drive import (
    DRIVE_CUTTER,
    DRIVE_ESPOOLER,
    DRIVE_GEAR,
    DRIVE_SELECTOR,
    Drive,
    DriveManager,
)


# --- Drive -----------------------------------------------------------------
def test_drive_move_with_actuator():
    speeds = []
    drive = Drive(kind=DRIVE_GEAR, set_speed=speeds.append, name="gear0")
    assert drive.move(10.0, 5.0) is True
    assert speeds == [5.0]
    assert drive.is_moving() is True
    status = drive.get_status()
    assert status["kind"] == DRIVE_GEAR
    assert status["name"] == "gear0"
    assert status["moving"] is True
    assert status["last_distance_mm"] == 10.0
    assert status["last_speed_mm_s"] == 5.0


def test_drive_move_signs_speed_by_distance():
    speeds = []
    drive = Drive(set_speed=speeds.append)
    assert drive.move(-10.0, 5.0) is True
    assert speeds == [-5.0]


def test_drive_move_without_actuator_returns_false():
    drive = Drive()
    assert drive.move(10.0, 5.0) is False
    assert drive.is_moving() is False
    assert drive.get_status()["moving"] is False


def test_drive_move_does_not_raise_on_actuator_error():
    def boom(_speed):
        raise RuntimeError("hardware caido")

    drive = Drive(set_speed=boom)
    assert drive.move(10.0, 5.0) is False
    assert drive.is_moving() is False
    assert drive.errors == 1


def test_drive_stop_calls_stop_fn():
    calls = []
    drive = Drive(set_speed=lambda _s: None, stop_fn=lambda: calls.append(True))
    drive.move(5.0, 3.0)
    drive.stop()
    assert calls == [True]
    assert drive.is_moving() is False


def test_drive_stop_without_stop_fn_uses_zero_speed():
    speeds = []
    drive = Drive(set_speed=speeds.append)
    drive.move(5.0, 3.0)
    drive.stop()
    assert speeds[-1] == 0.0
    assert drive.is_moving() is False


def test_drive_stop_without_actuator_is_safe():
    drive = Drive()
    drive.stop()
    assert drive.is_moving() is False


# --- DriveManager ----------------------------------------------------------
def test_drive_manager_reuses_by_key():
    manager = DriveManager()
    a = manager.get(DRIVE_GEAR, 0)
    b = manager.get(DRIVE_GEAR, 0)
    c = manager.get(DRIVE_GEAR, 1)
    d = manager.get(DRIVE_SELECTOR, 0)
    assert a is b
    assert a is not c
    assert a is not d
    assert a.kind == DRIVE_GEAR
    assert a.name == "gear"
    assert c.name == "gear_1"


def test_drive_manager_uses_factory():
    created = []

    def factory(kind):
        created.append(kind)
        return Drive(kind=kind)

    manager = DriveManager(factory=factory)
    drive = manager.get(DRIVE_CUTTER)
    assert drive.kind == DRIVE_CUTTER
    assert created == [DRIVE_CUTTER]
    assert manager.get(DRIVE_CUTTER) is drive


def test_drive_manager_stop_all():
    calls = []

    def factory(kind):
        return Drive(
            kind=kind,
            set_speed=calls.append,
            stop_fn=lambda: calls.append("stop"),
        )

    manager = DriveManager(factory=factory)
    d1 = manager.get(DRIVE_GEAR, 0)
    d2 = manager.get(DRIVE_ESPOOLER, 0)
    d1.move(5.0, 2.0)
    d2.move(3.0, 2.0)
    manager.stop_all()
    assert d1.is_moving() is False
    assert d2.is_moving() is False
    assert calls.count("stop") == 2


def test_drive_manager_get_status():
    manager = DriveManager()
    manager.get(DRIVE_GEAR, 0)
    manager.get(DRIVE_SELECTOR, 1)
    status = manager.get_status()
    assert status["count"] == 2
    assert "gear:0" in status["drives"]
    assert "selector:1" in status["drives"]
