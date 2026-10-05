"""Pruebas del validador del sistema."""

from __future__ import annotations

from dog_matrix.capabilities import Capabilities, find_profile
from installer.validator import (
    VALIDATION_FAIL,
    VALIDATION_PASS,
    SystemValidator,
)


def test_validate_schema_ok():
    profile = Capabilities(str(find_profile("box_turtle"))).load()
    result = SystemValidator().validate_schema(profile.raw)
    assert result.passed
    assert result.status in (VALIDATION_PASS, "VALIDATION_WARNING")


def test_validate_schema_rejects_bad_gates():
    bad = {
        "schema_version": 1,
        "profile_id": "dog_matrix.bad.v1",
        "topology": {"type": "gear_per_gate", "gates": 0},
        "capabilities": {},
        "limits": {"max_load_speed_mm_s": 1, "max_unload_speed_mm_s": 1, "max_distance_mm": 1},
    }
    result = SystemValidator().validate_schema(bad)
    assert not result.passed
    assert result.status == VALIDATION_FAIL


def test_pin_conflicts_detected():
    pins = {"main:PA1": ["encoder"], "main:PA2": ["toolhead", "gate_0"]}
    conflicts = SystemValidator().validate_pin_conflicts(pins)
    assert len(conflicts) == 1
    assert conflicts[0].pin == "main:PA2"


def test_no_pin_conflicts():
    pins = {"main:PA1": ["encoder"], "main:PA2": ["toolhead"]}
    assert SystemValidator().validate_pin_conflicts(pins) == []


def test_version_compatibility_ok():
    report = SystemValidator().validate_version_compatibility(
        {"klipper": "0.12.0", "moonraker": "0.9.0", "dog_matrix": "0.1.0"}
    )
    assert report.compatible


def test_version_compatibility_fails_on_old():
    report = SystemValidator().validate_version_compatibility(
        {"klipper": "0.5.0", "moonraker": "0.9.0", "dog_matrix": "0.1.0"}
    )
    assert not report.compatible
    assert "minimo" in report.details["klipper"]


def test_health_check():
    health = SystemValidator().perform_realtime_health_check()
    assert health.checks["python_supported"] is True


def test_cffi_bindings_integrity():
    assert SystemValidator().verify_cffi_bindings_integrity() is True
