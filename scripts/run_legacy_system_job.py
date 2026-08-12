#!/usr/bin/env python3
"""Run the remaining allowlisted heavy system job under Albery's global Hermes limit.

Legacy Hermes cron owns scheduling and the script owns its domain-specific flock/state/retry. This
wrapper owns only server-wide admission control. It accepts a symbolic allowlisted key instead of an
arbitrary command, so neither the UI nor cron metadata can turn it into a shell execution surface.
"""
from __future__ import annotations

import logging
import hashlib
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from shared.run_slots import build_default

JOBS = {
    "zoom-to-tasks": (
        Path("/root/.hermes/scripts/zoom_watchdog.sh"),
        "6a6cdc04cffa8ba1b4bd7bffe63f654340b72acc1b14e7f710501c3f510def58",
    ),
}
SLOT_WAIT_SECONDS = 240
JOB_TIMEOUT_SECONDS = 900


def run(job_key: str) -> int:
    job = JOBS.get(str(job_key or "").strip())
    if job is None:
        logging.error("legacy system job is not allowlisted: %s", job_key)
        return 64
    command, expected_sha256 = job
    if not command.is_file():
        logging.error("legacy system job executable is absent: %s", job_key)
        return 66
    actual_sha256 = hashlib.sha256(command.read_bytes()).hexdigest()
    if not expected_sha256 or actual_sha256 != expected_sha256:
        logging.error("legacy system job refused: reviewed script checksum changed (%s)", job_key)
        return 78
    slot = build_default().acquire(SLOT_WAIT_SECONDS)
    if slot is None:
        logging.warning("legacy system job deferred: global Hermes slots are busy (%s)", job_key)
        return 75
    if slot.is_local_fallback:
        slot.release()
        logging.error("legacy system job refused: PostgreSQL global limit is unavailable (%s)", job_key)
        return 75
    try:
        completed = subprocess.run(
            [str(command)],
            cwd=str(command.parent),
            check=False,
            timeout=JOB_TIMEOUT_SECONDS,
        )
        return int(completed.returncode)
    except subprocess.TimeoutExpired:
        logging.error("legacy system job timed out after %ss (%s)", JOB_TIMEOUT_SECONDS, job_key)
        return 124
    finally:
        slot.release()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 64
    return run(argv[1])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
