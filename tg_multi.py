"""Telegram-агенты, заведённые владельцем в кабинете.

Основной бот (@Albery_AI2_Bot) обслуживается tg_agent.py и здесь НЕ трогается: он несёт
бизнес-режим, лидов и воронку, и ломать его ради второго бота нельзя. Этот модуль поднимает
по отдельному потоку опроса на каждого агента из таблицы telegram_agents — каждый со своим
токеном, своим списком доступа и своей веткой журнала.

Работает внутри службы albery-tg (запускается из tg_agent.poll_forever), поэтому здесь те же
ограничения: никаких импортов app/b24bot — их импорт стартует живые планировщики.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import requests

import tg_agent as core

log = logging.getLogger("tg_multi")

_POLL_TIMEOUT = 50
_RELOAD_S = float(os.getenv("TG_MULTI_RELOAD_S", "60") or 60)
_threads: dict[str, threading.Thread] = {}
_offsets: dict[str, int] = {}


def load_agents() -> list[dict]:
    """Активные агенты с телеграмным мостом. Пустой список — база недоступна или агентов нет.

    Агенты живут в общей таблице `agents` — той же, что и субагенты Битрикса. Отличается только
    мост: там bitrix_bot_id, здесь telegram_bot_token. Благодаря этому у телеграмного агента
    есть всё то же самое: свой коннектор agent-<slug> с набором MCP-инструментов, подключённые
    инструкции, база знаний и личные инструкции."""
    try:
        with core._db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT slug, name, telegram_username AS username,"
                            " telegram_bot_token AS bot_token, role_prompt,"
                            " telegram_bot_user_id AS bot_user_id"
                            " FROM agents WHERE is_active AND telegram_bot_token IS NOT NULL"
                            " ORDER BY created_at")
                return [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        log.warning("не удалось прочитать список Telegram-агентов", exc_info=True)
        return []


def api(token: str, method: str, http_timeout: int = 35, **params):
    resp = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=params,
                         timeout=http_timeout)
    data = resp.json() if resp.content else {}
    if not (isinstance(data, dict) and data.get("ok")):
        raise RuntimeError(f"{method}: {str(data)[:200]}")
    return data.get("result")


def describe(token: str) -> dict:
    """Кто этот бот в Telegram. Используется и при создании агента — проверить токен."""
    me = api(token, "getMe", http_timeout=15) or {}
    return {"name": str(me.get("first_name") or "").strip(),
            "username": str(me.get("username") or "").strip(),
            "bot_user_id": me.get("id")}


def _answer(agent: dict, chat_id, sender: dict, text: str) -> None:
    """Ход агента: доступ → мозг → ответ → журнал. Ошибки не роняют поток опроса."""
    slug = agent["slug"]
    allowed = core.access_usernames(slug)
    uname = str(sender.get("username") or "").lower()
    core.journal(slug, chat_id, "in", text, kind="bot_dm", user=sender)
    if allowed and uname not in allowed:
        refusal = ("Это внутренний агент компании Albery. Если вам нужен доступ — "
                   "напишите Евгению.")
        try:
            api(agent["bot_token"], "sendMessage", chat_id=chat_id, text=refusal)
        except Exception:  # noqa: BLE001
            log.warning("отказ не доставлен", exc_info=True)
        core.journal(slug, chat_id, "out", refusal, kind="bot_dm", user=sender,
                     meta={"denied": True})
        return
    core.remember_access_user_id(slug, sender)
    prompt = ((agent.get("role_prompt") or "").strip()
              or "Ты — ИИ-агент компании Albery в Telegram. Отвечай по-русски, кратко и по делу, "
                 "обычным текстом без разметки.")
    # Личный коннектор агента — то же, на чём работают субагенты в Битриксе. Через него агент
    # получает ИМЕННО свой набор MCP-инструментов, подключённые инструкции и знания; без него
    # телеграмный агент был бы говорящей головой без инструментов.
    toolsets = f"agent-{slug},{os.getenv('TG_MULTI_EXTRA_TOOLSETS', 'web').strip()}".rstrip(",")
    try:
        answer = core.hermes_answer(f"{prompt}\n\nСообщение собеседника:\n{text}",
                                    f"tg-{slug}-{chat_id}", toolsets=toolsets)
    except Exception as exc:  # noqa: BLE001
        log.warning("мозг не ответил (%s): %s", slug, str(exc)[:200])
        core.journal(slug, chat_id, "out", f"мозг не ответил: {str(exc)[:200]}", kind="bot_dm",
                     user=sender, status="error")
        return
    answer = core._strip_markup((answer or "").strip())
    if not answer:
        return
    ok = True
    try:
        api(agent["bot_token"], "sendMessage", chat_id=chat_id, text=answer[:4000])
    except Exception as exc:  # noqa: BLE001
        ok = False
        log.warning("ответ не доставлен (%s): %s", slug, str(exc)[:200])
    core.journal(slug, chat_id, "out", answer, kind="bot_dm", user=sender,
                 status="ok" if ok else "error")


def _poll(agent: dict) -> None:
    slug = agent["slug"]
    token = agent["bot_token"]
    log.info("Telegram-агент «%s» (@%s) начал работу", agent.get("name"), agent.get("username"))
    while True:
        try:
            updates = api(token, "getUpdates", http_timeout=_POLL_TIMEOUT + 15,
                          timeout=_POLL_TIMEOUT, offset=_offsets.get(slug, 0),
                          allowed_updates=["message"])
        except Exception as exc:  # noqa: BLE001
            log.warning("getUpdates (%s): %s", slug, str(exc)[:150])
            time.sleep(5)
            continue
        for upd in updates or []:
            _offsets[slug] = max(_offsets.get(slug, 0), int(upd.get("update_id", 0)) + 1)
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            text = (msg.get("text") or msg.get("caption") or "").strip()
            sender = msg.get("from") or {}
            if chat.get("type") != "private" or not text or sender.get("is_bot"):
                continue
            try:
                _answer(agent, chat.get("id"), sender, text)
            except Exception:  # noqa: BLE001
                log.exception("ход агента %s упал", slug)


def start_all() -> None:
    """Поднять поток на каждого активного агента и следить за появлением новых."""
    def supervisor():
        while True:
            for agent in load_agents():
                slug = agent["slug"]
                alive = _threads.get(slug)
                if alive and alive.is_alive():
                    continue
                th = threading.Thread(target=_poll, args=(agent,), daemon=True,
                                      name=f"tg-{slug}")
                _threads[slug] = th
                th.start()
            time.sleep(_RELOAD_S)

    threading.Thread(target=supervisor, daemon=True, name="tg-multi-supervisor").start()
