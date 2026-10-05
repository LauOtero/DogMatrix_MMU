"""Pruebas del constructor guiado de placas (usuarios sin conocimientos tecnicos)."""

from __future__ import annotations

import json

import pytest

from dog_matrix import boards
from installer.board_builder import (
    BoardAnswers,
    BoardBuilder,
    BoardBuilderError,
    answers_from_dict,
    collect_answers,
    dump_yaml,
    validate_board_document,
)


def _simple_answers(**overrides) -> BoardAnswers:
    data = {
        "board_id": "mi_placa",
        "display_name": "Mi Placa MMU",
        "topology": "selector",
        "mcu": {"name": "mmu", "transport": "can", "arch": "rp2040"},
        "drivers": [
            {"step": "gpio2", "dir": "gpio1", "enable": "!gpio3", "uart": "gpio0"},
            {"step": "gpio7", "dir": "gpio8", "enable": "!gpio6", "uart": "gpio9"},
        ],
        "pins": {"selector_endstop": "^gpio20", "encoder": "^gpio15", "servo": "gpio21"},
        "capabilities": {"selector": True, "encoder": True, "servo": True},
    }
    data.update(overrides)
    return answers_from_dict(data)


def test_build_and_validate_simple_board():
    builder = BoardBuilder(_simple_answers())
    document = builder.build()
    assert document["board_id"] == "mi_placa"
    assert document["mcu"]["transport"] == "can"
    assert len(document["drivers"]) == 2
    assert builder.validate() == []


def test_invalid_board_id_is_rejected():
    builder = BoardBuilder(_simple_answers(board_id="Mi Placa!"))
    errors = builder.validate()
    assert any("board_id invalido" in error for error in errors)


def test_invalid_topology_is_rejected():
    document = BoardBuilder(_simple_answers()).build()
    document["topology"] = "rara"
    assert any("topology invalido" in error for error in validate_board_document(document))


def test_duplicate_pins_are_detected():
    answers = _simple_answers(
        drivers=[
            {"step": "gpio2", "dir": "gpio1"},
            {"step": "gpio2", "dir": "gpio8"},  # step duplicado
        ]
    )
    assert any("pin duplicado" in error for error in BoardBuilder(answers).validate())


def test_shared_pins_are_respected():
    answers = _simple_answers(
        drivers=[
            {"step": "PA4", "dir": "PA10", "enable": "!PA2", "uart": "PA8"},
            {"step": "PA9", "dir": "PB8", "enable": "!PA11", "uart": "PA8"},
        ]
    )
    document = BoardBuilder(answers).build()
    document["shared_pins"] = ["GEAR_UART,SEL_UART"]
    assert validate_board_document(document) == []


def test_write_and_reload_board_is_conflict_free(tmp_path):
    builder = BoardBuilder(_simple_answers())
    path = builder.write(str(tmp_path))
    assert path.exists()
    board = boards.load_board(str(path))
    assert board.board_id == "mi_placa"
    plan = boards.build_pin_plan(board)
    assert boards.validate_pin_plan(plan) == []


def test_write_refuses_to_overwrite(tmp_path):
    builder = BoardBuilder(_simple_answers())
    builder.write(str(tmp_path))
    with pytest.raises(BoardBuilderError):
        BoardBuilder(_simple_answers()).write(str(tmp_path))


def test_yaml_is_deterministic_and_has_comments():
    text_a = BoardBuilder(_simple_answers()).to_yaml()
    text_b = BoardBuilder(_simple_answers()).to_yaml()
    assert text_a == text_b
    assert text_a.startswith("# Dog Matrix MMU")
    assert "board_id: mi_placa" in text_a


def test_dump_yaml_quotes_special_values(tmp_path):
    text = dump_yaml({"enable": "!PA8", "name": "Box Turtle", "empty": ""})
    assert 'enable: "!PA8"' in text
    assert 'name: Box Turtle' in text
    assert 'empty: ""' in text
    # Reinterpretable por el cargador del proyecto.
    path = tmp_path / "x.yaml"
    path.write_text(text, encoding="utf-8")
    from dog_matrix.capabilities import _load_document

    loaded = _load_document(path)
    assert loaded["enable"] == "!PA8"


def test_interactive_collect_and_build(tmp_path):
    scripted = iter(
        [
            "placa_usuario", "Placa del Usuario", "",      # id, nombre, topologia
            "", "", "rp2040",                               # mcu, transport, arch
            "^gpio20", "^gpio15", "", "gpio21", "", "gpio14", "", "",  # pins
            "2",                                            # n drivers
            "gpio2", "gpio1", "!gpio3", "gpio0", "",        # driver 0
            "gpio7", "gpio8", "!gpio6", "gpio9", "",        # driver 1
        ]
    )

    def fake_input(_prompt: str) -> str:
        try:
            return next(scripted)
        except StopIteration:
            return ""

    answers = collect_answers(input_fn=fake_input, output_fn=lambda _line: None)
    builder = BoardBuilder(answers)
    assert builder.validate() == []
    path = builder.write(str(tmp_path))
    assert boards.load_board(str(path)).board_id == "placa_usuario"


def test_cli_add_board_from_json(tmp_path):
    from installer.cli import main

    payload = {
        "board_id": "placa_cli",
        "display_name": "Placa CLI",
        "topology": "selector",
        "drivers": [{"step": "gpio2", "dir": "gpio1", "enable": "!gpio3", "uart": "gpio0"}],
        "pins": {"encoder": "^gpio15", "servo": "gpio21"},
    }
    source = tmp_path / "board.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    boards_dir = tmp_path / "boards"
    code = main(
        [
            "add-board",
            "--from-json",
            str(source),
            "--boards-dir",
            str(boards_dir),
            "--json",
        ]
    )
    assert code == 0
    assert (boards_dir / "placa_cli.yaml").exists()


def test_cli_add_board_requires_fields_in_headless():
    from installer.cli import main

    assert main(["add-board", "--yes"]) == 1


def test_all_shipped_boards_validate_against_schema():
    for board_id, board in boards.load_all_boards().items():
        assert validate_board_document(board.raw) == [], board_id
