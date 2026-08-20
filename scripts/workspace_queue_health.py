#!/usr/bin/env python3
"""Content-free health probe for the durable Telegram/IU work queues."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable


def _connect():
    from shared.db import connect

    return connect()


def inspect_workspace_queue_health(
    *,
    connect_factory: Callable[[], Any] | None = None,
    now: datetime | None = None,
    overdue_after: timedelta = timedelta(minutes=10),
) -> list[str]:
    """Return operational queue problems without reading messages or payloads."""

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = moment.astimezone(timezone.utc) - overdue_after
    connector = connect_factory or _connect
    with connector() as connection:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT count(*) FROM funnel_workspace_updates
                      WHERE processing_status IN ('pending', 'retry')
                        AND available_at <= %s) AS update_overdue,
                    (SELECT count(*) FROM funnel_workspace_updates
                      WHERE processing_status = 'processing'
                        AND locked_until <= %s) AS update_expired,
                    (SELECT count(*) FROM funnel_workspace_ai_jobs
                      WHERE processing_status = 'pending'
                        AND available_at <= %s) AS ai_overdue,
                    (SELECT count(*) FROM funnel_workspace_ai_jobs
                      WHERE processing_status = 'leased'
                        AND locked_until <= %s) AS ai_expired,
                    (SELECT count(*) FROM funnel_workspace_outbox
                      WHERE delivery_status = 'unknown') AS outbox_unknown,
                    (SELECT count(*) FROM funnel_workspace_outbox
                      WHERE delivery_status = 'pending'
                        AND available_at <= %s) AS outbox_overdue,
                    (SELECT count(*) FROM funnel_workspace_outbox
                      WHERE delivery_status IN ('leased', 'sending')
                        AND locked_until <= %s) AS outbox_expired,
                    (SELECT count(*) FROM funnel_workspace_crm_actions
                      WHERE processing_status IN ('pending', 'retry')
                        AND available_at <= %s) AS crm_overdue,
                    (SELECT count(*) FROM funnel_workspace_crm_actions
                      WHERE processing_status = 'leased'
                        AND locked_until <= %s) AS crm_expired,
                    (SELECT count(*) FROM iu_bot_reminders
                      WHERE status = 'pending' AND due_at <= %s) AS reminder_overdue,
                    (SELECT count(*) FROM iu_bot_reminders
                      WHERE status = 'leased' AND locked_until <= %s) AS reminder_expired,
                    (SELECT count(*) FROM iu_manager_wait_alerts
                      WHERE status = 'unknown') AS manager_unknown,
                    (SELECT count(*) FROM iu_manager_wait_alerts
                      WHERE status = 'pending' AND due_at <= %s) AS manager_overdue,
                    (SELECT count(*) FROM iu_manager_wait_alerts
                      WHERE status IN ('leased', 'sending')
                        AND locked_until <= %s) AS manager_expired
                """,
                (
                    cutoff, moment, cutoff, moment, cutoff, moment,
                    cutoff, moment, cutoff, moment, cutoff, moment,
                ),
            )
            row = dict(cur.fetchone() or {})

    labels = {
        "update_overdue": "Telegram updates overdue",
        "update_expired": "Telegram update leases expired",
        "ai_overdue": "Telegram AI jobs overdue",
        "ai_expired": "Telegram AI leases expired",
        "outbox_unknown": "Telegram deliveries have ambiguous outcome",
        "outbox_overdue": "Telegram deliveries overdue",
        "outbox_expired": "Telegram delivery leases expired",
        "crm_overdue": "Telegram CRM actions overdue",
        "crm_expired": "Telegram CRM action leases expired",
        "reminder_overdue": "IU client reminders overdue",
        "reminder_expired": "IU client reminder leases expired",
        "manager_unknown": "IU manager alerts have ambiguous Bitrix outcome",
        "manager_overdue": "IU manager alerts overdue",
        "manager_expired": "IU manager alert leases expired",
    }
    return [
        f"{labels[key]}: {int(row.get(key) or 0)}"
        for key in labels
        if int(row.get(key) or 0)
    ]


# Терминальные состояния очередей: из них строка уже никогда не уйдёт сама. Считать их
# «текущей проблемой», как остальные счётчики, нельзя — счётчик не может обнулиться, и один
# разобранный инцидент тревожит вечно. Ровно это и случилось 19.08.2026: 75 CRM-задач Авито
# упали в dead_letter за один вечер и потом каждые 6 часов приходили в «Уведомления» как
# КРИТИЧНО. Поэтому здесь берётся не счётчик, а ГРАНИЦА: наибольший id, и алерт поднимают
# только строки новее уже показанной границы.
TERMINAL_QUEUES: dict[str, tuple[str, str, str]] = {
    "update_dead": ("funnel_workspace_updates", "processing_status", "dead_letter"),
    "ai_failed": ("funnel_workspace_ai_jobs", "processing_status", "failed"),
    "outbox_failed": ("funnel_workspace_outbox", "delivery_status", "failed"),
    "crm_dead": ("funnel_workspace_crm_actions", "processing_status", "dead_letter"),
}

TERMINAL_LABELS = {
    "update_dead": "обновления Telegram, упавшие насмерть",
    "ai_failed": "задания ИИ, упавшие насмерть",
    "outbox_failed": "неотправленные сообщения",
    "crm_dead": "CRM-задачи, упавшие насмерть",
}


def inspect_terminal_queues(
    *,
    connect_factory: Callable[[], Any] | None = None,
) -> dict[str, int]:
    """Наибольший id в каждом терминальном состоянии (0 — пусто). Без содержимого строк."""

    connector = connect_factory or _connect
    parts = [
        f"(SELECT coalesce(max(id), 0) FROM {table} WHERE {column} = '{value}') AS {metric}"
        for metric, (table, column, value) in TERMINAL_QUEUES.items()
    ]
    with connector() as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT " + ", ".join(parts))
            row = dict(cur.fetchone() or {})
    return {metric: int(row.get(metric) or 0) for metric in TERMINAL_QUEUES}


def unreported_terminal_queues(
    marks: dict[str, int], acknowledged: dict[str, Any] | None = None,
) -> list[str]:
    """Что появилось после последнего показанного алерта — по одной строке на очередь."""

    seen = acknowledged or {}
    out = []
    for metric, mark in marks.items():
        try:
            was = int(seen.get(metric) or 0)
        except (TypeError, ValueError):
            was = 0
        if mark > was:
            out.append(f"{TERMINAL_LABELS[metric]} — появились новые, разобрать")
    return out
