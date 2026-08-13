#!/usr/bin/env python3
"""Install Albery's systemd-249-compatible Hermes restart policy.

The Hermes base unit historically carried newer-systemd directives that Ubuntu 22.04 ignores.
This installer removes only those exact unsupported directives, installs a versioned drop-in and
never restarts the service. The caller owns the empty-work restart gate.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
DEFAULT_UNIT = Path("/etc/systemd/system/hermes-gateway.service")
DEFAULT_DROPIN = Path(
    "/etc/systemd/system/hermes-gateway.service.d/10-albery-restart-policy.conf"
)
POLICY_SOURCE = BASE / "deploy" / "hermes-gateway-restart-policy.conf"
UNSUPPORTED = ("RestartMaxDelaySec", "RestartSteps")


def sanitize_unit_text(text: str) -> tuple[str, list[str]]:
    """Remove only unsupported assignments, preserving all other unit content byte-for-byte."""

    kept: list[str] = []
    removed: list[str] = []
    for line in text.splitlines(keepends=True):
        key = line.lstrip().split("=", 1)[0].strip()
        if key in UNSUPPORTED:
            removed.append(key)
            continue
        kept.append(line)
    return "".join(kept), removed


def validate_policy_text(text: str) -> None:
    required = {
        "StartLimitIntervalSec=300s",
        "StartLimitBurst=5",
        "RestartSec=30s",
    }
    present = {line.strip() for line in text.splitlines()}
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(f"restart policy is incomplete: {', '.join(missing)}")


def atomic_write(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def install(*, unit: Path, dropin: Path, backup_dir: Path) -> list[str]:
    if not unit.is_absolute() or not dropin.is_absolute() or not backup_dir.is_absolute():
        raise RuntimeError("unit, drop-in and backup paths must be absolute")
    if not unit.is_file():
        raise RuntimeError(f"Hermes unit is absent: {unit}")
    policy = POLICY_SOURCE.read_text(encoding="utf-8")
    validate_policy_text(policy)
    original = unit.read_text(encoding="utf-8")
    sanitized, removed = sanitize_unit_text(original)

    backup_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(backup_dir, 0o700)
    shutil.copy2(unit, backup_dir / unit.name)
    if dropin.exists():
        shutil.copy2(dropin, backup_dir / dropin.name)

    atomic_write(unit, sanitized, mode=unit.stat().st_mode & 0o777)
    atomic_write(dropin, policy, mode=0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=30)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--unit", type=Path, default=DEFAULT_UNIT)
    parser.add_argument("--dropin", type=Path, default=DEFAULT_DROPIN)
    args = parser.parse_args()

    policy = POLICY_SOURCE.read_text(encoding="utf-8")
    validate_policy_text(policy)
    current = args.unit.read_text(encoding="utf-8")
    _, unsupported = sanitize_unit_text(current)
    if not args.apply:
        print(
            f"check: unsupported={','.join(unsupported) or 'none'} "
            f"dropin={'current' if args.dropin.exists() and args.dropin.read_text(encoding='utf-8') == policy else 'missing-or-drifted'}"
        )
        return 0 if not unsupported and args.dropin.exists() and args.dropin.read_text(encoding="utf-8") == policy else 1
    if args.backup_dir is None:
        parser.error("--backup-dir is required with --apply")
    removed = install(unit=args.unit, dropin=args.dropin, backup_dir=args.backup_dir)
    print(f"installed; removed unsupported directives: {','.join(removed) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
