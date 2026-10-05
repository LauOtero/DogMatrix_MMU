"""Pruebas del simulador del instalador/wizard/configurador."""

from __future__ import annotations

import io
import json

import pytest

from installer.simulator import InstallerSimulator, _parse_args, list_profiles, main


def test_parse_args_tokens():
    kwargs, positional = _parse_args(["vendor=box_turtle", "--units", "2", "file.txt", "--json"])
    assert kwargs["vendor"] == "box_turtle"
    assert kwargs["units"] == "2"
    assert kwargs["json"] is True
    assert positional == ["file.txt"]


def test_list_profiles_excludes_example():
    profiles = list_profiles()
    assert "box_turtle" in profiles
    assert "ercf" in profiles
    assert all("example" not in name for name in profiles)


def test_execute_help_and_lists(tmp_path):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    assert any("Comandos disponibles" in line for line in simulator.execute("help"))
    assert any("box_turtle" in line for line in simulator.execute("list-vendors"))
    assert "ercf" in simulator.execute("list-profiles")
    assert "mellow_fly_mmu" in simulator.execute("list-boards")


def test_execute_menuconfig(tmp_path):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    payload = json.loads("\n".join(simulator.execute("menuconfig vendor=kms units=2")))
    assert payload["vendor"] == "kms"
    assert payload["units"] == 2
    assert payload["gates"] == 8


def test_execute_wizard_and_ls_show(tmp_path):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    output = "\n".join(simulator.execute("wizard vendor=ercf_1_1"))
    assert "OK ->" in output
    assert "gates=9" in output
    listing = simulator.execute("ls")
    assert any("dog_matrix_generated.cfg" in line for line in listing)
    shown = "\n".join(simulator.execute("show dog_matrix_generated.cfg"))
    assert "[dm_pins]" in shown


def test_execute_unknown_and_dest(tmp_path):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    assert any("desconocido" in line for line in simulator.execute("bogus"))
    assert simulator.execute("dest") == [f"base   : {tmp_path}"]


def test_verify_uses_last_deployment(tmp_path):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    simulator.execute("wizard vendor=ercf_2_0")
    report = json.loads("\n".join(simulator.execute("verify")))
    assert report["ok"] is True
    assert report["errors"] == []


def test_execute_dry_run_does_not_write(tmp_path):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    simulator.execute("wizard vendor=box_turtle dry_run=1")
    assert not list(tmp_path.rglob("dog_matrix_generated.cfg"))


def test_run_console_scripted(tmp_path):
    simulator = InstallerSimulator(base_dir=str(tmp_path))
    stdin = io.StringIO("list-profiles\nexit\n")
    stdout = io.StringIO()
    simulator.run_console(stdin=stdin, stdout=stdout)
    assert "box_turtle" in stdout.getvalue()
    assert "Saliendo" in stdout.getvalue()


def test_main_with_commands_and_json(tmp_path, capsys):
    assert main(["--command", "list-boards", "--json", "--dest", str(tmp_path)]) == 0
    captured = capsys.readouterr().out
    assert "mellow_fly_mmu" in captured


def test_main_with_vendor(tmp_path, capsys):
    assert main(["--vendor", "box_turtle", "--units", "2", "--dest", str(tmp_path)]) == 0
    captured = capsys.readouterr().out
    assert "OK ->" in captured


def test_main_with_script(tmp_path, capsys):
    script = tmp_path / "escenario.txt"
    script.write_text("wizard vendor=emu\n", encoding="utf-8")
    assert main(["--script", str(script), "--dest", str(tmp_path / "out")]) == 0
    assert "OK ->" in capsys.readouterr().out


@pytest.mark.slow
def test_main_simulate_all_json(tmp_path, capsys):
    assert main(["--all", "--json", "--dest", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["count"] >= 20
