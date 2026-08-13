from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from scripts.postgres_backup_health import inspect_backup_health


NOW = 1_800_000_000.0


def _healthy_chain(tmp_path: Path) -> tuple[Path, Path, Path]:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o700)
    dump = backup_dir / "albery_20260813_031501.dump"
    dump.write_bytes(b"valid custom archive placeholder" * 50_000)
    dump.chmod(0o600)
    os.utime(dump, (NOW - 3600, NOW - 3600))
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    sidecar = dump.with_name(dump.name + ".sha256")
    sidecar.write_text(f"{digest}  {dump.name}\n", encoding="ascii")
    sidecar.chmod(0o600)
    status = tmp_path / "status" / "offsite.json"
    status.parent.mkdir(mode=0o700)
    status.write_text(json.dumps({
        "dump_name": dump.name,
        "bytes": dump.stat().st_size,
        "sha256": digest,
        "archive_valid": True,
    }), encoding="utf-8")
    status.chmod(0o600)
    os.utime(status, (NOW - 1800, NOW - 1800))
    return backup_dir, dump, status


def test_healthy_chain_is_silent(tmp_path: Path, monkeypatch):
    backup_dir, _, status = _healthy_chain(tmp_path)
    if os.name == "nt":
        # Windows does not expose POSIX chmod bits; production and CI run this on Linux.
        monkeypatch.setattr(
            "scripts.postgres_backup_health._mode",
            lambda path: 0o700 if path.is_dir() else 0o600,
        )
    assert inspect_backup_health(
        backup_dir, status, now=NOW, validate_archive=False,
    ) == []


def test_stale_local_dump_is_reported(tmp_path: Path):
    backup_dir, dump, status = _healthy_chain(tmp_path)
    os.utime(dump, (NOW - 31 * 3600, NOW - 31 * 3600))
    problems = inspect_backup_health(backup_dir, status, now=NOW, validate_archive=False)
    assert any("latest local dump" in problem for problem in problems)


def test_stale_partial_is_reported(tmp_path: Path):
    backup_dir, _, status = _healthy_chain(tmp_path)
    partial = backup_dir / "albery_20260813_041501.dump.partial"
    partial.write_bytes(b"partial")
    os.utime(partial, (NOW - 3 * 3600, NOW - 3 * 3600))
    problems = inspect_backup_health(backup_dir, status, now=NOW, validate_archive=False)
    assert any("stale partial" in problem for problem in problems)


def test_missing_sidecar_is_reported(tmp_path: Path):
    backup_dir, dump, status = _healthy_chain(tmp_path)
    dump.with_name(dump.name + ".sha256").unlink()
    problems = inspect_backup_health(backup_dir, status, now=NOW, validate_archive=False)
    assert any("sidecar" in problem for problem in problems)


def test_offsite_mismatch_waits_for_transfer_grace(tmp_path: Path):
    backup_dir, dump, status = _healthy_chain(tmp_path)
    payload = json.loads(status.read_text(encoding="utf-8"))
    payload["dump_name"] = "albery_20260812_031501.dump"
    status.write_text(json.dumps(payload), encoding="utf-8")
    status.chmod(0o600)

    assert not any(
        "not the verified offsite" in problem
        for problem in inspect_backup_health(backup_dir, status, now=NOW, validate_archive=False)
    )
    os.utime(dump, (NOW - 3 * 3600, NOW - 3 * 3600))
    assert any(
        "not the verified offsite" in problem
        for problem in inspect_backup_health(backup_dir, status, now=NOW, validate_archive=False)
    )


def test_wrong_permissions_are_reported(tmp_path: Path):
    backup_dir, dump, status = _healthy_chain(tmp_path)
    dump.chmod(0o644)
    problems = inspect_backup_health(backup_dir, status, now=NOW, validate_archive=False)
    assert any("dump mode" in problem for problem in problems)
