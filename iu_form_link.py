# -*- coding: utf-8 -*-
"""Персональная одноразовая ссылка на анкету ИУ и страница-переход по ней.

Зачем это вообще. Анкету принимает CRM-форма Битрикса, и сделку из неё создаёт САМ Битрикс —
наш код в этот момент не спрашивают. Поэтому человек, уже заведённый в воронку из Telegram,
получал вторую карточку, и склеить их было нечем: в названии формовой сделки нет ничего
опознаваемого (семь одинаковых строк «Заполнение CRM-формы …» на 29.07.2026).

Разбор виджета формы (`app.js`, 95 КБ) показал: из адреса он читает РОВНО пять utm-меток и
больше ничего — ни путь, ни произвольные параметры до сделки не доезжают. Живая заявка
29.07.2026 это подтвердила: сделка 264 приехала с `UTM_CONTENT=tg-TESTTOKEN1`. Значит метка —
единственный канал, и в него кладётся токен.

Почему токен, а не сам telegram_id: ссылку пересылают и сохраняют, а номер аккаунта в открытом
адресе светиться не должен. Плюс одноразовость позволяет заметить, что ссылкой воспользовались
второй раз.

Почему ссылка идёт через НАС, а не прямо на форму. Погасить битриксовую форму мы не можем — она
про наш токен ничего не знает и всегда открыта. Поэтому бот даёт адрес этой страницы: она
проверяет токен и либо перебрасывает на анкету с меткой, либо честно говорит, что анкета уже
заполнена. Персональных данных страница не собирает и не хранит — только сверяет токен.

Слой с базой отделён от слоя с Flask: вся логика токена (выдача, повтор, срок, гашение) — чистые
функции над соединением, поэтому она проверяется тестами без поднятия приложения.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

log = logging.getLogger("iu-form-link")

#: Адрес анкеты. Это публичный сайт компании, а не адрес портала: показывать клиенту
#: внутренние адреса Битрикса запрещено (владелец, 22.07.2026), и исходящий фильтр их режет.
FORM_URL = os.getenv("IU_FORM_URL", "https://b24-9qcm4m.bitrix24site.ru/").strip()
#: Наш домен, на котором живёт страница перехода.
LINK_BASE = os.getenv("IU_FORM_LINK_BASE", "https://www.m4s.ru").strip().rstrip("/")
#: Сколько живёт невостребованная ссылка. Не заполнил за неделю — бот выдаст новую.
TOKEN_TTL_DAYS = int(os.getenv("IU_FORM_TOKEN_TTL_DAYS", "7") or 7)
#: Метка источника: по ней вотчер отличает наши заявки от рекламных.
UTM_SOURCE = os.getenv("IU_FORM_UTM_SOURCE", "tg_bot").strip()
UTM_MEDIUM = os.getenv("IU_FORM_UTM_MEDIUM", "bot").strip()
#: Префикс значения метки. Нужен, чтобы НЕ принять чужую рекламную метку за свой токен:
#: ячейка utm в браузере одна на весь сайт, и туда мог попасть чужой параметр.
TOKEN_PREFIX = "tg-"

#: Длина токена. 16 символов base64url ≈ 96 бит — подобрать перебором нельзя, а в адресе
#: строка остаётся короткой и не пугает клиента.
_TOKEN_BYTES = 12


class TokenError(RuntimeError):
    """Токен не выдан. Ход обязан продолжиться без ссылки, а не упасть."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def mark_value(token: str) -> str:
    """Значение utm-метки для этого токена."""
    return f"{TOKEN_PREFIX}{token}"


def token_from_mark(value: str) -> str:
    """Токен из значения метки. Чужая метка (без нашего префикса) — пустая строка."""
    text = str(value or "").strip()
    if not text.startswith(TOKEN_PREFIX):
        return ""
    return text[len(TOKEN_PREFIX):].strip()


def link_for(token: str) -> str:
    """Адрес страницы перехода, который получает клиент."""
    return f"{LINK_BASE}/iu/{quote(str(token or ''), safe='')}"


def form_url_for(token: str) -> str:
    """Адрес анкеты с меткой. Именно сюда страница перехода отправляет клиента."""
    marks = urlencode({
        "utm_source": UTM_SOURCE,
        "utm_medium": UTM_MEDIUM,
        "utm_content": mark_value(token),
    })
    joiner = "&" if "?" in FORM_URL else "?"
    return f"{FORM_URL}{joiner}{marks}"


# --- работа с хранилищем ---------------------------------------------------------------------

def issue(conn, telegram_id: int, *, conversation_id=None, deal_id=None) -> dict:
    """Выдать ссылку человеку. Повторное нажатие возвращает ТУ ЖЕ живую ссылку.

    Иначе одноразовость превращается в фикцию: каждое нажатие плодило бы новый токен, а старые
    оставались бы рабочими, и «анкета уже заполнена» не срабатывало бы никогда."""
    person = int(telegram_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT token, expires_at, used_at FROM iu_form_tokens
            WHERE telegram_id = %s AND used_at IS NULL AND expires_at > now()
            ORDER BY created_at DESC LIMIT 1
            """,
            (person,),
        )
        row = cur.fetchone()
        if row:
            return {"token": row["token"], "url": link_for(row["token"]), "reused": True}

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        cur.execute(
            """
            INSERT INTO iu_form_tokens (token, telegram_id, conversation_id, deal_id, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING token
            """,
            (token, person, conversation_id, deal_id,
             _now() + timedelta(days=TOKEN_TTL_DAYS)),
        )
        created = cur.fetchone()["token"]
    return {"token": created, "url": link_for(created), "reused": False}


def resolve(conn, token: str) -> dict | None:
    """Строка токена или None. Ничего не меняет."""
    value = str(token or "").strip()
    if not value:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM iu_form_tokens WHERE token = %s", (value,))
        row = cur.fetchone()
    return dict(row) if row else None


def mark_opened(conn, token: str) -> None:
    """Отметить переход по ссылке. Заполнением это не считается.

    Счётчик переходов нужен сценарию: «перешёл и не заполнил» — повод напомнить, а не молчать."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE iu_form_tokens
               SET opened_at = COALESCE(opened_at, now()), open_count = open_count + 1
             WHERE token = %s
            """,
            (str(token or "").strip(),),
        )


def burn(conn, token: str, *, deal_id=None) -> bool:
    """Погасить токен по факту заполнения анкеты. Повторное гашение ничего не ломает.

    Возвращает True, только если токен погас именно этим вызовом: вотчер по этому признаку
    понимает, что заявку он видит впервые, и не переносит одни и те же поля дважды."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE iu_form_tokens
               SET used_at = now(), used_deal_id = COALESCE(%s, used_deal_id)
             WHERE token = %s AND used_at IS NULL
            RETURNING token
            """,
            (deal_id, str(token or "").strip()),
        )
        return cur.fetchone() is not None


def filled_by(conn, telegram_id: int) -> dict | None:
    """Заполненная этим человеком анкета, если она была. Иначе None.

    По ней бот отвечает «вы уже заполнили» вместо второй ссылки: владелец 29.07.2026 —
    «если для его айди заполнено, то всё отлично»."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT token, used_at, used_deal_id FROM iu_form_tokens
            WHERE telegram_id = %s AND used_at IS NOT NULL
            ORDER BY used_at DESC LIMIT 1
            """,
            (int(telegram_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def state_of(row: dict | None) -> str:
    """Что показывать по этой ссылке: `open` | `used` | `expired` | `unknown`."""
    if not row:
        return "unknown"
    if row.get("used_at"):
        return "used"
    expires = row.get("expires_at")
    if expires and expires <= _now():
        return "expired"
    return "open"


# --- страница перехода -----------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>
<style>
 body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#f5f7fa;font:16px/1.55 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1d2530}}
 .card{{max-width:32rem;margin:1.5rem;padding:2rem;background:#fff;border-radius:14px;
  box-shadow:0 2px 18px rgba(20,35,60,.08);text-align:center}}
 h1{{margin:0 0 .75rem;font-size:1.3rem}}
 p{{margin:0 0 .5rem;color:#41505f}}
</style></head>
<body><div class="card"><h1>{title}</h1><p>{text}</p></div></body></html>"""


def _page(title: str, text: str) -> str:
    return _PAGE.format(title=title, text=text)


try:  # pragma: no cover — при импорте в тестах Flask-приложения может не быть
    from flask import redirect

    from app import app, pg_connect

    @app.get("/iu/<token>")
    def iu_form_link_page(token: str):
        """Переход по персональной ссылке: на анкету или к честному объяснению."""
        try:
            with pg_connect() as conn:
                row = resolve(conn, token)
                state = state_of(row)
                if state == "open":
                    mark_opened(conn, token)
        except Exception:  # noqa: BLE001
            # База недоступна — отправляем на анкету без метки. Потерять заявку хуже, чем
            # потерять склейку: склейку добьём по username и телефону.
            log.warning("ссылка %s не проверена — пускаем на анкету без метки",
                        str(token)[:12], exc_info=True)
            return redirect(FORM_URL, code=302)

        if state == "open":
            return redirect(form_url_for(token), code=302)
        if state == "used":
            return _page("Анкета уже заполнена",
                         "Мы её получили — вернитесь в чат с ботом, там продолжим. "
                         "Если нужно что-то исправить, напишите об этом в чате."), 200
        if state == "expired":
            return _page("Ссылка устарела",
                         "Вернитесь в чат с ботом и нажмите «Присоединиться к ИУ» — "
                         "он выдаст новую ссылку."), 200
        return _page("Ссылка не найдена",
                     "Похоже, адрес скопирован не полностью. Вернитесь в чат с ботом "
                     "и откройте ссылку оттуда."), 404

except ImportError:  # pragma: no cover — модуль используется и без Flask (тесты, вотчер)
    log.debug("Flask-приложение недоступно: страница перехода не зарегистрирована")
