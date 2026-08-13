from __future__ import annotations

import json
import os
import stat

import pytest

from shared.secure_json_state import atomic_write_json, load_json


def test_secure_state_is_atomic_owner_only_and_round_trips(tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"old":true}', encoding="utf-8")
    os.chmod(target, 0o644)

    atomic_write_json(target, {"access_token": "secret", "name": "Албери"})

    assert load_json(target) == {"access_token": "secret", "name": "Албери"}
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_failed_atomic_publish_preserves_previous_state(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"version": 1})

    def fail_replace(_source, _target):
        raise OSError("simulated crash before publish")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        atomic_write_json(target, {"version": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_malformed_or_non_object_state_fails_closed(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("[1,2,3]", encoding="utf-8")
    assert load_json(target) == {}
    target.write_text("not-json", encoding="utf-8")
    assert load_json(target) == {}
