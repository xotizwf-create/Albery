"""Write-back for SYSTEM automation rows (kind='system' in agent_automations).

Standalone cron scripts (funnel_control.py, novinki_watch.py) and in-app threads call
mark_system_run() after each run so the «Автоматизации» tab shows real last-run status
instead of «ещё не запускалась». No Flask imports — safe from any offline process.
A registry failure must never break the job itself: everything is swallowed and logged.
"""
from __future__ import annotations

import logging

from shared.db import connect

_RESULT_KEEP = 2000


def mark_system_run(system_key: str, status: str, result: str = "", error: str | None = None) -> None:
    """status: ok | error | silent (ran, nothing to report)."""
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_automations SET last_run_at = now(), last_status = %s, "
                    "last_result = %s, last_error = %s, updated_at = now() "
                    "WHERE kind = 'system' AND system_key = %s",
                    (status, (result or "")[:_RESULT_KEEP], (error or None), system_key),
                )
            conn.commit()
    except Exception:  # noqa: BLE001
        logging.warning("automation registry: mark %s failed", system_key, exc_info=True)
