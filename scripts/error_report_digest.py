#!/usr/bin/env python3
"""Digest of employee error reports from the Bitrix bot, each linked to the dialogue that preceded
it, delivered to the Bitrix «Уведомления» group (chat728). Optionally enriches each report with a
short Mozg-generated likely-cause / how-to-fix note. Dedup via a watermark (max digested report id)
so it can run weekly OR on demand.

Usage (on the box, from /var/www/albery):
    .venv/bin/python scripts/error_report_digest.py            # new reports since last digest
    .venv/bin/python scripts/error_report_digest.py --all      # ignore watermark (within window)
    .venv/bin/python scripts/error_report_digest.py --dry-run  # print, do not send / advance
    .venv/bin/python scripts/error_report_digest.py --no-llm   # skip the Mozg cause/fix analysis

Config (env / .env): ALBERY_BITRIX_NOTIFY_CHAT (default chat728, see scripts/b24_chat_notify.py),
ALBERY_ERROR_DIGEST_DAYS (7), ALBERY_ERROR_DIGEST_LLM_TIMEOUT (90),
ALBERY_ERROR_DIGEST_STATE (state file path)."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
MSK = ZoneInfo("Europe/Moscow")
STATE_PATH = Path(os.getenv("ALBERY_ERROR_DIGEST_STATE", "/var/www/albery/.error_digest_state.json"))


def db_dsn() -> str:
    load_dotenv(ROOT / ".env")
    dsn = os.getenv("DATABASE_URL", "").strip()
    return re.sub(r"^postgresql\+psycopg2?://", "postgresql://", dsn)


def bitrix_send(text: str) -> tuple[bool, str | None]:
    """Mirror the digest into the Bitrix24 notifications chat AS THE BOT (imbot.message.add with an
    auto-refreshed app token — see scripts/b24_chat_notify.py). Best-effort; returns (ok, error)."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import b24_chat_notify
        return b24_chat_notify.notify(text)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:300]


def load_watermark() -> int:
    try:
        return int(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("last_report_id", 0))
    except Exception:  # noqa: BLE001
        return 0


def save_watermark(report_id: int) -> None:
    try:
        STATE_PATH.write_text(json.dumps({"last_report_id": int(report_id)}), encoding="utf-8")
    except OSError:
        pass


def mozg_fix_hint(report_text: str, context_turns: list[dict]) -> str:
    """Best-effort short cause/fix note from the local Hermes brain (empty string on any failure)."""
    convo = "\n".join(
        f"- Сотрудник: {(t.get('question') or '').strip()}\n  Агент: {(t.get('answer') or '').strip()}"
        for t in (context_turns or [])[-6:]
    )
    prompt = (
        "Сотрудник пожаловался на ИИ-агента компании. Жалоба:\n" + report_text +
        "\n\nФрагмент диалога перед жалобой:\n" + (convo or "(контекста нет)") +
        "\n\nКратко, по-русски, 2-3 пункта: вероятная причина и как пофиксить "
        "(инструкция для ИИ / промпт / код / данные). Без воды."
    )
    try:
        sys.path.insert(0, str(ROOT))
        from quality_llm import run_quality_text
        return run_quality_text(
            prompt,
            purpose="error_digest",
            timeout_s=int(os.getenv("ALBERY_ERROR_DIGEST_LLM_TIMEOUT", "90")),
            retries=1,
        )
    except Exception:  # noqa: BLE001
        return ""


def fmt_dt(value) -> str:
    try:
        return value.astimezone(MSK).strftime("%d.%m %H:%M")
    except Exception:  # noqa: BLE001
        return str(value)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=int(os.getenv("ALBERY_ERROR_DIGEST_DAYS", "7")))
    ap.add_argument("--all", action="store_true", help="ignore watermark (still within --days window)")
    ap.add_argument("--no-llm", action="store_true", help="skip Mozg cause/fix analysis")
    ap.add_argument("--force", action="store_true", help="send even if there are no new reports")
    ap.add_argument("--dry-run", action="store_true", help="print, do not send or advance watermark")
    args = ap.parse_args(argv)

    watermark = 0 if args.all else load_watermark()
    with psycopg.connect(db_dsn()) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT report_id, reported_at, dialog_id, bitrix_user_id, reporter_name,
                   report_text, delivered, delivery_error, context_turns
            FROM error_report_context
            WHERE reported_at >= now() - (%s || ' days')::interval
              AND report_id > %s
            ORDER BY report_id
            """,
            (args.days, watermark),
        )
        rows = cur.fetchall()

    if not rows and not args.force:
        print("no new error reports")
        return 0

    lines = [f"🗂 Дайджест жалоб на ИИ-агента (за {args.days} дн.): {len(rows)} шт."]
    max_id = watermark
    for r in rows:
        max_id = max(max_id, r["report_id"])
        ctx = r["context_turns"] or []
        lines.append("")
        header = (f"— #{r['report_id']} от {r['reporter_name'] or 'Сотрудник'} "
                  f"({fmt_dt(r['reported_at'])}), диалог {r['dialog_id']}")
        if not r["delivered"]:
            header += "  ⚠️ не доставлено в TG"
        lines.append(header)
        lines.append(f"  Жалоба: {r['report_text']}")
        if ctx:
            lines.append("  Контекст диалога перед жалобой:")
            for t in ctx[-5:]:
                q = (t.get("question") or "").strip().replace("\n", " ")[:160]
                a = (t.get("answer") or "").strip().replace("\n", " ")[:160]
                flag = "" if t.get("status") == "ok" else f" [{t.get('status')}]"
                lines.append(f"   • {q} → {a}{flag}")
        else:
            lines.append("  Контекст диалога: нет записей до жалобы.")
        if not args.no_llm:
            hint = mozg_fix_hint(r["report_text"], ctx)
            if hint:
                lines.append("  🔧 Разбор (Мозг): " + hint)

    text = "\n".join(lines)
    if args.dry_run:
        print(text)
        return 0
    # Уведомления перенесены в Bitrix-группу «Уведомления» (chat728) — в личный Telegram
    # владельца дайджест больше не отправляем.
    ok, err = bitrix_send(text)
    if ok and rows:
        save_watermark(max_id)
    print(f"bitrix_sent={ok} err={err} reports={len(rows)} watermark->{max_id if ok else watermark}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
