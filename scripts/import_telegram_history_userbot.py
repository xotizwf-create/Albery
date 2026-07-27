#!/usr/bin/env python3
"""Перенести НАСТОЯЩУЮ историю переписки из Telegram в рабочее окно.

Зачем отдельный скрипт. Бот в принципе не может прочитать прошлые сообщения: Bot API
отдаёт только новые обновления. Полную переписку видит лишь аккаунт-пользователь через
MTProto — та самая сессия менеджера, которая уже используется как «глаза» агента
(`tg_userbot.py`). Импорт из служебного журнала (`import_legacy_telegram_history.py`)
переносит только то, что успел записать старый бот, и потому неполон.

Что нужно один раз настроить владельцу (без этого запуск невозможен):

  1. TG_API_ID и TG_API_HASH в .env — берутся на my.telegram.org.
  2. Вход в сессию (Telegram пришлёт код в приложение владельца):
         .venv/bin/python scripts/tg_userbot_login.py request +7XXXXXXXXXX
         .venv/bin/python scripts/tg_userbot_login.py confirm <код>

Запуск:

    .venv/bin/python scripts/import_telegram_history_userbot.py --dry-run
    .venv/bin/python scripts/import_telegram_history_userbot.py --limit 200

Гарантии те же, что и у импорта из журнала: пишем только в переписку, очередь отправки не
трогаем, повторный запуск не создаёт дублей (идентификатор сообщения берётся из Telegram),
диалоги переносятся с идентификатором живого подключения.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

import funnel_workspace_store as store  # noqa: E402
import tg_userbot  # noqa: E402
from scripts.import_legacy_telegram_history import (  # noqa: E402
    active_business_connection,
    pause_imported,
)


def known_client_ids() -> dict[int, int]:
    """Telegram ID → id обращения. Переносим историю тех, кто уже есть в окне."""
    mapping: dict[int, int] = {}
    for row in store.list_conversations(limit=250)["items"]:
        external_id = row.get("external_user_id")
        if external_id:
            mapping[int(external_id)] = int(row["id"])
    return mapping


async def collect(limit: int, only: set[int] | None) -> dict[int, list[dict]]:
    """Прочитать переписку личных диалогов аккаунта менеджера."""
    client = tg_userbot._client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise SystemExit(
                "Сессия Telegram не авторизована. Нужен вход: "
                "scripts/tg_userbot_login.py request/confirm."
            )
        me = await client.get_me()
        collected: dict[int, list[dict]] = {}
        async for dialog in client.iter_dialogs():
            if not dialog.is_user:
                continue
            peer_id = int(dialog.entity.id)
            if only is not None and peer_id not in only:
                continue
            if peer_id == int(me.id):
                continue
            messages: list[dict] = []
            async for message in client.iter_messages(dialog.entity, limit=limit):
                text = (message.message or "").strip()
                if not text:
                    continue
                messages.append(
                    {
                        "message_id": int(message.id),
                        "outgoing": bool(message.out),
                        "text": text,
                        "date": message.date,
                    }
                )
            if messages:
                messages.reverse()  # от старых к новым
                collected[peer_id] = messages
                collected.setdefault("__names__", {})  # type: ignore[index]
                collected["__names__"][peer_id] = {  # type: ignore[index]
                    "username": getattr(dialog.entity, "username", None),
                    "display_name": (dialog.name or "").strip(),
                }
        return collected
    finally:
        await client.disconnect()


def store_messages(
    collected: dict,
    *,
    connection_id: str,
    dry_run: bool,
) -> dict[str, int]:
    names = collected.pop("__names__", {})
    imported = 0
    conversation_ids: set[int] = set()
    for peer_id, messages in collected.items():
        info = names.get(peer_id, {})
        for message in messages:
            if dry_run:
                imported += 1
                continue
            result = store.ingest_business_message(
                external_chat_id=str(peer_id),
                external_message_id=f"tg-{message['message_id']}",
                text=message["text"],
                # Исходящее в личке аккаунта компании — это ответ человека или агента от
                # имени компании; помечаем оператором, чтобы подпись не врала.
                author_type="operator" if message["outgoing"] else "client",
                business_connection_id=connection_id,
                external_user_id=peer_id,
                username=info.get("username"),
                display_name=info.get("display_name"),
                occurred_at=message["date"],
                metadata={"imported_from": "telegram_userbot"},
                schedule_ai=False,
                operator_lease_seconds=10,
            )
            conversation_ids.add(int(result["conversation"]["id"]))
            imported += 1
    return {"imported": imported, "conversations": sorted(conversation_ids)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=200, help="сколько последних сообщений на диалог")
    parser.add_argument("--all-dialogs", action="store_true", help="не только те, кто уже есть в окне")
    args = parser.parse_args()

    if not tg_userbot.session_ready():
        print("Сессии Telegram нет: .tg_userbot.session не найден.")
        print("Нужно один раз: TG_API_ID/TG_API_HASH в .env и вход через")
        print("  scripts/tg_userbot_login.py request +7XXXXXXXXXX")
        print("  scripts/tg_userbot_login.py confirm <код из Telegram>")
        return 1

    connection_id = active_business_connection()
    if not connection_id and not args.dry_run:
        print("Живое подключение Telegram Business не найдено — импорт остановлен.")
        return 1

    known = known_client_ids()
    only = None if args.all_dialogs else set(known)
    collected = asyncio.run(collect(args.limit, only))
    result = store_messages(collected, connection_id=connection_id, dry_run=args.dry_run)
    print(f"перенесено сообщений: {result['imported']}")
    print(f"диалогов затронуто: {len(result['conversations'])}")
    if args.dry_run:
        print("Холостой прогон: в базу ничего не записано.")
        return 0
    print(f"поставлено на паузу: {pause_imported(result['conversations'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
