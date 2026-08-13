#!/usr/bin/env python3
"""Atomically copy the newest verified PostgreSQL dump to the offsite host.

The receiver is intentionally capacity constrained, so retention stays configurable. Every
successful run proves local SHA-256, remote SHA-256 and remote pg_restore readability, then
writes a root-only local status file consumed by Albery self-check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path


BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/var/backups/albery/postgres"))
REMOTE_HOST = os.getenv("OFFSITE_HOST", "root@217.198.12.236")
REMOTE_DIR = os.getenv("OFFSITE_DIR", "/root/backups/albery-postgres")
STATUS_PATH = Path(os.getenv("OFFSITE_STATUS_PATH", "/var/lib/albery/backup-status/offsite.json"))
OFFSITE_KEEP = max(1, int(os.getenv("OFFSITE_KEEP", "1") or "1"))
SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=30"]
DUMP_RE = re.compile(r"^albery_\d{8}_\d{6}\.dump$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def plan_prune(names_newest_first: list[str], keep: int) -> list[str]:
    return list(names_newest_first[max(1, keep):])


def prune_before_send(free_mb: int, dump_mb: int) -> bool:
    return free_mb < dump_mb * 1.1


def plan_free_space(
    entries_newest_first: list[tuple[str, int]], needed_mb: int, free_mb: int, keep: int,
) -> tuple[list[str], int, bool]:
    doomed: list[str] = []
    free = free_mb
    surplus = max(0, len(entries_newest_first) - max(1, keep))
    for name, size_mb in reversed(entries_newest_first):
        if free >= needed_mb:
            break
        doomed.append(name)
        free += size_mb
    return doomed, free, len(doomed) > surplus


def _log(message: str) -> None:
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    print(f"{stamp} {message}", flush=True)


def _q(value: str | Path) -> str:
    return shlex.quote(str(value))


def _validate_dump_name(name: str) -> str:
    if not DUMP_RE.fullmatch(name):
        raise ValueError(f"unsafe dump name: {name!r}")
    return name


def _ssh(command: str, *, timeout: int = 300) -> str:
    result = subprocess.run(
        ["ssh", *SSH_OPTS, REMOTE_HOST, command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip()


def _remote_entries() -> list[tuple[str, int]]:
    listing = _ssh(
        f"cd {_q(REMOTE_DIR)} 2>/dev/null && "
        "find . -maxdepth 1 -type f -name 'albery_*.dump' -printf '%T@ %f %s\\n' "
        "| sort -rn || true"
    )
    entries: list[tuple[str, int]] = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        name = _validate_dump_name(parts[1])
        size_mb = max(1, (int(parts[2]) + 1024 * 1024 - 1) // 1024 // 1024)
        entries.append((name, size_mb))
    return entries


def _remote_delete(names: list[str]) -> None:
    for raw_name in names:
        name = _validate_dump_name(raw_name)
        base = f"{REMOTE_DIR}/{name}"
        _ssh(f"rm -f -- {_q(base)} {_q(base + '.sha256')} {_q(base + '.partial')}")


def _remote_prune(keep: int) -> None:
    doomed = plan_prune([name for name, _ in _remote_entries()], keep)
    _remote_delete(doomed)
    if doomed:
        _log(f"pruned {len(doomed)} old offsite dump(s)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _local_dump() -> tuple[Path, str]:
    dumps = sorted(
        (path for path in BACKUP_DIR.iterdir() if path.is_file() and DUMP_RE.fullmatch(path.name)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not dumps:
        raise RuntimeError(f"no completed PostgreSQL dumps in {BACKUP_DIR}")
    latest = dumps[0]
    sidecar = latest.with_name(latest.name + ".sha256")
    try:
        line = sidecar.read_text(encoding="ascii").strip()
        expected_sha, expected_name = line.split("  ", 1)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"valid SHA-256 sidecar is missing for {latest.name}") from exc
    if expected_name != latest.name or not SHA256_RE.fullmatch(expected_sha):
        raise RuntimeError(f"invalid SHA-256 sidecar for {latest.name}")
    actual_sha = _sha256(latest)
    if actual_sha != expected_sha:
        raise RuntimeError(f"local SHA-256 mismatch for {latest.name}")
    if stat.S_IMODE(latest.stat().st_mode) != 0o600:
        raise RuntimeError(f"unsafe local dump permissions for {latest.name}")
    return latest, actual_sha


def _verify_remote(latest: Path, local_sha: str) -> dict[str, object]:
    remote = f"{REMOTE_DIR}/{latest.name}"
    remote_size = int(_ssh(f"stat -c %s -- {_q(remote)}"))
    remote_sha = _ssh(f"sha256sum -- {_q(remote)}").split()[0]
    if remote_size != latest.stat().st_size:
        raise RuntimeError(f"offsite size mismatch for {latest.name}")
    if remote_sha != local_sha:
        raise RuntimeError(f"offsite SHA-256 mismatch for {latest.name}")
    _ssh(f"pg_restore --list {_q(remote)} >/dev/null", timeout=180)
    return {
        "version": 1,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dump_name": latest.name,
        "bytes": remote_size,
        "sha256": remote_sha,
        "archive_valid": True,
    }


def _write_status(status: dict[str, object]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(STATUS_PATH.parent, 0o700)
    partial = STATUS_PATH.with_name(STATUS_PATH.name + ".partial")
    partial.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(partial, 0o600)
    partial.replace(STATUS_PATH)


def _send_atomically(latest: Path, local_sha: str) -> None:
    dump_mb = max(1, (latest.stat().st_size + 1024 * 1024 - 1) // 1024 // 1024)
    remote = f"{REMOTE_DIR}/{latest.name}"
    partial = remote + ".partial"
    _ssh(f"mkdir -p -- {_q(REMOTE_DIR)} && chmod 700 -- {_q(REMOTE_DIR)}")

    # An already complete exact copy needs no transfer and, importantly, no destructive
    # pre-prune on a nearly full receiver.
    try:
        if _ssh(f"test -f {_q(remote)} && sha256sum -- {_q(remote)} || true").split()[0] == local_sha:
            _log(f"offsite already has verified bytes for {latest.name}")
            return
    except IndexError:
        pass

    _ssh(f"rm -f -- {_q(partial)}")
    free_mb = int(_ssh(f"df -Pm {_q(REMOTE_DIR)} | tail -1 | awk '{{print $4}}'"))
    if prune_before_send(free_mb, dump_mb):
        needed = int(dump_mb * 1.1) + 1
        doomed, free_after, forced = plan_free_space(_remote_entries(), needed, free_mb, OFFSITE_KEEP)
        if forced:
            _log("WARNING: capacity forces removal of the last offsite copy before transfer")
        _remote_delete(doomed)
        if free_after < needed:
            raise RuntimeError(f"offsite disk has {free_after}MB free, {needed}MB required")

    _log(f"sending {latest.name} ({dump_mb}MB) atomically")
    subprocess.run(
        [
            "rsync", "-a", "--chmod=F600", "-e", "ssh " + " ".join(SSH_OPTS),
            str(latest), f"{REMOTE_HOST}:{partial}",
        ],
        check=True,
        timeout=3600,
    )
    remote_sha = _ssh(f"sha256sum -- {_q(partial)}").split()[0]
    if remote_sha != local_sha:
        _ssh(f"rm -f -- {_q(partial)}")
        raise RuntimeError(f"transferred SHA-256 mismatch for {latest.name}")
    _ssh(f"pg_restore --list {_q(partial)} >/dev/null", timeout=180)
    _ssh(
        f"chmod 600 -- {_q(partial)} && mv -f -- {_q(partial)} {_q(remote)} && "
        f"printf '%s  %s\\n' {_q(local_sha)} {_q(latest.name)} > {_q(remote + '.sha256')} && "
        f"chmod 600 -- {_q(remote + '.sha256')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true", help="verify the existing exact offsite copy")
    args = parser.parse_args(argv)

    latest, local_sha = _local_dump()
    if not args.verify_only:
        _send_atomically(latest, local_sha)
    status = _verify_remote(latest, local_sha)
    _write_status(status)
    if not args.verify_only:
        _remote_prune(OFFSITE_KEEP)
    free_after = int(_ssh(f"df -Pm {_q(REMOTE_DIR)} | tail -1 | awk '{{print $4}}'"))
    _log(f"ok: {latest.name}, SHA-256 and pg_restore verified, receiver free {free_after}MB")
    if free_after < max(1, latest.stat().st_size // 1024 // 1024) * 2:
        _log("WARNING: receiver has less than two dump sizes free")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        _log(f"ERROR: command exited {exc.returncode}: {(exc.stderr or '')[-400:]}")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        _log(f"ERROR: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
