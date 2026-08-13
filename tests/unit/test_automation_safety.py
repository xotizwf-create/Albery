from __future__ import annotations


def test_unknown_tools_fail_safe_as_mutating():
    from shared.automation_safety import is_mutating_tool

    assert is_mutating_tool("brand_new_tool") is True
    assert is_mutating_tool("update_bitrix_task") is True
    assert is_mutating_tool("delete_crm_deal") is True
    # A read-looking but unreviewed name is still a write until it enters the policy.
    assert is_mutating_tool("get_bitrix_task") is True
    assert is_mutating_tool("list_crm_deals") is False
    assert is_mutating_tool("workspace_get_conversation") is False


def test_related_writes_share_one_business_object_key():
    from shared.automation_safety import business_object_key

    assert business_object_key("update_bitrix_task", {"task_id": 42}) == "task:42"
    assert business_object_key("delete_bitrix_task", {"bitrix_task_id": "42"}) == "task:42"
    assert business_object_key("add_bitrix_task_comment", {"bitrix_task_id": 42}) == "task:42"


def test_effect_fingerprint_is_canonical_and_sensitive_to_arguments():
    from shared.automation_safety import effect_fingerprint

    first = effect_fingerprint("update_bitrix_task", {"task_id": 7, "fields": {"b": 2, "a": 1}})
    reordered = effect_fingerprint("update_bitrix_task", {"fields": {"a": 1, "b": 2}, "task_id": 7})
    changed = effect_fingerprint("update_bitrix_task", {"task_id": 7, "fields": {"a": 9, "b": 2}})
    assert first == reordered
    assert first != changed


def test_completed_mutation_returns_cached_result_without_reexecution(monkeypatch):
    import shared.automation_safety as safety

    monkeypatch.setattr(safety, "_effect_existing", lambda *_args: {
        "status": "done", "result_json": {"ok": True, "task_id": 7},
    })
    called = []
    result = safety.guarded_tool_call(
        "update_bitrix_task", {"bitrix_task_id": 7},
        lambda _args: called.append(True), automation_run_id=19,
    )
    assert result == {"ok": True, "task_id": 7}
    assert called == []


def test_ambiguous_prior_mutation_fails_closed(monkeypatch):
    import pytest
    import shared.automation_safety as safety

    monkeypatch.setattr(safety, "_effect_existing", lambda *_args: {
        "status": "started", "result_json": None,
    })
    with pytest.raises(safety.AutomationEffectAmbiguous):
        safety.guarded_tool_call(
            "delete_bitrix_task", {"bitrix_task_id": 7}, lambda _args: {"ok": True},
            automation_run_id=19,
        )


def test_durable_migration_is_registered_and_contains_stage_ledgers():
    from pathlib import Path
    from scripts import ensure_postgres

    assert "083_durable_agent_automation_runs.sql" in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    sql = (Path(__file__).resolve().parents[2] / "database" / "migrations" /
           "083_durable_agent_automation_runs.sql").read_text(encoding="utf-8")
    assert "agent_automation_runs" in sql
    assert "agent_automation_deliveries" in sql
    assert "agent_automation_tool_effects" in sql
    assert "agent_automation_one_active_manual_idx" in sql


def test_automation_runner_uses_shared_slots_and_automation_alias():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "agent_automations.py").read_text(encoding="utf-8")
    assert "from shared.run_slots import build_default" in source
    assert "automation-agent-{agent['slug']}" in source
    assert "threading.Timer" not in source
    assert "queue.Queue" not in source
    assert "retry_transient=False" in source


def test_brain_stage_holds_shared_slot_and_uses_automation_connector(monkeypatch):
    import app  # noqa: F401 - production import order
    import agent_automations as aa
    import agent_center
    import b24bot
    import shared.run_slots

    events = []

    class Slot:
        is_local_fallback = False

        def release(self):
            events.append("released")

    class Slots:
        def acquire(self, timeout):
            events.append(("acquire", timeout))
            return Slot()

    monkeypatch.setattr(agent_center, "_agent_by_slug", lambda _slug: {
        "slug": "main", "name": "Main", "is_active": True,
    })
    monkeypatch.setattr(shared.run_slots, "build_default", lambda: Slots())
    monkeypatch.setattr(aa, "_automation_prompt", lambda *_args: "prompt")
    monkeypatch.setattr(b24bot, "_hermes_answer_is_error", lambda _answer: False)

    class Proc:
        returncode = 0
        stdout = "ready"

    def fake_hermes(command, *_args):
        events.append(("command", command))
        return Proc(), None

    monkeypatch.setattr(aa, "_hermes_once", fake_hermes)
    monkeypatch.setattr(aa, "_prepare_delivery", lambda run, answer: events.append(("stored", run["id"], answer)))

    aa._process_brain({
        "id": 11,
        "automation_id": 3,
        "agent_slug": "main",
        "brain_attempts": 1,
        "automation_snapshot": {"id": 3, "name": "Daily", "prompt": "x", "deliver_to": "16"},
    })

    command = next(item[1] for item in events if isinstance(item, tuple) and item[0] == "command")
    assert "automation-agent-main" in command[command.index("-t") + 1]
    assert "released" in events
    assert ("stored", 11, "ready") in events


def test_delivery_stage_cannot_start_hermes_again():
    import app  # noqa: F401
    import agent_automations as aa
    import inspect

    source = inspect.getsource(aa._process_delivery)
    assert "_hermes_once" not in source
    assert "subprocess" not in source


def test_channel_neutral_telegram_migration_has_durable_ledgers_and_actor_mapping():
    from pathlib import Path
    from scripts import ensure_postgres

    name = "084_channel_neutral_telegram_agents.sql"
    assert name in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    sql = (Path(__file__).resolve().parents[2] / "database" / "migrations" / name).read_text(
        encoding="utf-8"
    )
    assert "telegram_agent_updates" in sql
    assert "telegram_agent_offsets" in sql
    assert "telegram_agent_outbox" in sql
    assert "bitrix_user_id" in sql
    assert "delivery_channel" in sql
    assert "delivery_conversation_id" in sql
    assert "UNIQUE (agent_slug, provider_update_id)" in sql
    assert "idempotency_key" in sql
