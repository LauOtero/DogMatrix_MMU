"""Pruebas de capacidades y perfiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from dog_matrix.capabilities import Capabilities, ProfileError, find_profile

PROFILES = ["box_turtle", "ercf", "tradrack", "night_owl", "emu", "quattrobox"]


@pytest.mark.parametrize("name", PROFILES)
def test_profile_loads_and_validates(name):
    capabilities = Capabilities(str(find_profile(name)))
    profile = capabilities.load()
    assert profile.gates >= 1
    assert capabilities.validate() == []
    assert profile.profile_id.startswith("dog_matrix.")


def test_capabilities_get_dotted_path():
    capabilities = Capabilities(str(find_profile("box_turtle")))
    capabilities.load()
    assert capabilities.get("limits.max_distance_mm") == 1500
    assert capabilities.get("limits.inexistente", "fallback") == "fallback"
    assert capabilities.has_capability("encoder") is True


def test_profile_missing_raises():
    with pytest.raises(ProfileError):
        find_profile("no_existe")


def test_invalid_profile_is_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\nprofile_id: dog_matrix.bad.v1\ntopology:\n  type: gear_per_gate\n  gates: 0\n"
        "capabilities: {}\nlimits:\n  max_load_speed_mm_s: 0\n  max_unload_speed_mm_s: 1\n  max_distance_mm: 1\n",
        encoding="utf-8",
    )
    capabilities = Capabilities(str(bad))
    errors = capabilities.validate()
    assert any("gates" in error for error in errors)
    with pytest.raises(ProfileError):
        capabilities.load()


def test_selector_topology_requires_selector_capability(tmp_path: Path):
    bad = tmp_path / "sel.yaml"
    bad.write_text(
        "schema_version: 1\nprofile_id: dog_matrix.sel.v1\n"
        "topology:\n  type: selector\n  gates: 4\n  selector_type: linear\n"
        "capabilities:\n  selector: false\n"
        "limits:\n  max_load_speed_mm_s: 80\n  max_unload_speed_mm_s: 100\n  max_distance_mm: 1000\n",
        encoding="utf-8",
    )
    errors = Capabilities(str(bad)).validate()
    assert any("selector" in error for error in errors)
