"""Pruebas de los overrides de parametros por herramienta."""

from __future__ import annotations

from dog_matrix.tool_overrides import ToolOverride, ToolOverrideStore


def test_set_get_and_normalize_types():
    store = ToolOverrideStore()
    override = store.set_override(
        0,
        speed_factor="1.5",
        purge_mm="12.5",
        temperature="210",
        material="PLA",
        color="#ff0000",
    )
    assert isinstance(override, ToolOverride)
    assert override.speed_factor == 1.5
    assert override.purge_mm == 12.5
    assert override.temperature == 210
    assert override.material == "PLA"
    assert override.color == "#ff0000"
    assert store.get(0) is override

    # Actualiza solo el campo indicado sin perder el resto.
    store.set_override(0, speed_factor=2.0)
    assert store.get(0).speed_factor == 2.0
    assert store.get(0).purge_mm == 12.5


def test_clear_one_and_all():
    store = ToolOverrideStore()
    store.set_override(0, speed_factor=1.2)
    store.set_override(1, speed_factor=0.8)
    store.clear(0)
    assert store.get(0) is None
    assert store.get(1) is not None
    store.clear()
    assert store.get(1) is None
    assert store.as_dict() == {}


def test_unknown_fields_go_to_extra():
    store = ToolOverrideStore()
    override = store.set_override(2, speed_factor=1.1, flow=95, notes="dry")
    assert override.extra == {"flow": 95, "notes": "dry"}
    assert "flow" not in override.as_dict()
    assert override.as_dict()["extra"] == {"flow": 95, "notes": "dry"}


def test_apply_speed_with_and_without_override():
    store = ToolOverrideStore()
    assert store.apply_speed(0, 100.0) == 100.0
    store.set_override(0, speed_factor=1.5)
    assert store.apply_speed(0, 100.0) == 150.0


def test_apply_purge_with_and_without_override():
    store = ToolOverrideStore()
    assert store.apply_purge(0, 20.0) == 20.0
    # purge_mm <= 0 no sustituye el valor dado.
    store.set_override(0, purge_mm=0.0)
    assert store.apply_purge(0, 20.0) == 20.0
    store.set_override(0, purge_mm=35.0)
    assert store.apply_purge(0, 20.0) == 35.0


def test_apply_temperature_with_and_without_override():
    store = ToolOverrideStore()
    assert store.apply_temperature(0, 200) == 200
    store.set_override(0, temperature=0)
    assert store.apply_temperature(0, 200) == 200
    store.set_override(0, temperature=225)
    assert store.apply_temperature(0, 200) == 225


def test_as_dict_load_roundtrip():
    store = ToolOverrideStore()
    store.set_override(0, speed_factor=1.25, purge_mm=8.0, temperature=215, material="PETG", color="blue", brand="X")
    payload = store.as_dict()
    assert set(payload) == {"0"}

    other = ToolOverrideStore()
    other.load(payload)
    restored = other.get(0)
    assert restored == store.get(0)
    assert restored.extra == {"brand": "X"}


def test_config_dict_initial_load():
    config = {
        "tool_overrides": {
            "1": {"speed_factor": 0.9, "temperature": 240, "material": "ABS"},
        }
    }
    store = ToolOverrideStore(config)
    override = store.get(1)
    assert override is not None
    assert override.speed_factor == 0.9
    assert override.temperature == 240
    assert override.material == "ABS"


def test_get_status():
    store = ToolOverrideStore()
    assert store.get_status() == {"count": 0, "overrides": {}}
    store.set_override(0, speed_factor=1.5)
    store.set_override(3, temperature=210)
    status = store.get_status()
    assert status["count"] == 2
    assert set(status["overrides"]) == {"0", "3"}
    assert status["overrides"]["3"]["temperature"] == 210
