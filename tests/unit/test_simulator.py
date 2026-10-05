"""Pruebas del simulador interactivo (F-32)."""

from __future__ import annotations

from dog_matrix.simulator import Simulator, _parse_line


def test_parse_line():
    name, params = _parse_line("DM_GATE_MAP GATE=1 STATUS=available")
    assert name == "DM_GATE_MAP"
    assert params == {"GATE": "1", "STATUS": "available"}


def test_simulator_executes_status():
    simulator = Simulator(profile="box_turtle")
    responses = simulator.execute("DM_STATUS")
    assert responses
    assert any("Dog Matrix" in r for r in responses)


def test_simulator_unknown_command():
    simulator = Simulator(profile="box_turtle")
    responses = simulator.execute("NO_SUCH_COMMAND")
    assert any("desconocido" in r for r in responses)


def test_simulator_tick_advances_reactor():
    simulator = Simulator(profile="box_turtle")
    responses = simulator.execute("TICK SECONDS=2")
    assert simulator.printer.reactor.monotonic() == 2.0
    assert responses


def test_simulator_select_gate():
    simulator = Simulator(profile="box_turtle")
    simulator.execute("DM_SELECT GATE=3")
    assert simulator.core.current_gate == 3


def test_simulator_help_lists_commands():
    simulator = Simulator(profile="box_turtle")
    responses = simulator.execute("HELP")
    assert any("DM_STATUS" == r for r in responses)


def test_simulator_run_script_stops_on_error():
    simulator = Simulator(profile="box_turtle")
    output = simulator.run_script([
        "# comentario",
        "DM_SELECT GATE=2",
        "NO_SUCH_COMMAND",
        "DM_SELECT GATE=3",
    ])
    assert simulator.core.current_gate == 2
    assert any(item.startswith("ERROR:") for item in output)
    # Se detiene en el error: el segundo SELECT no se ejecuta.
    assert simulator.core.current_gate != 3


def test_simulator_dump_state():
    simulator = Simulator(profile="box_turtle")
    state = simulator.dump_state()
    assert state["gates"] == 8
    assert "print_state" in state
    assert any(item.startswith("gates=") for item in simulator.execute("DUMP"))


def test_simulator_main_script_and_json(tmp_path, capsys):
    from dog_matrix.simulator import main

    script = tmp_path / "sim.txt"
    script.write_text("DM_SELECT GATE=4\nTICK SECONDS=1\n", encoding="utf-8")
    assert main(["--script", str(script)]) == 0
    assert main(["--script", str(script), "--json"]) == 0
    captured = capsys.readouterr().out
    assert '"gates": 8' in captured
