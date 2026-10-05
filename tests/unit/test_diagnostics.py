"""Pruebas de diagnostico estructurado y evidence bundle."""

from __future__ import annotations

import json
import zipfile

from dog_matrix.diagnostics import Diagnostics


def _make(tmp_path, secret=None, monkeypatch=None):
    if secret and monkeypatch is not None:
        monkeypatch.setenv("DM_EVIDENCE_KEY", secret)
    return Diagnostics(
        {
            "log_level": "debug",
            "log_path": str(tmp_path / "dm.jsonl"),
            "evidence_dir": str(tmp_path / "evidence"),
        }
    )


def test_log_event_writes_jsonl(tmp_path):
    diagnostics = _make(tmp_path)
    diagnostics.log_event("info", "core", "test_event", value=42)
    lines = (tmp_path / "dm.jsonl").read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["component"] == "core"
    assert record["event"] == "test_event"
    assert record["value"] == 42


def test_recent_errors(tmp_path):
    diagnostics = _make(tmp_path)
    diagnostics.log_event("error", "core", "boom", code="E1")
    diagnostics.log_event("info", "core", "ok")
    errors = diagnostics.get_recent_errors()
    assert len(errors) == 1
    assert errors[0]["code"] == "E1"


def test_evidence_bundle(tmp_path):
    diagnostics = _make(tmp_path)
    diagnostics.log_event("info", "core", "event")
    bundle_path = diagnostics.create_evidence_bundle()
    assert bundle_path.endswith(".zip")
    with zipfile.ZipFile(bundle_path) as archive:
        names = archive.namelist()
        assert "manifest.json" in names


def test_evidence_signature(tmp_path, monkeypatch):
    diagnostics = _make(tmp_path, secret="s3cr3t", monkeypatch=monkeypatch)
    diagnostics.log_event("info", "core", "event")
    bundle_path = diagnostics.create_evidence_bundle()
    with zipfile.ZipFile(bundle_path) as archive:
        assert "manifest.sig" in archive.namelist()
    assert diagnostics.sign_evidence(bundle_path)
