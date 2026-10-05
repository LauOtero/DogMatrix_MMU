"""Pruebas de integracion del CLI del instalador."""

from __future__ import annotations

from installer.cli import main


def test_cli_preflight_returns_success():
    assert main(["preflight", "--json"]) == 0


def test_cli_doctor_returns_success():
    assert main(["doctor", "--json"]) == 0


def test_cli_generate_and_validate(tmp_path):
    dest = str(tmp_path / "cfg")
    assert main(["generate", "--profile", "box_turtle", "--dest", dest, "--json"]) == 0
    assert (tmp_path / "cfg" / "dog_matrix_generated.cfg").exists()
    assert main(["validate", "--dest", dest, "--json"]) == 0


def test_cli_rollback_without_snapshot_fails(tmp_path):
    assert main(["rollback", "--dest", str(tmp_path / "empty")]) == 1


def test_cli_migrate_dry_run(tmp_path):
    source = tmp_path / "mmu_vars.cfg"
    source.write_text("[Variables]\nnum_gates = 4\nbowden = 700\n", encoding="utf-8")
    assert main(["migrate", "--source", str(source), "--dry-run", "--json"]) == 0
