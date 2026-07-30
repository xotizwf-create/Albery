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


def _paused_dialog_responder(conv_before, message, job, executed_modes):
    """Отвечает как настоящая таблица: режим в UPDATE решает код, а не подделка теста."""

    def respond(sql, params):
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
            executed_modes.append(params[1])
            return {
                **conv_before,
                "status": params[0],
                "control_mode": params[1],
                "state_version": params[9],
            }
        if sql.startswith("INSERT INTO funnel_workspace_control_events"):
            return {"id": 81}
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

    return respond


def test_new_client_question_returns_a_paused_dialog_to_the_ai():
    """Жалоба владельца 28.07.2026: «написано ответы приостановлены».

    Передав разговор человеку, агент ставит режим `paused`. Дальше клиент пишет снова —
    и не получает НИЧЕГО: ИИ отключён, человек ещё не подошёл. Молчание — сломанная
    логика, поэтому новый вопрос клиента возвращает ход ИИ там, где ИИ разрешён.
    """

    conv_before = conversation(state_version=7, status="waiting", control_mode="paused")
    message = {
        "id": 51,
        "conversation_id": 41,
        "author_type": "client",
        "text": "Как я буду платить налоги?",
    }
    job = {
        "id": 61,
        "conversation_id": 41,
        "trigger_message_id": 51,
        "expected_version": 8,
        "processing_status": "pending",
    }
    modes: list[str] = []

    connect, connection = connect_factory(
        _paused_dialog_responder(conv_before, message, job, modes)
    )
    result = store.ingest_business_message(
        source_key="telegram_bot",
        external_chat_id="9001",
        external_message_id="100",
        text="Как я буду платить налоги?",
        author_type="client",
        occurred_at=NOW,
        schedule_ai=True,
        connect=connect,
    )

    assert modes == ["ai"]
    assert result["conversation"]["control_mode"] == "ai"
    assert result["ai_job"]["trigger_message_id"] == 51
    events = [
        params
        for sql, params in connection.cursor_instance.executed
        if sql.startswith("INSERT INTO funnel_workspace_control_events")
    ]
    assert len(events) == 1
    assert "paused" in events[0] and "ai" in events[0]


def test_paused_dialog_stays_paused_where_the_ai_is_switched_off():
    """Канал без ИИ не оживает от сообщения клиента: там отвечает только человек."""

    conv_before = conversation(state_version=7, status="waiting", control_mode="paused")
    message = {
        "id": 51,
        "conversation_id": 41,
        "author_type": "client",
        "text": "Добрый день!",
    }
    modes: list[str] = []

    connect, connection = connect_factory(
        _paused_dialog_responder(conv_before, message, None, modes)
    )
    result = store.ingest_business_message(
        external_chat_id="9001",
        external_message_id="100",
        text="Добрый день!",
        author_type="client",
        occurred_at=NOW,
        schedule_ai=False,
        connect=connect,
    )

    assert modes == ["paused"]
    assert result["ai_job"] is None
    assert not any(
        sql.startswith("INSERT INTO funnel_workspace_control_events")
        for sql, _ in connection.cursor_instance.executed
    )


def test_human_hold_is_not_taken_away_by_a_new_client_question():
    """Оператор, забравший диалог, остаётся за рулём: ИИ не перебивает человека."""

    conv_before = conversation(state_version=7, status="open", control_mode="human")
    message = {
        "id": 51,
        "conversation_id": 41,
        "author_type": "client",
        "text": "Ну что там?",
    }
    modes: list[str] = []

    connect, _connection = connect_factory(
        _paused_dialog_responder(conv_before, message, None, modes)
    )
    result = store.ingest_business_message(
        source_key="telegram_bot",
        external_chat_id="9001",
        external_message_id="100",
        text="Ну что там?",
        author_type="client",
        occurred_at=NOW,
        schedule_ai=True,
        connect=connect,
    )

    assert modes == ["human"]
    assert result["ai_job"] is None


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


def test_control_return_to_ai_clears_operator_unread_queue():
    before = conversation(
        state_version=7,
        control_mode="human",
        unread_count=4,
        last_message_id=91,
    )
    after = conversation(
        state_version=8,
        control_mode="ai",
        unread_count=0,
        last_read_message_id=91,
    )

    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return before
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return after
        if sql.startswith("INSERT INTO funnel_workspace_control_events"):
            return None
        if sql.startswith("SELECT client.id"):
            return None
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.transition_control(
        41,
        mode="ai",
        expected_version=7,
        actor_name="Александр",
        now=NOW,
        connect=connect,
    )

    update_sql = next(
        sql
        for sql, _params in connection.cursor_instance.executed
        if sql.startswith("UPDATE funnel_workspace_conversations")
    )
    assert "unread_count = CASE WHEN %s = 'ai' THEN 0" in update_sql
    assert "last_read_message_id = CASE" in update_sql
    assert result["unread_count"] == 0


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


def test_skipped_automatic_crm_action_does_not_change_local_stage():
    action = {
        "id": 70,
        "conversation_id": 41,
        "outbox_id": 9,
        "action_type": "move_stage",
        "target_stage": "C16:TERMS",
        "processing_status": "done",
    }

    def respond(sql, _params):
        if sql.startswith("UPDATE funnel_workspace_crm_actions"):
            return action
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            raise AssertionError("skipped automatic action must not change local stage")
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.complete_crm_action(
        70,
        worker_id="crm-worker",
        result={
            "status": "skipped",
            "reason": "automatic_stage_transitions_disabled",
        },
        now=NOW,
        connect=connect,
    )

    assert result == action
    assert len(connection.cursor_instance.executed) == 1


def test_conversation_search_looks_through_the_whole_retained_history():
    def respond(sql, _params):
        if "SELECT c.*, s.source_type" in sql:
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    store.list_conversations(q="договор", connect=connect)

    sql, params = connection.cursor_instance.executed[0]
    assert "FROM funnel_workspace_messages" in sql
    assert params.count("%договор%") == 6


def test_conversation_list_reports_how_long_the_client_waits_for_an_answer():
    """Срочность считается по переписке, а не по отдельному полю: вопрос без ответа —
    это клиентское сообщение новее последнего исходящего."""
    def respond(sql, _params):
        if "SELECT c.*, s.source_type" in sql:
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    store.list_conversations(connect=connect)

    sql, _params = connection.cursor_instance.executed[0]
    assert "awaiting_reply_since" in sql
    assert "author_type IN ('agent', 'operator')" in sql
    # Ответом клиенту не является то, чего он не видел: отменённое, неудавшееся и
    # удалённое нами. Иначе удаление своего ответа не вернуло бы прежний статус.
    assert "delivery_status NOT IN ('cancelled', 'failed')" in sql
    assert "(answer.metadata ->> 'telegram_deleted') IS DISTINCT FROM 'true'" in sql
    # Очередь разбора владельца: очень срочно → новый клиент → клиент ждёт ответа →
    # ждём ответа от клиента; внутри группы первым — кто ждёт дольше.
    assert "WHEN NOT has_answer THEN 2" in sql
    assert "awaiting_reply_since ASC NULLS LAST" in sql


def test_operator_stage_change_is_shown_at_once_and_queued_for_bitrix():
    """Сайт и Битрикс — одно целое: этап виден сразу, а в CRM его переставляет та же
    durable-очередь, что и после доставки сообщения."""
    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return conversation(stage_id="C16:NEW")
        if sql.startswith("SELECT id FROM funnel_workspace_messages"):
            return {"id": 55}
        if sql.startswith("INSERT INTO funnel_workspace_crm_actions"):
            assert "'move_stage'" in sql
            return {"id": 8, "action_type": "move_stage", "target_stage": "C16:NDA"}
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            assert "stage_id = %s" in sql
            return conversation(stage_id="C16:NDA")
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    result = store.enqueue_operator_stage_change(
        41,
        target_stage="C16:NDA",
        expected_version=7,
        operator_name="Юлия",
        now=NOW,
        connect=connect,
    )

    assert result["conversation"]["stage_id"] == "C16:NDA"
    assert result["crm_action"]["action_type"] == "move_stage"
    statements = [sql for sql, _ in connection.cursor_instance.executed]
    assert any(sql.startswith("INSERT INTO funnel_workspace_crm_actions") for sql in statements)


def test_deleting_a_conversation_removes_its_history_but_not_the_deal():
    """Сделка в Битриксе — карточка клиента, она живёт своей жизнью; удалять её вместе
    с журналом переписки никто не просил."""
    statements: list[str] = []

    def respond(sql, _params):
        statements.append(sql)
        if sql.startswith("SELECT c.id, c.display_name"):
            return {
                "id": 5,
                "display_name": "Иван",
                "username": "ivan",
                "external_chat_id": "9001",
                "deal_id": 188,
                "messages": 12,
            }
        if sql.startswith("DELETE FROM"):
            return None
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.delete_conversation(5, connect=connect)

    assert result == {
        "deleted": True,
        "conversation_id": 5,
        "messages": 12,
        "client": "Иван",
    }
    assert any(sql.startswith("DELETE FROM funnel_workspace_conversations") for sql in statements)
    # Ничего в CRM и ничего в сделках.
    assert not any("crm" in sql.lower() and sql.startswith("DELETE") for sql in statements)


def test_deleting_a_missing_conversation_is_reported_not_silent():
    def respond(sql, _params):
        if sql.startswith("SELECT c.id, c.display_name"):
            return None
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceNotFoundError):
        store.delete_conversation(404, connect=connect)


def test_urgency_filter_uses_the_same_threshold_as_the_badge():
    """«urgency» осталась ради инструментов агента: urgent — тот же срочный статус,
    working — «Ждём ответ клиента» (мы ответили последними)."""
    def respond(sql, _params):
        if "SELECT c.*, s.source_type" in sql:
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    store.list_conversations(urgency="urgent", connect=connect)
    urgent_sql, _params = connection.cursor_instance.executed[0]

    connect, connection = connect_factory(respond)
    store.list_conversations(urgency="working", connect=connect)
    working_sql, _params = connection.cursor_instance.executed[0]

    minutes = store.urgent_after_minutes()
    # Порог живёт и в ОТБОРЕ, и в порядке списка, поэтому сравнивать надо именно
    # условие отбора: в порядке он есть всегда, и без разделения тест ничего не ловит.
    urgent_filter = urgent_sql.split(") ranked")[0]
    working_filter = working_sql.split(") ranked")[0]
    assert f"interval '{minutes} minutes'" in urgent_filter
    # «Ждём ответ клиента» — про очередь хода, а не про время: порога в отборе нет.
    assert f"interval '{minutes} minutes'" not in working_filter
    assert "IS NULL" in working_filter


def test_unknown_urgency_is_refused():
    connect, _connection = connect_factory(lambda sql, params: [])

    with pytest.raises(store.WorkspaceValidationError):
        store.list_conversations(urgency="очень-очень", connect=connect)


def test_manager_request_is_available_as_a_status_filter():
    def respond(sql, _params):
        if "SELECT c.*, s.source_type" in sql:
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    store.list_conversations(urgency="manager_requested", connect=connect)
    sql = connection.cursor_instance.executed[0][0]

    assert "manager_requested_at" in sql
    assert "manager_request_handled_at" in sql


def test_migration_allows_a_stage_move_without_a_sent_message():
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "071_workspace_operator_stage.sql"
    ).read_text(encoding="utf-8")

    assert "funnel_workspace_crm_actions" in migration
    assert "move_stage" in migration
    assert "VALIDATE CONSTRAINT" in migration
    # Требование к самому этапу обязано сохраниться — иначе в CRM уедет мусор.
    assert "char_length(btrim(target_stage)) BETWEEN 1 AND 200" in migration


def test_operator_stage_change_respects_the_expected_version():
    def respond(sql, _params):
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return conversation(state_version=9)
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceConflictError):
        store.enqueue_operator_stage_change(
            41,
            target_stage="C16:NDA",
            expected_version=7,
            connect=connect,
        )


def test_unlinking_a_dead_deal_lets_the_backfill_create_a_new_one():
    """Backfill пропускает диалог, пока существует запись ensure_deal. Если её оставить,
    диалог с удалённой сделкой навсегда останется без карточки CRM."""
    statements: list[str] = []

    def respond(sql, _params):
        statements.append(sql)
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return {"id": 1, "deal_id": None, "stage_id": None}
        if sql.startswith("DELETE FROM funnel_workspace_crm_actions"):
            return None
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    row = store.unlink_conversation_deal(1, connect=connect)

    assert row["deal_id"] is None
    deletion = next(sql for sql in statements if sql.startswith("DELETE FROM funnel_workspace_crm_actions"))
    assert "action_type = 'ensure_deal'" in deletion
    assert "processing_status IN ('done', 'dead_letter')" in deletion


def test_conversation_list_filters_by_funnel_stage():
    # Этап — код сделки в Битриксе, а не наш перечень: белого списка тут быть не должно,
    # иначе новый этап у владельца молча перестанет фильтроваться.
    def respond(sql, _params):
        if "SELECT c.*, s.source_type" in sql:
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    store.list_conversations(stage="C16:NDA", connect=connect)

    sql, params = connection.cursor_instance.executed[0]
    assert "c.stage_id = %s" in sql
    assert "C16:NDA" in params


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


def test_work_state_filters_use_the_answer_fact_not_a_stored_field():
    """Три статуса считаются по переписке: «Клиент ждёт ответ» — пока последнее слово за
    ним, «Ждём ответ клиента» — пока за нами, «Очень срочно» — по времени ожидания."""
    def respond(sql, _params):
        if "SELECT c.*, s.source_type" in sql:
            return []
        raise AssertionError(sql)

    captured = {}
    for state in ("client_waiting", "waiting_client", "urgent"):
        connect, connection = connect_factory(respond)
        store.list_conversations(state=state, connect=connect)
        captured[state] = connection.cursor_instance.executed[0][0]

    assert "IS NOT NULL" in captured["client_waiting"]
    assert "IS NULL" in captured["waiting_client"]
    assert f"interval '{store.urgent_after_minutes()} minutes'" in captured["urgent"]
    # Ответом считается только то, что клиент ВИДЕЛ: отменённое, неудавшееся и удалённое
    # нами ответом не является — иначе ход считался бы сделанным, а клиент ничего не получил.
    assert "author_type IN ('agent', 'operator')" in captured["client_waiting"]
    assert "delivery_status NOT IN ('cancelled', 'failed')" in captured["client_waiting"]
    assert (
        "(answer.metadata ->> 'telegram_deleted') IS DISTINCT FROM 'true'"
        in captured["client_waiting"]
    )


def test_client_waiting_covers_a_dialog_where_we_never_answered():
    """Клиент, которому мы ещё ни разу не ответили, обязан попадать в «Клиент ждёт ответа»:
    после снятия статуса «Новый клиент» ему больше некуда деться, и потерять его нельзя."""
    def respond(sql, _params):
        if "SELECT c.*, s.source_type" in sql:
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    store.list_conversations(state="client_waiting", connect=connect)
    sql = connection.cursor_instance.executed[0][0]

    assert "NOT EXISTS" not in sql  # прежнее условие «мы уже отвечали» снято


def test_the_removed_new_client_filter_still_answers_with_a_list():
    """Убранный статус приходит из старых вкладок и вызовов инструментов агента — он обязан
    показать список «клиент ждёт ответа», а не 400."""
    def respond(sql, _params):
        if "SELECT c.*, s.source_type" in sql:
            return []
        raise AssertionError(sql)

    connect, connection = connect_factory(respond)
    store.list_conversations(state="new_client", connect=connect)
    sql = connection.cursor_instance.executed[0][0]

    assert "IS NOT NULL" in sql


def test_unknown_work_state_is_refused():
    connect, _connection = connect_factory(lambda sql, params: [])

    with pytest.raises(store.WorkspaceValidationError):
        store.list_conversations(state="в работе", connect=connect)


def test_reopening_reply_windows_never_touches_spam():
    """Открываем ответы всем, кроме помеченного спамом: спам открывать не просили."""
    statements: list[tuple[str, object]] = []

    def respond(sql, params):
        statements.append((sql, params))
        return []

    connect, _connection = connect_factory(respond)
    store.reopen_reply_windows(now=NOW, connect=connect)

    sql, _params = statements[0]
    assert sql.startswith("UPDATE funnel_workspace_conversations")
    assert "reply_deadline_at = %s" in sql
    assert "status <> 'spam'" in sql


def message_row(**overrides):
    base = {
        "id": 55,
        "conversation_id": 41,
        "author_type": "operator",
        "delivery_status": "sent",
        "text": "Старый текст",
        "provider_message_id": "712",
        "external_chat_id": "9001",
        "business_connection_id": "bc-1",
        "source_key": "telegram",
        "conversation_last_message_id": 55,
    }
    base.update(overrides)
    return base


def test_only_our_delivered_message_can_be_edited():
    """Слова клиента не наши, а неотправленный ответ надо отменять, а не править:
    иначе клиент увидит текст, которого оператор уже не писал."""
    def respond_client(sql, _params):
        if sql.startswith("SELECT m.*, c.external_chat_id"):
            return message_row(author_type="client")
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond_client)
    with pytest.raises(store.WorkspaceValidationError):
        store.edit_outgoing_message(55, text="Новый", connect=connect)

    def respond_pending(sql, _params):
        if sql.startswith("SELECT m.*, c.external_chat_id"):
            return message_row(delivery_status="pending")
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond_pending)
    with pytest.raises(store.WorkspaceControlError):
        store.edit_outgoing_message(55, text="Новый", connect=connect)


def test_edit_updates_the_text_and_the_conversation_preview():
    statements: list[str] = []

    def respond(sql, _params):
        statements.append(sql)
        if sql.startswith("SELECT m.*, c.external_chat_id"):
            return message_row()
        if sql.startswith("UPDATE funnel_workspace_messages"):
            return {**message_row(), "text": "Новый текст"}
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return None
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.edit_outgoing_message(
        55, text="Новый текст", actor_name="Юлия", now=NOW, connect=connect
    )

    assert result["message"]["text"] == "Новый текст"
    assert result["provider_message_id"] == "712"
    # Превью диалога тоже обязано обновиться — это было последнее сообщение.
    assert any(sql.startswith("UPDATE funnel_workspace_conversations") for sql in statements)


def test_deleted_message_uses_the_same_tombstone_as_a_client_delete():
    """Формат надгробия один на всю систему: два разных вида удалённых сообщений в одной
    переписке — верный способ запутать оператора."""
    def respond(sql, _params):
        if sql.startswith("SELECT m.*, c.external_chat_id"):
            return message_row(author_type="client", conversation_last_message_id=0)
        if sql.startswith("UPDATE funnel_workspace_messages"):
            assert "'[Сообщение удалено]'" in sql
            return {**message_row(), "text": "[Сообщение удалено]"}
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.delete_message_for_everyone(55, actor_name="Юлия", now=NOW, connect=connect)

    assert result["message"]["text"] == "[Сообщение удалено]"
    assert result["provider_message_id"] == "712"


def test_message_that_never_reached_the_client_is_removed_not_tombstoned():
    """Живой случай 27.07.2026: диалог 69 (Evgenii Pal), три ответа оператора легли с
    `failed` (`PEER_ID_INVALID`). Надгробие «[Сообщение удалено]» оставляет в переписке
    след от того, чего клиент никогда не видел, — такую запись надо убирать совсем."""
    statements: list[str] = []

    def respond(sql, params):
        statements.append(sql)
        if sql.startswith("SELECT m.*, c.external_chat_id"):
            return message_row(
                id=98,
                conversation_id=69,
                delivery_status="failed",
                provider_message_id=None,
                text="123 тест",
                conversation_last_message_id=98,
            )
        if sql.startswith("SELECT count(*) AS live FROM funnel_workspace_outbox"):
            return {"live": 0}
        if sql.startswith("DELETE FROM funnel_workspace_messages"):
            return None
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            return conversation(id=69, last_message_id=95, last_message_text="Предыдущее")
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    result = store.purge_undelivered_message(98, actor_name="Юлия", now=NOW, connect=connect)

    assert result["deleted"] is True
    assert result["message_id"] == 98
    assert result["conversation_id"] == 69
    # Запись именно удаляется, а не переписывается надгробием.
    assert any(sql.startswith("DELETE FROM funnel_workspace_messages") for sql in statements)
    assert not any("'[Сообщение удалено]'" in sql for sql in statements)
    # Превью и счётчик непрочитанного пересобираются по уцелевшей переписке.
    assert result["conversation"]["last_message_id"] == 95


def test_delivered_message_is_never_purged_from_the_journal():
    """У клиента сообщение осталось — вычистить его у себя значит соврать оператору."""
    def respond(sql, _params):
        if sql.startswith("SELECT m.*, c.external_chat_id"):
            return message_row(delivery_status="sent")
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceControlError):
        store.purge_undelivered_message(55, connect=connect)


def test_unknown_delivery_is_never_purged_from_the_journal():
    """`unknown` значит «Telegram мог принять»: удалять такую запись — терять улику."""
    def respond(sql, _params):
        if sql.startswith("SELECT m.*, c.external_chat_id"):
            return message_row(delivery_status="unknown")
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceControlError):
        store.purge_undelivered_message(55, connect=connect)


def test_client_message_is_never_purged_even_when_it_looks_undelivered():
    """Слова клиента — не наши, их нельзя вычищать из журнала ни при каком статусе."""
    def respond(sql, _params):
        if sql.startswith("SELECT m.*, c.external_chat_id"):
            return message_row(author_type="client", delivery_status="failed")
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceValidationError):
        store.purge_undelivered_message(55, connect=connect)


def test_message_still_in_the_send_queue_is_not_purged_under_the_sender():
    """Пока строка очереди жива, отправка может уйти в Telegram уже после удаления —
    клиент получил бы текст, которого в системе больше нет."""
    def respond(sql, _params):
        if sql.startswith("SELECT m.*, c.external_chat_id"):
            return message_row(delivery_status="failed", provider_message_id=None)
        if sql.startswith("SELECT count(*) AS live FROM funnel_workspace_outbox"):
            return {"live": 1}
        raise AssertionError(sql)

    connect, _connection = connect_factory(respond)
    with pytest.raises(store.WorkspaceControlError):
        store.purge_undelivered_message(55, connect=connect)
