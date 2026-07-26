#!/usr/bin/env python3
"""Перенести переписки старого прямого Telegram-пути в рабочее окно.

Зачем: обращения клиентов до перехода на рабочее окно лежат только в журнале
`telegram_bot_messages`. Оператор их там не видит, а значит не видит и половину истории
клиента, который завтра напишет снова.

Гарантии, которые здесь соблюдаются:

* Ни одной отправки. Скрипт пишет только в журнал переписки; очередь отправки не
  трогается вообще, поэтому клиент не получит ни одного сообщения из-за импорта.
* Повторный запуск безопасен: сообщение опознаётся по идентификатору Telegram (а если
  его нет — по идентификатору строки журнала), и вставка одного и того же сообщения
  дважды невозможна.
* Диалоги переносятся с тем же идентификатором подключения Telegram, что и живые: иначе
  следующее сообщение клиента создало бы ВТОРОЕ обращение, и история снова разъехалась
  бы.
* Ответы, которые в старом журнале помечены ошибкой, не переносятся: клиент их не
  получил, и показывать их как доставленные нельзя.
* Импортированные диалоги ставятся на паузу: старая переписка не должна вызвать
  автоматический ответ агента.

Запуск (на сервере, из /var/www/albery):

    .venv/bin/python scripts/import_legacy_telegram_history.py --dry-run
    .venv/bin/python scripts/import_legacy_telegram_history.py
    .venv/bin/python scripts/import_legacy_telegram_history.py --link-deals
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

import funnel_workspace_store as store  # noqa: E402
from shared.db import connect  # noqa: E402


# Клиентские разговоры через аккаунт компании. `bot_dm` — переписка людей с самим ботом
# (сотрудники, тесты), это не канал воронки.
DEFAULT_KINDS = ("lead_chat",)


def active_business_connection() -> str:
    """Идентификатор живого подключения Telegram Business.

    Личность обращения — это (источник, подключение, чат). Если перенести историю с
    пустым подключением, следующее живое сообщение того же клиента создаст отдельное
    обращение.
    """
    import tg_agent

    business = (tg_agent.load_state().get("business") or {})
    for connection_id, info in business.items():
        info = info or {}
        if info.get("enabled") is not False and info.get("can_reply") is not False:
            return str(connection_id)
    return ""


def legacy_rows(kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, created_at, bot, dialog_id, tg_user_id, username,
                       display_name, direction, kind, text, tg_message_id, status
                  FROM telegram_bot_messages
                 WHERE kind = ANY(%s)
                   AND tg_user_id IS NOT NULL
                   AND COALESCE(btrim(text), '') <> ''
                 ORDER BY created_at, id
                """,
                (list(kinds),),
            )
            return [dict(row) for row in cur.fetchall()]


def import_rows(
    rows: list[dict[str, Any]],
    *,
    connection_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    imported = 0
    skipped_failed = 0
    per_chat: dict[str, int] = defaultdict(int)
    conversations: dict[str, int] = {}

    for row in rows:
        outgoing = str(row.get("direction") or "").lower() == "out"
        if outgoing and str(row.get("status") or "") != "ok":
            # Ответ не ушёл клиенту — переносить его как доставленный нельзя.
            skipped_failed += 1
            continue
        chat_id = str(row["tg_user_id"])
        external_message_id = (
            str(row["tg_message_id"])
            if row.get("tg_message_id")
            else f"legacy-{row['id']}"
        )
        per_chat[chat_id] += 1
        if dry_run:
            imported += 1
            continue
        result = store.ingest_business_message(
            external_chat_id=chat_id,
            external_message_id=external_message_id,
            text=row.get("text") or "",
            author_type="client" if not outgoing else "agent",
            business_connection_id=connection_id,
            external_user_id=row.get("tg_user_id"),
            username=row.get("username"),
            display_name=row.get("display_name"),
            author_name=None if not outgoing else "ИИ-агент",
            occurred_at=row.get("created_at"),
            metadata={
                "imported_from": "telegram_bot_messages",
                "legacy_id": row["id"],
                "legacy_bot": row.get("bot"),
                "legacy_kind": row.get("kind"),
            },
            schedule_ai=False,
        )
        conversations[chat_id] = int(result["conversation"]["id"])
        imported += 1

    return {
        "imported": imported,
        "skipped_failed": skipped_failed,
        "chats": dict(per_chat),
        "conversations": conversations,
    }


def pause_imported(conversation_ids: list[int]) -> int:
    """Старая переписка не должна вызвать автоматический ответ агента."""
    paused = 0
    for conversation_id in conversation_ids:
        conversation = store.get_conversation(conversation_id)
        if str(conversation.get("control_mode")) == "paused":
            continue
        try:
            store.transition_control(
                conversation_id,
                mode="paused",
                expected_version=conversation["state_version"],
                actor_type="system",
                actor_name="Импорт истории",
                reason="Перенесённая переписка: автоматические ответы выключены.",
            )
            paused += 1
        except store.WorkspaceStoreError as exc:
            print(f"  диалог {conversation_id}: не удалось поставить на паузу — {exc}")
    return paused


def link_existing_deals(conversation_ids: list[int]) -> int:
    """Связать обращение с уже существующей сделкой. Новые сделки НЕ создаются."""
    import funnel_workspace_crm as crm
    import tg_agent

    linked = 0
    for conversation_id in conversation_ids:
        conversation = store.get_conversation(conversation_id)
        if conversation.get("deal_id"):
            continue
        try:
            deal_id = crm.find_existing_deal(
                conversation,
                crm_call=tg_agent.mcp_call,
                telegram_field=tg_agent.CRM_TELEGRAM_FIELD,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  диалог {conversation_id}: поиск сделки не удался — {exc}")
            continue
        if not deal_id:
            continue
        store.update_crm_link(conversation_id, deal_id=int(deal_id))
        linked += 1
    return linked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет перенесено")
    parser.add_argument("--link-deals", action="store_true", help="связать с существующими сделками (без создания новых)")
    parser.add_argument("--kinds", nargs="*", default=list(DEFAULT_KINDS), help="какие виды переписки переносить")
    args = parser.parse_args()

    kinds = tuple(args.kinds)
    rows = legacy_rows(kinds)
    connection_id = active_business_connection()
    print(f"строк в старом журнале ({', '.join(kinds)}): {len(rows)}")
    print(f"подключение Telegram: {'найдено' if connection_id else 'НЕ НАЙДЕНО — импорт остановлен'}")
    if not connection_id and not args.dry_run:
        print("Без живого подключения перенос создаст обращения, которые не сольются с новыми сообщениями.")
        return 1

    result = import_rows(rows, connection_id=connection_id, dry_run=args.dry_run)
    print(f"перенесено сообщений: {result['imported']}")
    if result["skipped_failed"]:
        print(f"пропущено недоставленных ответов: {result['skipped_failed']}")
    print(f"диалогов затронуто: {len(result['chats'])}")
    for chat_id, count in sorted(result["chats"].items(), key=lambda item: -item[1]):
        print(f"  чат {chat_id}: {count} сообщ.")

    if args.dry_run:
        print("Это был холостой прогон, в базу ничего не записано.")
        return 0

    conversation_ids = sorted(set(result["conversations"].values()))
    print(f"поставлено на паузу: {pause_imported(conversation_ids)}")
    if args.link_deals:
        print(f"связано с существующими сделками: {link_existing_deals(conversation_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
