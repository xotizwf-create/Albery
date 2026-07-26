from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

import funnel_workspace_store as store


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, responder):
        self.responder = responder
        self.executed: list[tuple[str, object]] = []
        self._one = None
        self._many = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.executed.append((normalized, params))
        result = self.responder(normalized, params)
        self._one = None
        self._many = []
        self.rowcount = 0
        if result is None:
            return
        if isinstance(result, list):
            self._many = result
            self.rowcount = len(result)
            return
        self._one = result
        self.rowcount = 1

    def fetchone(self):
        value = self._one
        self._one = None
        return value

    def fetchall(self):
        values = self._many
        self._many = []
        return values


class FakeConnection:
    def __init__(self, responder):
        self.cursor_instance = FakeCursor(responder)
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


def connect_factory(responder):
    connection = FakeConnection(responder)

    @contextmanager
    def connect():
        yield connection

    return connect, connection


def conversation(**overrides):
    base = {
        "id": 41,
        "source_key": "telegram",
        "external_chat_id": "9001",
        "business_connection_id": "bc-1",
        "status": "open",
        "control_mode": "ai",
        "state_version": 7,
        "reply_deadline_at": datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        "resume_at": None,
        "assigned_to": None,
    }
    base.update(overrides)
    return base


def test_migration_contains_durable_update_job_and_outbox_tables():
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "070_funnel_workspace.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS funnel_workspace_updates" in migration
    assert "CREATE TABLE IF NOT EXISTS funnel_workspace_ai_jobs" in migration
    assert "CREATE TABLE IF NOT EXISTS funnel_workspace_outbox" in migration
    assert "CREATE TABLE IF NOT EXISTS funnel_workspace_crm_actions" in migration
    assert "CREATE INDEX IF NOT EXISTS idx_fwu_business_head" in migration
    assert "CREATE INDEX IF NOT EXISTS idx_fwu_bot_head" in migration
    assert "action_type IN ('ensure_deal', 'delivery_effects', 'move_stage')" in migration
    assert "uq_fwca_ensure_conversation" in migration
    assert "WHERE processing_status = 'pending'" in migration
    assert "delivery_status IN ('pending', 'leased', 'sending', 'sent', 'failed', 'unknown', 'cancelled')" in migration
    assert "processing_status IN ('pending', 'leased', 'retry', 'done', 'dead_letter')" in migration
    assert "pg_get_constraintdef(oid)" in migration
    assert "NOT LIKE '%sending%'" in migration


def test_outgoing_telegram_echo_is_deduped_without_control_or_version_change():
    existing = {
        "id": 88,
        "conversation_id": 41,
        "external_message_id": None,
        "provider_message_id": "712",
        "author_type": "agent",
        "occurred_at": NOW,
    }

    def respond(sql, _params):
        if sql.startswith("INSERT INTO funnel_workspace_sources"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_conversations"):
            return conversation()
        if sql.startswith("SELECT * FROM funnel_workspace_messages"):
            return existing
        if sql.startswith("UPDATE funnel_workspace_messages SET external_message_id"):
            return {**existing, "external_message_id": "712"}
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.ingest_business_message(
        external_chat_id="9001",
        business_connection_id="bc-1",
        external_message_id="712",
        text="Уже отправленный ответ",
        author_type="operator",
        occurred_at=NOW,
        connect=connect,
    )

    assert result["duplicate"] is True
    assert result["message"]["external_message_id"] == "712"
    statements = [sql for sql, _ in connection.cursor_instance.executed]
    assert not any(
        sql.startswith("UPDATE funnel_workspace_conversations")
        for sql in statements
    )
    assert not any("funnel_workspace_control_events" in sql for sql in statements)


def test_business_bot_echo_reconciles_inflight_outbox_atomically():
    message = {
        "id": 88,
        "conversation_id": 41,
        "external_message_id": None,
        "provider_message_id": None,
        "author_type": "agent",
        "text": "Ответ",
    }
    candidate = {
        "id": 99,
        "conversation_id": 41,
        "message_id": 88,
        "delivery_status": "sending",
        "provider_message_id": None,
        "text": "Ответ",
        "external_chat_id": "9001",
        "conversation_version": 7,
        "author_type": "agent",
        "payload": {"asset": "terms"},
        "message": message,
    }
    delivery_action = {
        "id": 100,
        "conversation_id": 41,
        "message_id": 88,
        "outbox_id": 99,
        "action_type": "delivery_effects",
        "processing_status": "pending",
    }

    def respond(sql, _params):
        if sql.startswith("INSERT INTO funnel_workspace_sources"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_conversations"):
            return conversation()
        if sql.startswith("SELECT o.*, row_to_json(m) AS message"):
            return candidate
        if sql.startswith("UPDATE funnel_workspace_outbox"):
            return {**candidate, "delivery_status": "sent", "provider_message_id": "712"}
        if sql.startswith("UPDATE funnel_workspace_messages"):
            return {
                **message,
                "external_message_id": "712",
                "provider_message_id": "712",
                "delivery_status": "sent",
            }
        if sql.startswith("INSERT INTO funnel_workspace_crm_actions"):
            return delivery_action
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.ingest_business_message(
        external_chat_id="9001",
        business_connection_id="bc-1",
        external_message_id="712",
        text="Ответ",
        author_type="agent",
        occurred_at=NOW,
        metadata={"sent_via_business_bot": True},
        connect=connect,
    )

    assert result["duplicate"] is True
    assert result["reconciled_echo"] is True
    assert result["message"]["provider_message_id"] == "712"
    assert result["outbox"]["delivery_status"] == "sent"
    assert result["delivery_action"] == delivery_action
    assert not any(
        sql.startswith("INSERT INTO funnel_workspace_messages")
        for sql, _ in connection.cursor_instance.executed
    )


@pytest.mark.parametrize(
    ("target_conversation_id", "text", "author_type"),
    [
        (42, "Первый ответ", "operator"),
        (41, "Другой ответ", "operator"),
        (41, "Первый ответ", "agent"),
    ],
)
def test_global_idempotency_key_rejects_different_message(
    target_conversation_id,
    text,
    author_type,
):
    existing = {
        "id": 99,
        "conversation_id": 41,
        "message_id": 88,
        "author_type": "operator",
        "text": "Первый ответ",
        "idempotency_key": "same-key",
        "message": {
            "id": 88,
            "conversation_id": 41,
            "author_type": "operator",
            "text": "Первый ответ",
        },
        "conversation": conversation(),
    }

    def respond(sql, _params):
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            return None
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return conversation(id=target_conversation_id)
        if sql.startswith("SELECT o.*, row_to_json(m) AS message"):
            return dict(existing)
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceConflictError):
        if author_type == "operator":
            store.enqueue_outgoing_operator(
                target_conversation_id,
                text=text,
                expected_version=7,
                operator_name="Александр",
                idempotency_key="same-key",
                connect=connect,
            )
        else:
            store.enqueue_outgoing_agent(
                target_conversation_id,
                text=text,
                expected_version=7,
                idempotency_key="same-key",
                connect=connect,
            )


def test_ingest_does_not_schedule_ai_without_explicit_rollout_flag():
    conv_before = conversation(state_version=1, status="new")
    conv_after = conversation(state_version=2, status="new")
    message = {
        "id": 51,
        "conversation_id": 41,
        "author_type": "client",
        "text": "Здравствуйте",
    }

    def respond(sql, _params):
        if sql.startswith("INSERT INTO funnel_workspace_sources"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_conversations"):
            return conv_before
        if sql.startswith("SELECT * FROM funnel_workspace_messages"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_messages"):
            return message
        if "UPDATE funnel_workspace_outbox" in sql:
            return None
        if sql.startswith("UPDATE funnel_workspace_ai_jobs"):
            return None
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return conv_after
        if sql.startswith("INSERT INTO funnel_workspace_crm_actions"):
            return {
                "id": 71,
                "conversation_id": 41,
                "message_id": 51,
                "action_type": "ensure_deal",
                "processing_status": "pending",
            }
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.ingest_business_message(
        external_chat_id="9001",
        external_message_id="100",
        text="Здравствуйте",
        author_type="client",
        occurred_at=NOW,
        connect=connect,
    )

    assert result["ai_job"] is None
    assert result["crm_ensure_action"]["action_type"] == "ensure_deal"
    assert not any(
        sql.startswith("INSERT INTO funnel_workspace_ai_jobs")
        for sql, _ in connection.cursor_instance.executed
    )


def test_ingest_schedules_debounced_ai_when_explicitly_enabled():
    conv_before = conversation(state_version=1, status="new")
    conv_after = conversation(state_version=2, status="new")
    message = {
        "id": 51,
        "conversation_id": 41,
        "author_type": "client",
        "text": "Здравствуйте",
    }
    job = {
        "id": 61,
        "conversation_id": 41,
        "trigger_message_id": 51,
        "expected_version": 2,
        "processing_status": "pending",
    }

    def respond(sql, _params):
        if sql.startswith("INSERT INTO funnel_workspace_sources"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_conversations"):
            return conv_before
        if sql.startswith("SELECT * FROM funnel_workspace_messages"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_messages"):
            return message
        if "UPDATE funnel_workspace_outbox" in sql:
            return None
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return conv_after
        if sql.startswith("UPDATE funnel_workspace_ai_jobs"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_ai_jobs"):
            return job
        if sql.startswith("INSERT INTO funnel_workspace_crm_actions"):
            return {
                "id": 71,
                "conversation_id": 41,
                "message_id": 51,
                "action_type": "ensure_deal",
                "processing_status": "pending",
            }
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.ingest_business_message(
        external_chat_id="9001",
        external_message_id="100",
        text="Здравствуйте",
        author_type="client",
        occurred_at=NOW,
        schedule_ai=True,
        connect=connect,
    )

    assert result["ai_job"]["expected_version"] == 2
    assert result["ai_job"]["trigger_message_id"] == 51


def test_edit_updates_client_tombstone_and_invalidates_old_ai_work():
    conv_before = conversation(state_version=7)
    conv_after = conversation(state_version=8)
    original = {
        "id": 51,
        "conversation_id": 41,
        "external_message_id": "100",
        "author_type": "client",
        "text": "Старый текст",
    }
    edited = {**original, "text": "Новый текст"}

    def respond(sql, _params):
        if sql.startswith("INSERT INTO funnel_workspace_sources"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_conversations"):
            return conv_before
        if sql.startswith("SELECT * FROM funnel_workspace_messages"):
            return original
        if sql.startswith("UPDATE funnel_workspace_messages SET text"):
            return edited
        if "UPDATE funnel_workspace_outbox" in sql:
            return None
        if sql.startswith("UPDATE funnel_workspace_ai_jobs"):
            return None
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return conv_after
        if sql.startswith("INSERT INTO funnel_workspace_crm_actions"):
            return {
                "id": 71,
                "conversation_id": 41,
                "message_id": 51,
                "action_type": "ensure_deal",
                "processing_status": "pending",
            }
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.ingest_business_message(
        external_chat_id="9001",
        business_connection_id="bc-1",
        external_message_id="100",
        text="Новый текст",
        author_type="client",
        occurred_at=NOW,
        is_edit=True,
        connect=connect,
    )

    assert result["edited"] is True
    assert result["conversation"]["state_version"] == 8
    assert result["message"]["text"] == "Новый текст"
    assert any(
        "processing_status IN ('pending', 'leased')" in sql
        for sql, _ in connection.cursor_instance.executed
    )


def test_edit_reschedules_the_latest_unanswered_client_message():
    conv_before = conversation(state_version=7)
    conv_after = conversation(state_version=8)
    original = {
        "id": 51,
        "conversation_id": 41,
        "external_message_id": "100",
        "author_type": "client",
        "text": "Старый текст",
    }
    edited = {**original, "text": "Новый текст"}
    scheduled = {
        "id": 91,
        "conversation_id": 41,
        "trigger_message_id": 75,
        "expected_version": 8,
        "processing_status": "pending",
    }

    def respond(sql, params):
        if sql.startswith("INSERT INTO funnel_workspace_sources"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_conversations"):
            return conv_before
        if sql.startswith("SELECT * FROM funnel_workspace_messages"):
            return original
        if sql.startswith("UPDATE funnel_workspace_messages SET text"):
            return edited
        if "UPDATE funnel_workspace_outbox" in sql:
            return None
        if sql.startswith("UPDATE funnel_workspace_ai_jobs"):
            return None
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return conv_after
        if sql.startswith("SELECT client.id"):
            assert "cancelled_outbox.cancel_requested = true" in sql
            return {"id": 75}
        if sql.startswith("INSERT INTO funnel_workspace_ai_jobs"):
            assert params[1] == 75
            return scheduled
        if sql.startswith("INSERT INTO funnel_workspace_crm_actions"):
            return {
                "id": 71,
                "conversation_id": 41,
                "message_id": 51,
                "action_type": "ensure_deal",
                "processing_status": "pending",
            }
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.ingest_business_message(
        external_chat_id="9001",
        business_connection_id="bc-1",
        external_message_id="100",
        text="Новый текст",
        author_type="client",
        occurred_at=NOW,
        schedule_ai=True,
        is_edit=True,
        connect=connect,
    )

    assert result["ai_job"]["trigger_message_id"] == 75
    assert result["ai_job"]["trigger_message_id"] != original["id"]


def test_delete_tombstones_client_message_and_invalidates_old_ai_work():
    conv_before = conversation(state_version=7, last_message_id=51)
    conv_after = conversation(
        state_version=8,
        last_message_id=51,
        last_message_text="[Сообщение удалено]",
    )
    original = {
        "id": 51,
        "conversation_id": 41,
        "external_message_id": "100",
        "author_type": "client",
        "text": "Удалить меня",
    }
    tombstone = {**original, "text": "[Сообщение удалено]"}

    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return conv_before
        if sql.startswith("SELECT * FROM funnel_workspace_messages"):
            return [original]
        if sql.startswith("UPDATE funnel_workspace_messages SET text"):
            return [tombstone]
        if "UPDATE funnel_workspace_outbox" in sql:
            return None
        if sql.startswith("UPDATE funnel_workspace_ai_jobs"):
            return None
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return conv_after
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.tombstone_business_messages(
        external_chat_id="9001",
        external_message_ids=["100"],
        business_connection_id="bc-1",
        occurred_at=NOW,
        connect=connect,
    )

    assert result["message_id"] == 51
    assert result["messages"][0]["text"] == "[Сообщение удалено]"
    assert result["conversation"]["state_version"] == 8
    assert any(
        "processing_status IN ('pending', 'leased')" in sql
        for sql, _ in connection.cursor_instance.executed
    )


def test_crm_link_does_not_change_conversation_state_version():
    before = conversation(state_version=19)
    after = conversation(
        state_version=19,
        deal_id=123,
        funnel_id=16,
        stage_id="C16:NEW",
    )

    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return before
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            assert "state_version =" not in sql
            assert "deal_id = COALESCE(deal_id, %s)" in sql
            return after
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.update_crm_link(
        41,
        deal_id=123,
        funnel_id=16,
        stage_id="C16:NEW",
        expected_version=1,  # ignored: CRM metadata cannot invalidate the AI turn
        connect=connect,
    )

    assert result["state_version"] == 19
    assert result["deal_id"] == 123


def test_crm_link_never_overwrites_existing_concurrent_winner():
    winner = conversation(
        state_version=19,
        deal_id=777,
        funnel_id=16,
        stage_id="C16:NEW",
    )

    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return winner
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.update_crm_link(
        41,
        deal_id=888,
        funnel_id=16,
        stage_id="C16:CONTACTED",
        connect=connect,
    )

    assert result["deal_id"] == 777
    assert result["stage_id"] == "C16:NEW"
    assert [
        sql
        for sql, _ in connection.cursor_instance.executed
        if sql.startswith("UPDATE funnel_workspace_conversations")
    ] == []


def test_control_transition_takes_human_lease_and_advances_version():
    before = conversation(state_version=7, control_mode="ai")
    after = conversation(
        state_version=8,
        control_mode="human",
        assigned_to="Александр",
        resume_at=datetime(2026, 7, 26, 12, 2, tzinfo=timezone.utc),
    )

    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return before
        if sql.startswith("SELECT id FROM funnel_workspace_outbox"):
            return None
        if "UPDATE funnel_workspace_outbox" in sql:
            return None
        if sql.startswith("UPDATE funnel_workspace_ai_jobs"):
            return None
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return after
        if sql.startswith("INSERT INTO funnel_workspace_control_events"):
            return None
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.transition_control(
        41,
        mode="human",
        expected_version=7,
        actor_name="Александр",
        now=NOW,
        connect=connect,
    )

    assert result["control_mode"] == "human"
    assert result["state_version"] == 8
    assert result["resume_at"] == datetime(2026, 7, 26, 12, 2, tzinfo=timezone.utc)


def test_human_takeover_rejects_an_ai_send_already_at_provider_boundary():
    before = conversation(state_version=7, control_mode="ai")

    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return before
        if sql.startswith("SELECT id FROM funnel_workspace_outbox"):
            return {"id": 99}
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceConflictError) as exc_info:
        store.transition_control(
            41,
            mode="human",
            expected_version=7,
            actor_name="Александр",
            now=NOW,
            connect=connect,
        )

    assert exc_info.value.details["reason"] == "ai_send_in_progress"
    assert exc_info.value.details["outbox_id"] == 99
    assert not any(
        sql.startswith("UPDATE funnel_workspace_conversations")
        for sql, _ in connection.cursor_instance.executed
    )


def test_operator_reply_rejects_an_ai_send_already_at_provider_boundary():
    before = conversation(state_version=7, control_mode="ai")

    def respond(sql, _params):
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            return None
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return before
        if sql.startswith("SELECT o.*, row_to_json(m) AS message"):
            return None
        if sql.startswith("SELECT id FROM funnel_workspace_outbox"):
            return {"id": 99}
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceConflictError) as exc_info:
        store.enqueue_outgoing_operator(
            41,
            text="Ответ менеджера",
            expected_version=7,
            operator_name="Александр",
            idempotency_key="operator:41:request-1",
            now=NOW,
            connect=connect,
        )

    assert exc_info.value.details["reason"] == "ai_send_in_progress"
    assert not any(
        sql.startswith("INSERT INTO funnel_workspace_messages")
        for sql, _ in connection.cursor_instance.executed
    )


def test_status_change_rejects_an_ai_send_already_at_provider_boundary():
    before = conversation(state_version=7, control_mode="ai")

    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return before
        if sql.startswith("SELECT id FROM funnel_workspace_outbox"):
            return {"id": 99}
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceConflictError) as exc_info:
        store.update_conversation_status(
            41,
            status="closed",
            expected_version=7,
            actor_name="Александр",
            now=NOW,
            connect=connect,
        )

    assert exc_info.value.details["reason"] == "ai_send_in_progress"
    assert not any(
        sql.startswith("UPDATE funnel_workspace_conversations")
        for sql, _ in connection.cursor_instance.executed
    )


def test_human_takeover_sql_cancels_pending_and_marks_leased_ai_work():
    source = Path(store.__file__).read_text(encoding="utf-8")

    assert "delivery_status = 'cancelled'" in source
    assert "cancel_requested = true" in source
    assert "funnel_workspace_ai_jobs" in source
    assert "processing_status IN ('pending', 'leased')" in source


def test_outbox_recovery_distinguishes_reservation_from_provider_call():
    source = Path(store.__file__).read_text(encoding="utf-8")
    recover = source[
        source.index("def _recover_outbox_cursor("):
        source.index("def _cancel_stale_agent_outbox")
    ]

    assert "WHERE delivery_status = 'leased'" in recover
    assert "delivery_status = 'pending'" in recover
    assert "WHERE delivery_status = 'sending'" in recover
    assert "delivery_status = 'unknown'" in recover


def test_begin_outbox_send_crosses_provider_boundary_atomically():
    sending = {
        "id": 9,
        "conversation_id": 41,
        "delivery_status": "sending",
        "locked_by": "worker",
    }

    def respond(sql, _params):
        if sql.startswith("SELECT conversation_id FROM funnel_workspace_outbox"):
            return {"conversation_id": 41}
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return conversation()
        if sql.startswith("UPDATE funnel_workspace_outbox o"):
            assert "delivery_status = 'sending'" in sql
            assert "o.delivery_status = 'leased'" in sql
            return sending
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.begin_outbox_send(
        9,
        worker_id="worker",
        now=NOW,
        connect=connect,
    )

    assert result["allowed"] is True
    assert result["outbox"]["delivery_status"] == "sending"
    statements = [sql for sql, _ in connection.cursor_instance.executed]
    assert statements.index(
        "SELECT * FROM funnel_workspace_conversations WHERE id = %s FOR UPDATE"
    ) < next(
        index
        for index, sql in enumerate(statements)
        if sql.startswith("UPDATE funnel_workspace_outbox o")
    )


def test_claim_updates_enforces_strict_business_head_of_line():
    def respond(sql, params):
        if sql.startswith("UPDATE funnel_workspace_updates"):
            assert "source_key = %s" in sql
            assert "NOT (payload ? 'message')" in sql
            assert params[2] == "telegram"
            return None
        if sql.startswith("WITH head AS MATERIALIZED"):
            assert "u.processing_status IN ('pending', 'processing', 'retry')" in sql
            assert "NOT (u.payload ? 'message')" in sql
            assert "u.available_at <= %s" in sql
            assert "SKIP LOCKED" not in sql
            assert sql.index("ORDER BY u.id") < sql.index("u.available_at <= %s")
            return []
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    assert store.claim_updates(
        worker_id="business-worker",
        lane="business",
        source_key="telegram",
        limit=25,
        now=NOW,
        connect=connect,
    ) == []


def test_claim_updates_isolates_bot_lane_and_rejects_unknown_lane():
    def respond(sql, _params):
        if sql.startswith("UPDATE funnel_workspace_updates"):
            assert "(payload ? 'message')" in sql
            assert "NOT (payload ? 'message')" not in sql
            return None
        if sql.startswith("WITH head AS MATERIALIZED"):
            assert "(u.payload ? 'message')" in sql
            assert "NOT (u.payload ? 'message')" not in sql
            return []
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    assert store.claim_updates(
        worker_id="bot-worker",
        lane="bot",
        now=NOW,
        connect=connect,
    ) == []
    with pytest.raises(store.WorkspaceValidationError):
        store.claim_updates(worker_id="worker", lane="other", connect=connect)


def test_finish_outbox_accepts_echo_that_won_the_commit_race():
    message = {
        "id": 88,
        "conversation_id": 41,
        "delivery_status": "sent",
        "provider_message_id": "712",
    }
    sent = {
        "id": 9,
        "conversation_id": 41,
        "message_id": 88,
        "delivery_status": "sent",
        "provider_message_id": "712",
        "message": message,
    }

    def respond(sql, _params):
        if sql.startswith("UPDATE funnel_workspace_outbox"):
            return None
        if sql.startswith("SELECT o.*, row_to_json(m) AS message"):
            return dict(sent)
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.finish_outbox(
        9,
        worker_id="worker",
        result="unknown",
        error="timeout",
        now=NOW,
        connect=connect,
    )

    assert result["outbox"]["delivery_status"] == "sent"
    assert result["message"]["provider_message_id"] == "712"


def test_confirmed_delivery_enqueues_crm_stage_in_same_transaction():
    message = {
        "id": 88,
        "conversation_id": 41,
        "delivery_status": "sent",
        "provider_message_id": "712",
    }
    sent = {
        "id": 9,
        "conversation_id": 41,
        "message_id": 88,
        "delivery_status": "sent",
        "provider_message_id": "712",
        "external_chat_id": "9001",
        "conversation_version": 8,
        "author_type": "agent",
        "payload": {
            "stage_move": "C16:TERMS",
            "asset": "terms",
            "escalate_after_delivery": False,
        },
    }
    delivery_action = {
        "id": 69,
        "conversation_id": 41,
        "message_id": 88,
        "outbox_id": 9,
        "action_type": "delivery_effects",
        "target_stage": None,
        "idempotency_key": "delivery-effects:outbox:9",
        "processing_status": "pending",
    }
    action = {
        "id": 70,
        "conversation_id": 41,
        "message_id": 88,
        "outbox_id": 9,
        "action_type": "move_stage",
        "target_stage": "C16:TERMS",
        "idempotency_key": "crm-stage:outbox:9:C16:TERMS",
        "processing_status": "pending",
    }

    def respond(sql, _params):
        if sql.startswith("UPDATE funnel_workspace_outbox"):
            return sent
        if sql.startswith("UPDATE funnel_workspace_messages"):
            return message
        if sql.startswith("INSERT INTO funnel_workspace_crm_actions"):
            return (
                delivery_action
                if "'delivery_effects'" in sql
                else action
            )
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.finish_outbox(
        9,
        worker_id="worker",
        result="sent",
        provider_message_id="712",
        now=NOW,
        connect=connect,
    )

    assert result["crm_action"] == action
    assert result["delivery_action"] == delivery_action
    statements = [sql for sql, _ in connection.cursor_instance.executed]
    assert statements.index(
        next(sql for sql in statements if sql.startswith("UPDATE funnel_workspace_outbox"))
    ) < statements.index(
        next(sql for sql in statements if sql.startswith("INSERT INTO funnel_workspace_crm_actions"))
    )


def test_crm_action_enqueue_is_idempotent_and_rejects_payload_drift():
    outbox = {
        "id": 9,
        "conversation_id": 41,
        "message_id": 88,
        "delivery_status": "sent",
        "provider_message_id": "712",
        "payload": {"stage_move": "C16:TERMS"},
    }
    existing = {
        "id": 70,
        "conversation_id": 41,
        "message_id": 88,
        "outbox_id": 9,
        "action_type": "move_stage",
        "target_stage": "C16:TERMS",
        "idempotency_key": "crm-stage:outbox:9:C16:TERMS",
        "processing_status": "done",
    }

    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_outbox"):
            return outbox
        if sql.startswith("INSERT INTO funnel_workspace_crm_actions"):
            return None
        if sql.startswith("SELECT * FROM funnel_workspace_crm_actions"):
            return existing
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.ensure_crm_action_for_sent_outbox(9, connect=connect)

    assert result == existing


def test_crm_action_retry_becomes_dead_letter_at_bounded_attempt_limit():
    dead = {
        "id": 70,
        "attempts": 8,
        "max_attempts": 8,
        "processing_status": "dead_letter",
    }

    def respond(sql, _params):
        if sql.startswith("UPDATE funnel_workspace_crm_actions"):
            assert "WHEN attempts >= max_attempts THEN 'dead_letter'" in sql
            return dead
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.retry_crm_action(
        70,
        worker_id="crm-worker",
        error="Bitrix unavailable",
        delay_seconds=3600,
        now=NOW,
        connect=connect,
    )

    assert result["processing_status"] == "dead_letter"


def test_crm_action_claim_serializes_stage_sets_per_conversation():
    source = Path(store.__file__).read_text(encoding="utf-8")
    claim = source[
        source.index("def claim_crm_actions("):
        source.index("def complete_crm_action(")
    ]

    assert "FOR UPDATE OF a SKIP LOCKED" in claim
    assert "earlier.conversation_id = a.conversation_id" in claim
    assert "earlier.id < a.id" in claim
    assert "'pending', 'leased', 'retry'" in claim


def test_conversation_search_looks_through_the_whole_retained_history():
    def respond(sql, _params):
        if sql.startswith("SELECT c.*, s.source_type"):
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    store.list_conversations(q="договор", connect=connect)

    sql, params = connection.cursor_instance.executed[0]
    assert "FROM funnel_workspace_messages" in sql
    assert params.count("%договор%") == 6


def test_retention_drains_the_backlog_instead_of_one_batch_per_run():
    remaining = {"messages": 2500}

    def respond(sql, _params):
        if "DELETE FROM funnel_workspace_messages" in sql:
            batch = min(1000, remaining["messages"])
            remaining["messages"] -= batch
            return [{"conversation_id": 41}] * batch
        if sql.startswith("DELETE FROM") or "DELETE FROM" in sql:
            return []
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.retention_cleanup(days=30, batch_size=1000, now=NOW, connect=connect)

    assert remaining["messages"] == 0
    assert result["messages"] == 2500
    # Каждая партия — своя транзакция, иначе чистка держит блокировки целиком.
    assert connection.commits >= 3


def test_retention_never_deletes_a_message_the_live_queues_still_need():
    source = Path(store.__file__).read_text(encoding="utf-8")
    cleanup = source[
        source.index("def retention_cleanup("):
        source.index("def message_export_rows(")
    ]

    assert "NOT EXISTS" in cleanup
    assert "funnel_workspace_outbox" in cleanup
    assert "delivery_status NOT IN ('sent', 'cancelled')" in cleanup
    assert "funnel_workspace_crm_actions" in cleanup
    assert "funnel_workspace_ai_jobs" in cleanup
    assert "processing_status IN ('pending', 'leased', 'retry')" in cleanup


def test_retention_rebuilds_conversation_counters_after_deleting_history():
    passes = {"messages": 1}

    def respond(sql, _params):
        if "DELETE FROM funnel_workspace_messages" in sql:
            if passes["messages"]:
                passes["messages"] = 0
                return [{"conversation_id": 41}, {"conversation_id": 41}]
            return []
        if "DELETE FROM" in sql:
            return []
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    store.retention_cleanup(days=30, batch_size=1000, now=NOW, connect=connect)

    refresh = [
        (sql, params)
        for sql, params in connection.cursor_instance.executed
        if sql.startswith("UPDATE funnel_workspace_conversations")
    ]
    assert refresh, "счётчики диалога не пересобраны после удаления истории"
    sql, params = refresh[0]
    assert "unread_count =" in sql
    assert "last_message_id =" in sql
    assert "last_message_text =" in sql
    assert "last_read_message_id =" in sql
    assert params[-1] == [41]
