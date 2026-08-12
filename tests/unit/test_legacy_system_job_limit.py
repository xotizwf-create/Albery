from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[2] / "scripts" / "run_legacy_system_job.py"
    spec = importlib.util.spec_from_file_location("run_legacy_system_job", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class Slot:
    def __init__(self, *, local=False):
        self.is_local_fallback = local
        self.released = 0

    def release(self):
        self.released += 1


def test_unknown_job_cannot_become_shell_execution():
    module = _module()
    assert module.run("; rm -rf /tmp/x") == 64


def test_busy_global_limit_defers_without_starting_job(tmp_path, monkeypatch):
    module = _module()
    job = tmp_path / "job.sh"
    job.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    module.JOBS = {"test": (job, module.hashlib.sha256(job.read_bytes()).hexdigest())}
    monkeypatch.setattr(module, "build_default", lambda: type("Limiter", (), {"acquire": lambda self, wait: None})())
    called = []
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: called.append(True))

    assert module.run("test") == 75
    assert called == []


def test_database_fallback_is_refused(tmp_path, monkeypatch):
    module = _module()
    job = tmp_path / "job.sh"
    job.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    module.JOBS = {"test": (job, module.hashlib.sha256(job.read_bytes()).hexdigest())}
    slot = Slot(local=True)
    monkeypatch.setattr(module, "build_default", lambda: type("Limiter", (), {"acquire": lambda self, wait: slot})())

    assert module.run("test") == 75
    assert slot.released == 1


def test_allowlisted_job_holds_and_releases_global_slot(tmp_path, monkeypatch):
    module = _module()
    job = tmp_path / "job.sh"
    job.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    module.JOBS = {"test": (job, module.hashlib.sha256(job.read_bytes()).hexdigest())}
    slot = Slot()
    monkeypatch.setattr(module, "build_default", lambda: type("Limiter", (), {"acquire": lambda self, wait: slot})())
    seen = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: seen.append(command) or type("Done", (), {"returncode": 0})())

    assert module.run("test") == 0
    assert seen == [[str(job)]]
    assert slot.released == 1


def test_reviewed_job_checksum_blocks_untracked_drift(tmp_path, monkeypatch):
    module = _module()
    job = tmp_path / "job.sh"
    job.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    module.JOBS = {"test": (job, "0" * 64)}
    called = []
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: called.append(True))

    assert module.run("test") == 78
    assert called == []
