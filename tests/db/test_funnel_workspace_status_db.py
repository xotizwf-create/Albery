"""Статусы обращения, очередь разбора и полный перехват — на настоящем PostgreSQL.

Эти правила живут в SQL (признак «ответили ли мы» и порядок списка), поэтому проверять их
подделанным курсором бессмысленно: тест обязан гонять тот же запрос, что и прод.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

import funnel_workspace_store as store
from shared.db import connect


pytestmark = pytest.mark.db


def _ingest(source_key, chat_id, *, author, text, message_id, **kwargs):
    return store.ingest_business_message(
        source_key=source_key,
        external_chat_id=chat_id,
        external_message_id=message_id,
        text=text,
        author_type=author,
        business_connection_id=f"bc-{source_key}",
        external_user_id=int(chat_id),
        **kwargs,
    )


def _cleanup(source_key):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM funnel_workspace_updates WHERE source_key = %s",
                (source_key,),
            )
            cur.execute(
                "DELETE FROM funnel_workspace_conversations WHERE source_key = %s",
                (source_key,),
            )
            cur.execute(
                "DELETE FROM funnel_workspace_sources WHERE source_key = %s",
                (source_key,),
            )


def _row_of(source_key, conversation_id):
    listing = store.list_conversations(source=source_key, limit=250)
    for row in listing["items"]:
        if int(row["id"]) == int(conversation_id):
            return row
    raise AssertionError(f"диалог {conversation_id} не найден в списке")


def test_deleting_our_answer_returns_the_conversation_to_the_previous_status():
    """Требование владельца: удалили свой ответ — обращение снова ждёт нашего ответа.

    Надгробие в переписке остаётся, но ответом клиенту оно уже не является: клиент этого
    текста больше не видит.
    """
    suffix = uuid4().hex[:12]
    source_key = f"test-status-delete-{suffix}"
    chat_id = "700000001"
    try:
        store.ensure_source(source_key, source_type="test", display_name="status delete")
        _ingest(source_key, chat_id, author="client", text="Здравствуйте",
                message_id=f"{suffix}-1")
        answer = _ingest(source_key, chat_id, author="operator", text="Добрый день!",
                         message_id=f"{suffix}-2")
        conversation_id = int(answer["conversation"]["id"])

        after_answer = _row_of(source_key, conversation_id)
        assert after_answer["has_answer"] is True
        assert after_answer["awaiting_reply_since"] is None

        store.delete_message_for_everyone(int(answer["message"]["id"]), actor_name="Оператор")

        after_delete = _row_of(source_key, conversation_id)
        assert after_delete["has_answer"] is False
        assert after_delete["awaiting_reply_since"] is not None
    finally:
        _cleanup(source_key)


def test_question_during_human_lease_is_scheduled_when_ai_returns():
    """A client turn received during the manager lease must not disappear."""

    suffix = uuid4().hex[:12]
    source_key = f"test-lease-question-{suffix}"
    chat_id = "700000203"
    try:
        store.ensure_source(
            source_key,
            source_type="test",
            display_name="lease pending question",
        )
        first = _ingest(
            source_key,
            chat_id,
            author="client",
            text="Первый вопрос",
            message_id=f"{suffix}-1",
        )
        conversation_id = int(first["conversation"]["id"])
        held = store.transition_control(
            conversation_id,
            mode="human",
            expected_version=int(first["conversation"]["state_version"]),
            actor_name="Юлия",
        )
        pending = _ingest(
            source_key,
            chat_id,
            author="client",
            text="Вопрос, заданный менеджеру",
            message_id=f"{suffix}-2",
            schedule_ai=True,
        )
        assert pending["ai_job"] is None

        released = store.release_expired_human_leases(
            now=held["resume_at"] + timedelta(seconds=1)
        )
        assert conversation_id in {int(row["id"]) for row in released}
        jobs = [
            row
            for row in store.list_pending_ai_jobs(limit=100)
            if int(row["conversation_id"]) == conversation_id
        ]
        assert len(jobs) == 1
        assert int(jobs[0]["trigger_message_id"]) == int(pending["message"]["id"])
        assert int(jobs[0]["expected_version"]) == int(
            store.get_conversation(conversation_id)["state_version"]
        )
    finally:
        _cleanup(source_key)


def test_a_failed_answer_never_counts_as_an_answer_to_the_client():
    """Ответ, который Telegram отверг, клиент не видел — статус обязан остаться прежним."""
    suffix = uuid4().hex[:12]
    source_key = f"test-status-failed-{suffix}"
    chat_id = "700000002"
    try:
        store.ensure_source(source_key, source_type="test", display_name="status failed")
        _ingest(source_key, chat_id, author="client", text="Вопрос", message_id=f"{suffix}-1")
        answer = _ingest(source_key, chat_id, author="operator", text="Ответ",
                         message_id=f"{suffix}-2")
        conversation_id = int(answer["conversation"]["id"])
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE funnel_workspace_messages"
                    "   SET delivery_status = 'failed'"
                    " WHERE id = %s",
                    (int(answer["message"]["id"]),),
                )

        row = _row_of(source_key, conversation_id)
        assert row["has_answer"] is False
        assert row["awaiting_reply_since"] is not None
    finally:
        _cleanup(source_key)


def test_queue_order_is_urgent_then_client_waiting_then_us():
    """Очередь разбора владельца (27.07.2026): очень срочно → клиент ждёт ответа → ждём
    ответа от клиента; внутри группы первым тот, кто ждёт дольше."""
    suffix = uuid4().hex[:12]
    source_key = f"test-queue-order-{suffix}"
    try:
        store.ensure_source(source_key, source_type="test", display_name="queue order")
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        fresh = datetime.now(timezone.utc) - timedelta(seconds=30)

        # 1. Очень срочно: мы отвечали, клиент написал давно и всё ещё ждёт.
        _ingest(source_key, "700000101", author="client", text="Первый",
                message_id=f"{suffix}-a1", occurred_at=old - timedelta(minutes=5))
        _ingest(source_key, "700000101", author="operator", text="Ответ",
                message_id=f"{suffix}-a2", occurred_at=old - timedelta(minutes=4))
        urgent = _ingest(source_key, "700000101", author="client", text="Ну что там?",
                         message_id=f"{suffix}-a3", occurred_at=old)

        # 2. Клиент ждёт ответа, причём мы не отвечали ему ни разу: отдельного статуса
        #    «Новый клиент» больше нет (владелец, 27.07.2026) — новизна живёт этапом
        #    воронки. Ждёт он дольше следующего, поэтому идёт выше него.
        never_answered = _ingest(source_key, "700000102", author="client", text="Здравствуйте",
                                 message_id=f"{suffix}-b1",
                                 occurred_at=fresh - timedelta(seconds=5))

        # 3. Клиент ждёт ответа: мы уже отвечали, он написал только что.
        _ingest(source_key, "700000103", author="client", text="Привет",
                message_id=f"{suffix}-c1", occurred_at=fresh - timedelta(seconds=20))
        _ingest(source_key, "700000103", author="operator", text="Слушаю",
                message_id=f"{suffix}-c2", occurred_at=fresh - timedelta(seconds=10))
        client_waiting = _ingest(source_key, "700000103", author="client", text="Ещё вопрос",
                                 message_id=f"{suffix}-c3", occurred_at=fresh)

        # 4. Ждём ответа от клиента: последнее слово за нами.
        _ingest(source_key, "700000104", author="client", text="Спасибо",
                message_id=f"{suffix}-d1", occurred_at=old)
        waiting_client = _ingest(source_key, "700000104", author="operator", text="Ждём вас",
                                 message_id=f"{suffix}-d2", occurred_at=old)

        listing = store.list_conversations(source=source_key, limit=250)
        assert [int(row["id"]) for row in listing["items"]] == [
            int(urgent["conversation"]["id"]),
            int(never_answered["conversation"]["id"]),
            int(client_waiting["conversation"]["id"]),
            int(waiting_client["conversation"]["id"]),
        ]
    finally:
        _cleanup(source_key)


def test_full_takeover_survives_an_operator_reply_and_the_lease_sweeper():
    """Полный перехват: ИИ не возвращается ни по таймеру, ни после ответа оператора."""
    suffix = uuid4().hex[:12]
    source_key = f"test-takeover-{suffix}"
    chat_id = "700000201"
    try:
        store.ensure_source(source_key, source_type="test", display_name="takeover")
        first = _ingest(source_key, chat_id, author="client", text="Вопрос",
                        message_id=f"{suffix}-1")
        conversation_id = int(first["conversation"]["id"])

        held = store.transition_control(
            conversation_id,
            mode="human",
            expected_version=int(first["conversation"]["state_version"]),
            actor_name="Юлия",
            permanent=True,
        )
        assert held["control_mode"] == "human"
        assert held["resume_at"] is None
        assert store.is_permanent_hold(held) is True

        # Ответ оператора не превращает полный перехват в двухминутную аренду.
        replied = _ingest(source_key, chat_id, author="operator", text="Отвечаю сам",
                          message_id=f"{suffix}-2", author_name="Юлия")
        assert replied["conversation"]["control_mode"] == "human"
        assert replied["conversation"]["resume_at"] is None

        # Сторож аренды такой диалог не трогает даже сутки спустя.
        released = store.release_expired_human_leases(
            now=datetime.now(timezone.utc) + timedelta(days=1)
        )
        assert conversation_id not in {int(row["id"]) for row in released}

        current = store.get_conversation(conversation_id)
        assert current["control_mode"] == "human"
        assert current["resume_at"] is None
    finally:
        _cleanup(source_key)


def test_a_temporary_takeover_still_expires_back_to_ai():
    """Обычный перехват обязан истекать: иначе «взял на минуту» молча стало бы навсегда.

    Время сдвигаем чуть дальше аренды, а не на сутки: за сутки истекло бы ещё и окно
    ответа Telegram, и диалог ушёл бы в паузу совсем по другой причине.
    """
    suffix = uuid4().hex[:12]
    source_key = f"test-lease-{suffix}"
    chat_id = "700000202"
    try:
        store.ensure_source(source_key, source_type="test", display_name="lease")
        first = _ingest(source_key, chat_id, author="client", text="Вопрос",
                        message_id=f"{suffix}-1")
        conversation_id = int(first["conversation"]["id"])
        held = store.transition_control(
            conversation_id,
            mode="human",
            expected_version=int(first["conversation"]["state_version"]),
            actor_name="Юлия",
        )
        assert held["resume_at"] is not None
        assert store.is_permanent_hold(held) is False

        released = store.release_expired_human_leases(
            now=held["resume_at"] + timedelta(seconds=1)
        )
        assert conversation_id in {int(row["id"]) for row in released}
        current = store.get_conversation(conversation_id)
        assert current["control_mode"] == "ai"
        assert current["resume_at"] is None
    finally:
        _cleanup(source_key)
