from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from config import MSK_TZ
import funnel_telegram_gateway as gateway
import iu_bot_reminders
import iu_manager_response_watch as watch
from scripts import ensure_postgres


def _msk(hour: int, minute: int = 0, *, day: int = 30) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=MSK_TZ)


def test_manager_notification_window_has_exact_owner_boundaries():
    assert watch.manager_notifications_open(_msk(8, 59)) is False
    assert watch.manager_notifications_open(_msk(9, 0)) is True
    assert watch.manager_notifications_open(_msk(18, 0)) is True
    assert watch.manager_notifications_open(_msk(18, 1)) is False


def test_one_after_hours_period_spans_evening_and_next_morning():
    evening = watch.after_hours_period_key(42, _msk(20, 0, day=29))
    morning = watch.after_hours_period_key(42, _msk(8, 59, day=30))

    assert evening == morning == "iu-bot:after-hours:42:2026-07-30"


def test_due_kind_uses_10_30_60_and_morning_summary():
    anchor = _msk(10, 0)

    assert watch.due_kind(anchor, _msk(10, 9)) is None
    assert watch.due_kind(anchor, _msk(10, 10)) == "10m"
    assert watch.due_kind(anchor, _msk(10, 30)) == "30m"
    assert watch.due_kind(anchor, _msk(11, 0)) == "60m"
    assert watch.due_kind(_msk(17, 55, day=29), _msk(9, 0)) == "morning"
    assert watch.due_kind(_msk(8, 59), _msk(9, 0)) == "morning"


def test_individual_alert_has_client_wait_and_dialog_link():
    text = watch.format_individual_alert(
        {
            "kind": "30m",
            "conversation_id": 311,
            "client_name": "Александр",
        }
    )

    assert "Клиент Александр ждёт ответа уже 30 минут" in text
    assert "[URL=https://www.m4s.ru/agent-funnels/311]" in text


def test_morning_summary_is_oldest_first():
    now = _msk(9, 0)
    text = watch.format_morning_summary(
        [
            {
                "conversation_id": 2,
                "client_name": "Новый",
                "anchor_occurred_at": now - timedelta(hours=2),
            },
            {
                "conversation_id": 1,
                "client_name": "Старый",
                "anchor_occurred_at": now - timedelta(hours=14),
            },
        ],
        now=now,
    )

    assert "Сначала ответьте клиентам, которые ждут дольше всех" in text
    assert text.index("Клиент Старый") < text.index("Клиент Новый")


def test_existing_client_reminders_use_the_same_quiet_hours(monkeypatch):
    monkeypatch.setattr(iu_bot_reminders, "STALE_HOURS", 24)
    due = _msk(17, 50)

    assert (
        iu_bot_reminders.delivery_decision(_msk(18, 0), due).action == "send"
    )
    night = iu_bot_reminders.delivery_decision(_msk(18, 1), due)
    assert night.action == "wait"
    assert night.retry_at == _msk(9, 0, day=31)


def test_night_handover_acknowledges_client_without_alerting_manager(monkeypatch):
    queued = []
    handed_over = []
    monkeypatch.setattr(gateway, "_manager_notifications_open", lambda: False)
    monkeypatch.setattr(
        watch,
        "after_hours_period_key",
        lambda conversation_id: f"night:{conversation_id}:2026-07-31",
    )
    monkeypatch.setattr(
        gateway,
        "_reply_to_client",
        lambda conversation_id, text, **kwargs: queued.append(
            (conversation_id, text, kwargs)
        )
        or {"message": {"id": 1}},
    )
    monkeypatch.setattr(gateway, "_cancel_bot_reminders", lambda _conversation_id: None)
    monkeypatch.setattr(
        gateway,
        "_hand_over_to_human",
        lambda conversation_id, reason, **kwargs: handed_over.append(
            (conversation_id, reason, kwargs)
        ),
    )

    gateway._reply_and_hand_over(
        311,
        "Менеджер скоро подключится.",
        idempotency_key="ordinary-handoff",
        event="operator_called",
        reason="Клиент позвал менеджера.",
    )

    assert queued[0][1] == watch.AFTER_HOURS_CLIENT_REPLY
    assert queued[0][2]["idempotency_key"] == "night:311:2026-07-31"
    assert queued[0][2]["metadata"]["manager_notification_deferred"] is True
    assert "notify_manager_after_delivery" not in queued[0][2]["metadata"]
    assert handed_over[0][2]["manager_requested"] is True


def test_durable_alert_migration_is_registered_for_every_deploy():
    migration_name = "079_iu_manager_wait_alerts.sql"
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / migration_name
    )

    assert migration_name in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    sql = migration.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS iu_manager_wait_alerts" in sql
    assert "UNIQUE (conversation_id, anchor_message_id, kind)" in sql
