"""Pruebas de persistencia atomica y snapshots."""

from __future__ import annotations

import json

import pytest

from dog_matrix.persistence import ChecksumError, Persistence, PersistenceError


def test_save_load_roundtrip(tmp_path):
    persistence = Persistence(str(tmp_path / "state.json"))
    persistence.save({"current_gate": 3, "ttg_map": [0, 1, 2]})
    loaded = persistence.load()
    assert loaded["current_gate"] == 3
    assert loaded["ttg_map"] == [0, 1, 2]


def test_load_missing_returns_empty(tmp_path):
    persistence = Persistence(str(tmp_path / "none.json"))
    assert persistence.load() == {}


def test_checksum_detects_tampering(tmp_path):
    path = tmp_path / "state.json"
    persistence = Persistence(str(path))
    persistence.save({"value": 1})
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["data"]["value"] = 999  # manipulacion sin actualizar checksum
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ChecksumError):
        persistence.load()


def test_snapshot_create_restore_validate(tmp_path):
    persistence = Persistence(str(tmp_path / "state.json"))
    persistence.save({"a": 1})
    snapshot_id = persistence.create_snapshot()
    assert persistence.validate_checksum(snapshot_id)
    persistence.save({"a": 2})
    restored = persistence.restore_snapshot(snapshot_id)
    assert restored["a"] == 1
    assert persistence.load()["a"] == 1


def test_rotation_keeps_latest(tmp_path):
    persistence = Persistence(str(tmp_path / "state.json"))
    for index in range(4):
        persistence.save({"index": index})
        persistence.create_snapshot()
    persistence.rotate_backups(2)
    assert len(persistence.list_snapshots()) == 2


def test_corrupt_envelope_raises(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(PersistenceError):
        Persistence(str(path)).load()
