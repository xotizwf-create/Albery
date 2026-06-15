#!/usr/bin/env python3
"""Weekly usage digest for the Bitrix24 Hermes-brain chat-bot.

Aggregates the last 7 days from bitrix_bot_interactions and delivers a short
summary to the owner's Telegram via `hermes send` (no LLM, no agent loop).
Scheduled weekly (see /etc/cron.d/albery-bitrix-bot-digest).
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

import psycopg

ENV_PATH = "/var/www/albery/.env"
TARGET = os.getenv("B24_DIGEST_TARGET", "telegram:Александр Никитенко")


def db_url() -> str:
    for line in open(ENV_PATH, encoding="utf-8"):
        if line.startswith("DATABASE_URL="):
            raw = line.split("=", 1)[1].strip()
            return re.sub(r"^postgresql\+psycopg2?://", "postgresql://", raw)
    raise SystemExit("DATABASE_URL not found in .env")


def build_digest() -> str:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    with psycopg.connect(db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*),
                       count(distinct bitrix_user_id),
                       count(*) FILTER (WHERE status = 'error'),
                       coalesce(round(avg(latency_ms)), 0),
                       count(*) FILTER (WHERE tier = 'full')
                FROM bitrix_bot_interactions WHERE created_at >= %s
                """,
                (since,),
            )
            total, users, errors, avg_ms, full_cnt = cur.fetchone()
            cur.execute(
                "SELECT question FROM bitrix_bot_interactions "
                "WHERE created_at >= %s AND status = 'ok' AND question <> '' "
                "ORDER BY id DESC LIMIT 10",
                (since,),
            )
            questions = [r[0] for r in cur.fetchall() if r[0]]

    if not total:
        return "📊 Гермес-бот в Битриксе: за неделю обращений не было."

    sample = "\n".join(f"• {q[:120]}" for q in questions)
    return (
        "📊 Гермес-бот в Битриксе — за 7 дней:\n"
        f"• обращений: {total}\n"
        f"• пользователей: {users}\n"
        f"• полный доступ (руководители): {full_cnt}\n"
        f"• ошибок: {errors}\n"
        f"• средняя задержка: {int(avg_ms)} мс\n\n"
        f"Последние вопросы:\n{sample}"
    )


def main() -> None:
    body = build_digest()
    subprocess.run(
        ["hermes", "send", "--to", TARGET, body],
        cwd="/root", env={**os.environ, "HOME": "/root"}, check=False,
    )


if __name__ == "__main__":
    main()
