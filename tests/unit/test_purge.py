"""Pruebas de la purga inteligente (matriz, Blobifier y secuencia)."""

from __future__ import annotations

from dog_matrix.purge import FILAMENT_AREA_MM2, PurgeManager


def test_base_volume():
    manager = PurgeManager()
    result = manager.calculate_volume(0)
    assert abs(result.volume_mm3 - 120.0 * FILAMENT_AREA_MM2) < 0.05


def test_material_transition_factor():
    manager = PurgeManager()
    same = manager.calculate_volume(0, from_material="PLA", to_material="PLA")
    different = manager.calculate_volume(0, from_material="PLA", to_material="ABS")
    similar = manager.calculate_volume(0, from_material="PLA", to_material="PLA+")
    assert different.volume_mm3 > same.volume_mm3
    assert same.volume_mm3 < similar.volume_mm3 < different.volume_mm3


def test_color_change_adds_volume():
    manager = PurgeManager()
    same = manager.calculate_volume(0, from_color="#111111", to_color="#111111")
    diff = manager.calculate_volume(0, from_color="#111111", to_color="#222222")
    assert diff.volume_mm3 > same.volume_mm3


def test_blobifier_reduces_volume():
    manager = PurgeManager(config={"purge_use_blobifier": True, "purge_blobifier_factor": 0.5})
    normal = PurgeManager().calculate_volume(0)
    blob = manager.calculate_volume(0)
    assert blob.blobifier_mode is True
    assert abs(blob.volume_mm3 - normal.volume_mm3 * 0.5) < 0.05


def test_build_matrix_shape_and_total():
    manager = PurgeManager()
    matrix = manager.build_matrix(materials=["PLA", "ABS", "PETG"], colors=["", "", ""], gates=3)
    assert len(matrix) == 3 and all(len(row) == 3 for row in matrix)
    summary = manager.calculate_purge_volumes(materials=["PLA", "ABS"], gates=2)
    assert summary["total_mm3"] == round(sum(sum(r) for r in summary["matrix"]), 2)


def test_purge_sequence_emits_scripts():
    scripts = []
    manager = PurgeManager(emit=scripts.append)
    result = manager.purge_sequence(300.0, from_gate=0, to_gate=1)
    assert result == scripts
    assert any(script.startswith("G1 E") for script in scripts)
    assert scripts[0] == "G91" and scripts[-1] == "G90"


def test_config_from_dict():
    manager = PurgeManager(config={"purge_length_mm": 200.0, "purge_z_hop_mm": 6.0})
    assert manager.get_config().default_length_mm == 200.0
    assert manager.get_config().z_hop_height_mm == 6.0
