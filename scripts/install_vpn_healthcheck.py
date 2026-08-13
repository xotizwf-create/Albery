#!/usr/bin/env python3
"""Install the versioned Albery VPN healthcheck atomically without restarting services."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
SOURCE = BASE / "deploy" / "vpn-healthcheck.sh"
DEFAULT_TARGET = Path("/usr/local/sbin/vpn-healthcheck.sh")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate(content: bytes) -> None:
    text = content.decode("utf-8")
    required = (
        "PROBE_ATTEMPTS=${VPN_HEALTH_PROBE_ATTEMPTS:-3}",
        "policy route",
        'if [ "$OPENAI_CODE" = "401" ]',
        "RESULT: PROBLEM",
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(f"VPN healthcheck source is incomplete: {', '.join(missing)}")


def atomic_write(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o755)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install(target: Path, backup_dir: Path) -> None:
    if not target.is_absolute() or not backup_dir.is_absolute():
        raise RuntimeError("target and backup directory must be absolute")
    content = SOURCE.read_bytes()
    validate(content)
    backup_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(backup_dir, 0o700)
    if target.exists():
        shutil.copy2(target, backup_dir / target.name)
    atomic_write(target, content)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()

    source = SOURCE.read_bytes()
    validate(source)
    current = args.target.read_bytes() if args.target.is_file() else b""
    is_current = current == source and (args.target.stat().st_mode & 0o777) == 0o755 if current else False
    if not args.apply:
        print(
            f"check: {'current' if is_current else 'missing-or-drifted'} "
            f"sha256={digest(source)}"
        )
        return 0 if is_current else 1
    if args.backup_dir is None:
        parser.error("--backup-dir is required with --apply")
    install(args.target, args.backup_dir)
    print(f"installed: {args.target}; sha256={digest(source)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
