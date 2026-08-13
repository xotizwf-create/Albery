#!/usr/bin/env python3
"""Fast, side-effect-free health checks for PostgreSQL backup artifacts."""
from __future__ import annotations

import json
import re
import stat
import subprocess
import time
from pathlib import Path


DUMP_RE = re.compile(r"^albery_\d{8}_\d{6}\.dump$")
SHA256_RE = re.compile(r"^([0-9a-f]{64})  (albery_\d{8}_\d{6}\.dump)$")
DEFAULT_BACKUP_DIR = Path("/var/backups/albery/postgres")
DEFAULT_OFFSITE_STATUS = Path("/var/lib/albery/backup-status/offsite.json")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def read_sidecar(dump: Path) -> tuple[str, str] | None:
    sidecar = dump.with_name(dump.name + ".sha256")
    try:
        line = sidecar.read_text(encoding="ascii").strip()
    except OSError:
        return None
    match = SHA256_RE.fullmatch(line)
    if not match:
        return None
    return match.group(1), match.group(2)


def inspect_backup_health(
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    offsite_status: Path = DEFAULT_OFFSITE_STATUS,
    *,
    now: float | None = None,
    max_age_seconds: int = 26 * 3600,
    offsite_grace_seconds: int = 3600,
    partial_max_age_seconds: int = 2 * 3600,
    minimum_dump_bytes: int = 1024 * 1024,
    validate_archive: bool = True,
) -> list[str]:
    """Return human-readable problems. An empty list means the backup chain is healthy."""
    now = time.time() if now is None else now
    problems: list[str] = []
    if not backup_dir.is_dir():
        return [f"PostgreSQL backup: directory is missing ({backup_dir})"]
    if _mode(backup_dir) != 0o700:
        problems.append(f"PostgreSQL backup: directory mode is {_mode(backup_dir):04o}, expected 0700")

    dumps = sorted(
        (path for path in backup_dir.iterdir() if path.is_file() and DUMP_RE.fullmatch(path.name)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not dumps:
        return problems + ["PostgreSQL backup: no completed dumps"]

    latest = dumps[0]
    latest_stat = latest.stat()
    age = max(0.0, now - latest_stat.st_mtime)
    if age > max_age_seconds:
        problems.append(f"PostgreSQL backup: latest local dump is {age / 3600:.1f}h old")
    if latest_stat.st_size < minimum_dump_bytes:
        problems.append(f"PostgreSQL backup: latest local dump is unexpectedly small ({latest_stat.st_size} bytes)")
    if _mode(latest) != 0o600:
        problems.append(f"PostgreSQL backup: dump mode is {_mode(latest):04o}, expected 0600")

    for partial in backup_dir.glob("albery_*.dump.partial"):
        partial_age = max(0.0, now - partial.stat().st_mtime)
        if partial_age > partial_max_age_seconds:
            problems.append(f"PostgreSQL backup: stale partial file {partial.name} ({partial_age / 3600:.1f}h)")

    sidecar = read_sidecar(latest)
    if sidecar is None:
        problems.append(f"PostgreSQL backup: valid SHA-256 sidecar is missing for {latest.name}")
        local_sha = None
    else:
        local_sha, sidecar_name = sidecar
        if sidecar_name != latest.name:
            problems.append("PostgreSQL backup: SHA-256 sidecar points to another dump")
        sidecar_path = latest.with_name(latest.name + ".sha256")
        if _mode(sidecar_path) != 0o600:
            problems.append(f"PostgreSQL backup: sidecar mode is {_mode(sidecar_path):04o}, expected 0600")

    if validate_archive:
        try:
            result = subprocess.run(
                ["pg_restore", "--list", str(latest)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            problems.append(f"PostgreSQL backup: archive validation could not run ({type(exc).__name__})")
        else:
            if result.returncode != 0:
                problems.append("PostgreSQL backup: pg_restore rejected the latest local dump")

    try:
        status_stat = offsite_status.stat()
        status = json.loads(offsite_status.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        if age > offsite_grace_seconds:
            problems.append("PostgreSQL backup: offsite verification status is missing or invalid")
        return problems

    if _mode(offsite_status) != 0o600:
        problems.append(f"PostgreSQL backup: offsite status mode is {_mode(offsite_status):04o}, expected 0600")
    status_age = max(0.0, now - status_stat.st_mtime)
    if status_age > max_age_seconds:
        problems.append(f"PostgreSQL backup: offsite verification is {status_age / 3600:.1f}h old")
    if age > offsite_grace_seconds:
        if status.get("dump_name") != latest.name:
            problems.append("PostgreSQL backup: latest local dump is not the verified offsite dump")
        if local_sha and status.get("sha256") != local_sha:
            problems.append("PostgreSQL backup: local and offsite SHA-256 do not match")
        if status.get("bytes") != latest_stat.st_size:
            problems.append("PostgreSQL backup: local and offsite sizes do not match")
        if status.get("archive_valid") is not True:
            problems.append("PostgreSQL backup: offsite archive was not validated by pg_restore")
    return problems


def main() -> int:
    problems = inspect_backup_health()
    if problems:
        print("\n".join(problems))
        return 1
    print("PostgreSQL backup chain: healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
