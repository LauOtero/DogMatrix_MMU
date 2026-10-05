"""Pruebas de las etiquetas de texto de filamento (sin UI)."""

from __future__ import annotations

from dog_matrix.filament_display import (
    FilamentDisplay,
    SIN_FILAMENTO,
    format_filament,
    format_gate,
)


def test_format_filament_full():
    entry = {"material": "PLA", "color": "#FF0000", "spool_id": 12}
    assert format_filament(entry) == "PLA #FF0000 (spool 12)"


def test_format_filament_without_color():
    assert format_filament({"material": "PETG", "spool_id": "7"}) == "PETG (spool 7)"


def test_format_filament_without_spool():
    assert format_filament({"material": "ABS", "color": "#000000"}) == "ABS #000000"


def test_format_filament_empty_and_invalid():
    assert format_filament({}) == SIN_FILAMENTO
    assert format_filament(None) == SIN_FILAMENTO
    assert format_filament("no-dict") == SIN_FILAMENTO


def test_format_gate():
    entry = {"material": "PLA", "color": "#FF0000"}
    assert format_gate(0, entry) == "G0: PLA #FF0000"
    assert format_gate(2, entry, active=True) == "G2: PLA #FF0000 *"


def test_display_lines_and_active():
    display = FilamentDisplay(gate_count=2)
    display.set_gate(0, {"material": "PLA", "color": "#FF0000"})
    display.set_gate(1, {"material": "PETG", "color": "#00FF00"})
    display.set_active(1)
    assert display.lines() == [
        "G0: PLA #FF0000",
        "G1: PETG #00FF00 *",
    ]
    assert display.active_line() == "PETG #00FF00"


def test_active_line_without_active():
    display = FilamentDisplay(gate_count=1)
    display.set_gate(0, {"material": "PLA"})
    assert display.active_line() == SIN_FILAMENTO


def test_get_status():
    display = FilamentDisplay(gate_count=1)
    display.set_gate(0, {"material": "PLA", "color": "#FFFFFF"})
    display.set_active(0)
    assert display.get_status() == {
        "lines": ["G0: PLA #FFFFFF *"],
        "active": 0,
        "gate_count": 1,
    }


def test_display_tolerates_empty_entries():
    display = FilamentDisplay(gate_count=2)
    display.set_gate(0, None)  # type: ignore[arg-type]
    display.set_active(0)
    assert display.lines()[0] == "G0: {} *".format(SIN_FILAMENTO)
    assert display.active_line() == SIN_FILAMENTO
