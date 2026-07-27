#!/usr/bin/env python3
"""Перенести в рабочее окно переписку, присланную вручную.

Зачем. Бот не может прочитать прошлые сообщения Telegram: Bot API отдаёт только новые
обновления. Пока MTProto-сессия менеджера не настроена, единственный источник полной
переписки — сам владелец. Этот скрипт принимает её текстом и раскладывает в диалог.

Формат файла — по строке на сообщение, автор перед двоеточием:

    # можно указать собеседника заголовком (иначе передайте параметрами)
    telegram_id: 212850563
    username: yulia1344
    name: Юлия

    клиент: Здравствуйте, интересуют условия
    мы: Добрый день! Отправляю условия.
    клиент: Спасибо, изучу
    [24.07.2026 14:05] клиент: А по срокам что?

Автором может быть «клиент» (он же «client», «к») или «мы» («оператор», «агент», «we»).
Дата в квадратных скобках необязательна: без неё сообщения расставляются по порядку с
шагом в минуту от указанного начала (--start) или от времени первого сообщения диалога.

Запуск:

    .venv/bin/python scripts/import_pasted_conversation.py разговор.txt --dry-run
    .venv/bin/python scripts/import_pasted_conversation.py разговор.txt
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

import funnel_workspace_store as store  # noqa: E402
from scripts.import_legacy_telegram_history import active_business_connection  # noqa: E402


CLIENT_AUTHORS = {"клиент", "client", "к", "он", "она"}
US_AUTHORS = {"мы", "оператор", "operator", "агент", "agent", "we", "я"}

HEADER_RE = re.compile(r"^\s*(telegram_id|username|name|имя)\s*:\s*(.+?)\s*$", re.I)
LINE_RE = re.compile(
    r"^\s*(?:\[(?P<stamp>[^\]]+)\]\s*)?(?P<author>[^:]{1,40}?)\s*:\s*(?P<text>.+?)\s*$"
)
STAMP_FORMATS = (
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m %H:%M",
    "%H:%M",
)


def parse_stamp(raw: str, fallback_year: int) -> datetime | None:
    value = raw.strip()
    for fmt in STAMP_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            parsed = parsed.replace(year=fallback_year)
        return parsed.replace(tzinfo=timezone.utc)
    return None


def parse_file(path: Path) -> tuple[dict[str, str], list[dict]]:
    header: dict[str, str] = {}
    messages: list[dict] = []
    year = datetime.now(timezone.utc).year
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        head = HEADER_RE.match(line)
        if head and head.group(1).lower() in {"telegram_id", "username", "name", "имя"}:
            key = head.group(1).lower()
            header["name" if key == "имя" else key] = head.group(2).strip()
            continue
        match = LINE_RE.match(line)
        if not match:
            # Продолжение предыдущего сообщения: в переписке абзацы — обычное дело.
            if messages:
                messages[-1]["text"] += "\n" + line
            continue
        author = match.group("author").strip().lower()
        if author in CLIENT_AUTHORS:
            author_type = "client"
        elif author in US_AUTHORS:
            author_type = "operator"
        else:
            if messages:
                messages[-1]["text"] += "\n" + line
            continue
        messages.append(
            {
                "author_type": author_type,
                "text": match.group("text").strip(),
                "occurred_at": parse_stamp(match.group("stamp") or "", year),
            }
        )
    return header, messages


def fill_times(messages: list[dict], start: datetime) -> None:
    """Проставить время там, где его не указали: порядок важнее точности."""
    current = start
    for message in messages:
        if message["occurred_at"] is None:
            message["occurred_at"] = current
        current = message["occurred_at"] + timedelta(minutes=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="файл с перепиской")
    parser.add_argument("--telegram-id", type=int, help="numeric Telegram ID собеседника")
    parser.add_argument("--username", help="@username собеседника")
    parser.add_argument("--name", help="как показывать собеседника")
    parser.add_argument("--start", help="время первого сообщения, ДД.ММ.ГГГГ ЧЧ:ММ")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    header, messages = parse_file(args.file)
    telegram_id = args.telegram_id or (
        int(header["telegram_id"]) if header.get("telegram_id", "").isdigit() else None
    )
    if not telegram_id:
        print("Не указан numeric Telegram ID собеседника: он нужен, чтобы переписка легла")
        print("в тот же диалог, куда придут новые сообщения (--telegram-id или строка")
        print("«telegram_id: ...» в файле).")
        return 1
    if not messages:
        print("В файле не нашлось ни одного сообщения. Формат: «клиент: текст» / «мы: текст».")
        return 1

    start = None
    if args.start:
        start = parse_stamp(args.start, datetime.now(timezone.utc).year)
    fill_times(messages, start or datetime.now(timezone.utc) - timedelta(days=1))

    username = args.username or header.get("username")
    display_name = args.name or header.get("name")
    connection_id = active_business_connection()

    print(f"собеседник: {display_name or username or telegram_id}")
    print(f"сообщений в файле: {len(messages)}")
    print(f"  от клиента: {sum(1 for m in messages if m['author_type'] == 'client')}")
    print(f"  наших: {sum(1 for m in messages if m['author_type'] == 'operator')}")
    if args.dry_run:
        for message in messages[:5]:
            who = "Клиент" if message["author_type"] == "client" else "Мы"
            stamp = message["occurred_at"].strftime("%d.%m %H:%M")
            print(f"  [{stamp}] {who}: {message['text'][:60]}")
        print("Холостой прогон: в базу ничего не записано.")
        return 0

    conversation_id = None
    for index, message in enumerate(messages, start=1):
        result = store.ingest_business_message(
            external_chat_id=str(telegram_id),
            # Устойчивый идентификатор: повторный запуск того же файла не задвоит переписку.
            external_message_id=f"pasted-{telegram_id}-{index}",
            text=message["text"],
            author_type=message["author_type"],
            business_connection_id=connection_id,
            external_user_id=telegram_id,
            username=username,
            display_name=display_name,
            occurred_at=message["occurred_at"],
            metadata={"imported_from": "pasted_transcript", "source_file": args.file.name},
            schedule_ai=False,
            operator_lease_seconds=10,
        )
        conversation_id = int(result["conversation"]["id"])
    print(f"перенесено в обращение #{conversation_id}")
    print(f"ссылка: {__import__('funnel_workspace').conversation_url(conversation_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
