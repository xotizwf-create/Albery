from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.db


def test_manual_and_scheduled_runs_are_deduplicated_transactionally():
    import agent_automations as aa

    suffix = uuid.uuid4().hex[:12]
    row = {
        "agent_slug": f"test-{suffix}",
        "name": f"automation-{suffix}",
        "prompt": "test only",
        "deliver_to": "test-target",
    }
    with aa.pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_automations (agent_slug, name, schedule, prompt, kind) "
                    "VALUES (%s, %s, '0 0 * * *', %s, 'agent') RETURNING id",
                    (row["agent_slug"], row["name"], row["prompt"]),
                )
                row["id"] = int(cur.fetchone()["id"])
    try:
        now = aa.msk_now().replace(second=0, microsecond=0)
        first_manual = aa._enqueue_run(row, "manual", now)
        assert first_manual is not None
        assert aa._enqueue_run(row, "manual", now) is None

        first_schedule = aa._enqueue_run(row, "schedule", now)
        assert first_schedule is not None
        assert aa._enqueue_run(row, "schedule", now) is None

        with aa.pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_automation_runs SET status = 'done' WHERE id = %s",
                        (first_manual,),
                    )
        assert aa._enqueue_run(row, "manual", now) is not None
    finally:
        with aa.pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM agent_automations WHERE id = %s", (row["id"],))
