#!/usr/bin/env python3
"""Ночной обзор качества переписки агента воронки ИУ.

Владелец 25.07.2026: «это ИИ продажник и консультант, нельзя чтобы он клевал в грязь лицом».
До сих пор качество замечал сам владелец, читая диалоги. Теперь это делает система каждую ночь.

Два уровня, сознательно разделённые:
1. МЕХАНИЧЕСКИЕ проверки (quality_checks) — приветствие в первом сообщении, невыполнимые
   обещания, утечки служебных маркеров, правило одного вопроса, вопрос клиента без ответа.
   Дёшево, быстро, без предвзятости модели. Большая часть провалов недели ловится здесь.
2. СУДЬЯ на модели по рубрике (docs/quality-rubric.md) — то, что механикой не поймать: тон,
   человечность, отсутствие давления. Оценки 1-5 по каждому измерению плюс короткая причина.

Тихий по умолчанию: пишет владельцу, только если есть нарушения или оценка ниже порога.
Ставится systemd-таймером (albery-quality.timer), раз в сутки.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import quality_checks as qc          # noqa: E402
from shared.db import connect        # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("quality-review")

HOURS = int(os.getenv("QUALITY_REVIEW_HOURS", "24") or 24)
MIN_SCORE = float(os.getenv("QUALITY_MIN_SCORE", "4") or 4)      # ниже — зовём владельца
JUDGE_LIMIT = int(os.getenv("QUALITY_JUDGE_LIMIT", "12") or 12)  # сколько сообщений судить

RUBRIC = """Ты — руководитель отдела продаж. Оцени сообщения менеджера клиенту.

По каждому сообщению поставь оценки 1-5:
- «человечность»: звучит как живой человек, а не автоответчик; уместное обращение, без канцелярита;
- «полезность»: отвечает на то, что спросил клиент, и понятно называет следующий шаг;
- «без давления»: не давит, не торопит, не обещает того, чего не может.

Верни СТРОГО JSON-массив, по объекту на сообщение, в том же порядке:
[{"человечность": 5, "полезность": 4, "без давления": 5, "замечание": "коротко, что улучшить"}]

СООБЩЕНИЯ:
"""


def _dialogs(hours: int) -> dict[str, list[dict]]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dialog_id, direction, text, meta, created_at"
                " FROM telegram_bot_messages WHERE kind = 'lead_chat' AND created_at > %s"
                " ORDER BY id", (since,))
            rows = list(cur.fetchall())
    out: dict[str, list[dict]] = {}
    for r in rows:
        meta = r["meta"] or {}
        if meta.get("escalated") and r["direction"] == "out" and not (r["text"] or "").strip():
            continue
        out.setdefault(str(r["dialog_id"]), []).append({
            "direction": r["direction"], "text": r["text"] or "",
            # Дословный документ владельца судить по длине и числу вопросов нельзя.
            "verbatim": bool(meta.get("terms") or meta.get("anketa")),
            "at": str(r["created_at"])[:19],
        })
    return out


def _judge(messages: list[str]) -> list[dict]:
    """Оценки модели по рубрике. Пустой список, если судья недоступен."""
    if not messages:
        return []
    try:
        sys.path.insert(0, str(ROOT))
        import tg_agent as tg
        tg._load_env_file()
        numbered = "\n\n".join(f"{i + 1}. {m}" for i, m in enumerate(messages))
        raw = tg.hermes_answer(RUBRIC + numbered, "quality-review")
        text = raw[raw.find("["):raw.rfind("]") + 1]
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 — обзор не имеет права падать из-за судьи
        log.warning("судья недоступен", exc_info=True)
        return []


def main() -> int:
    dialogs = _dialogs(HOURS)
    problems: list[str] = []
    outgoing: list[str] = []
    for dialog_id, messages in dialogs.items():
        issues = qc.check_dialog(messages)
        if issues:
            problems.append(f"диалог {dialog_id}: " + qc.summary(issues))
            for issue in issues[:3]:
                problems.append(f"    — {issue.kind}: {issue.detail}")
        outgoing += [m["text"] for m in messages
                     if m["direction"] == "out" and not m["verbatim"] and m["text"].strip()]

    scores = _judge(outgoing[-JUDGE_LIMIT:])
    low: list[str] = []
    averages: dict[str, float] = {}
    if scores:
        for key in ("человечность", "полезность", "без давления"):
            values = [float(s.get(key) or 0) for s in scores if s.get(key)]
            if values:
                averages[key] = round(sum(values) / len(values), 2)
        for i, s in enumerate(scores):
            worst = min((float(s.get(k) or 5) for k in
                         ("человечность", "полезность", "без давления")), default=5)
            if worst < MIN_SCORE:
                low.append(f"    — «{outgoing[-JUDGE_LIMIT:][i][:70]}»: {s.get('замечание') or ''}")

    report = [f"Обзор качества за {HOURS} ч: диалогов {len(dialogs)}, "
              f"сообщений агента {len(outgoing)}"]
    if averages:
        report.append("Оценки судьи: " + ", ".join(f"{k} {v}" for k, v in averages.items()))
    if problems:
        report.append("\nНарушения правил:")
        report += problems
    if low:
        report.append("\nСлабые по рубрике:")
        report += low
    text = "\n".join(report)
    print(text)

    if not problems and not low:
        log.info("качество в норме — владельца не беспокоим")
        return 0
    chat_id = (os.getenv("TG_ESCALATION_CHAT_ID") or "").strip()
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if chat_id and token:
        import requests
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text[:3900]}, timeout=20)
            log.info("отчёт отправлен владельцу")
        except Exception:  # noqa: BLE001
            log.warning("отчёт не доставлен", exc_info=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
