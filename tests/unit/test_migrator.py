"""Pruebas de migracion desde Happy Hare."""

from __future__ import annotations

import pytest

from installer.migrator import ConfigMigrator


MMU_VARS = """[Variables]
num_gates = 8
bowden = 720
toolhead = 90
encoder_resolution = 0.5
gate_map = {0: 0, 1: 1, 2: 2}
"""


def _write_vars(tmp_path):
    path = tmp_path / "mmu_vars.cfg"
    path.write_text(MMU_VARS, encoding="utf-8")
    return path


def test_detect_legacy_configuration(tmp_path):
    (tmp_path / "mmu_vars.cfg").write_text("", encoding="utf-8")
    legacy = ConfigMigrator().detect_legacy_configuration(str(tmp_path))
    assert legacy is not None
    assert legacy.kind == "happy_hare"


def test_detect_returns_none_without_legacy(tmp_path):
    assert ConfigMigrator().detect_legacy_configuration(str(tmp_path)) is None


def test_migrate_preserves_calibration(tmp_path):
    bundle = ConfigMigrator().migrate_from_happy_hare(str(_write_vars(tmp_path)))
    generated = bundle.files["dog_matrix_generated.cfg"]
    assert "bowden_length: 720" in generated
    assert "encoder_resolution: 0.5" in generated
    assert "dog_matrix_migration_diff.json" in bundle.files


def test_migrate_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ConfigMigrator().migrate_from_happy_hare(str(tmp_path / "nope.cfg"))


def test_migrate_schema_same_version():
    migrator = ConfigMigrator()
    config = {"schema_version": 1, "value": 1}
    assert migrator.migrate_schema_version(config, 1, 1) == config


def test_migrate_schema_unsupported_version():
    with pytest.raises(ValueError):
        ConfigMigrator().migrate_schema_version({"schema_version": 1}, 9, 1)
