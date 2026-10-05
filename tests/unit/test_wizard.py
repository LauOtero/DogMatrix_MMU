"""Pruebas del asistente de instalacion (wizard)."""

from __future__ import annotations

from installer.rollback import RollbackManager
from installer.wizard import (
    ERR_CONFLICTING_PINS,
    ERR_INVALID_PROFILE,
    InstallationWizard,
    WizardContext,
)

GENERATED_AT = "2026-01-01T00:00:00Z"


def _wizard(dest, **kwargs):
    context = WizardContext(
        dest=str(dest), profile_id=kwargs.pop("profile_id", "box_turtle"), generated_at=GENERATED_AT, **kwargs
    )
    return InstallationWizard(context)


def test_probe_hardware_environment(dest_dir):
    wizard = _wizard(dest_dir)
    probe = wizard.probe_hardware_environment()
    assert "python" in probe.details
    assert isinstance(probe.ok, bool)


def test_detect_mcu_devices_from_profile(dest_dir):
    wizard = _wizard(dest_dir)
    wizard.select_mmu_profile()
    devices = wizard.detect_mcu_devices()
    assert any(device.name == "mmu_main" for device in devices)


def test_dry_run_does_not_write(dest_dir):
    wizard = _wizard(dest_dir, dry_run=True)
    result = wizard.generate_and_install_configs(str(dest_dir))
    assert result.success
    assert result.dry_run
    assert not (dest_dir / "dog_matrix_generated.cfg").exists()


def test_deployment_writes_files(dest_dir):
    wizard = _wizard(dest_dir)
    result = wizard.generate_and_install_configs(str(dest_dir))
    assert result.success
    assert (dest_dir / "dog_matrix_generated.cfg").exists()
    assert (dest_dir / "dog_matrix_profile.json").exists()
    assert result.snapshot_id is not None


def test_verification_after_deployment(dest_dir):
    wizard = _wizard(dest_dir)
    wizard.generate_and_install_configs(str(dest_dir))
    report = wizard.verify_system_integrity(str(dest_dir))
    assert report.ok


def test_invalid_profile_returns_error(dest_dir):
    wizard = _wizard(dest_dir, profile_id="perfil_inexistente")
    result = wizard.generate_and_install_configs(str(dest_dir))
    assert not result.success
    assert result.error_code == ERR_INVALID_PROFILE


def test_pin_conflict_blocks_deployment(dest_dir, monkeypatch):
    wizard = _wizard(dest_dir)
    monkeypatch.setattr(
        InstallationWizard,
        "_pin_map",
        staticmethod(lambda _cfg: {"main:PA2": ["toolhead", "gate_0"]}),
    )
    result = wizard.generate_and_install_configs(str(dest_dir))
    assert not result.success
    assert result.error_code == ERR_CONFLICTING_PINS


def test_rollback_restores_previous_files(dest_dir):
    (dest_dir / "dog_matrix_generated.cfg").write_text("original", encoding="utf-8")
    manager = RollbackManager(str(dest_dir))
    snapshot = manager.prepare()
    (dest_dir / "dog_matrix_generated.cfg").write_text("modificado", encoding="utf-8")
    assert manager.rollback(snapshot)
    assert (dest_dir / "dog_matrix_generated.cfg").read_text(encoding="utf-8") == "original"
