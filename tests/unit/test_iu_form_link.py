"""Персональная одноразовая ссылка на анкету ИУ.

Владелец 29.07.2026: «человек нажал присоединиться — ему вышла его уникальная ссылка. Перешёл
с телефона — не заполнил. Перешёл с компа — заполнил, действие ссылки истекло. Если ещё раз
перейдёт — не получится, а бот скажет: вы уже заполнили анкету».

Почему метка, а не поле формы: виджет CRM-формы читает из адреса РОВНО пять utm-параметров и
ничего больше — проверено разбором `app.js` и живой заявкой 29.07.2026 (сделка 264 приехала с
`UTM_CONTENT=tg-TESTTOKEN1`). Ни путь, ни произвольный параметр до сделки не доезжают.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import iu_form_link as link


class FakeCursor:
    """Курсор поверх словаря строк: логика токена проверяется без Postgres."""

    def __init__(self, rows: dict):
        self.rows = rows
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=()):
        text = " ".join(sql.split())
        if text.startswith("SELECT token, expires_at, used_at FROM iu_form_tokens"):
            person = params[0]
            now = datetime.now(timezone.utc)
            live = [r for r in self.rows.values()
                    if r["telegram_id"] == person and not r["used_at"]
                    and r["expires_at"] > now]
            self._result = max(live, key=lambda r: r["created_at"]) if live else None
        elif text.startswith("INSERT INTO iu_form_tokens"):
            token, person, conversation_id, deal_id, expires = params
            self.rows[token] = {
                "token": token, "telegram_id": person, "conversation_id": conversation_id,
                "deal_id": deal_id, "created_at": datetime.now(timezone.utc),
                "expires_at": expires, "opened_at": None, "open_count": 0,
                "used_at": None, "used_deal_id": None,
            }
            self._result = {"token": token}
        elif text.startswith("SELECT * FROM iu_form_tokens"):
            self._result = self.rows.get(params[0])
        elif text.startswith("UPDATE iu_form_tokens SET opened_at"):
            row = self.rows.get(params[0])
            if row:
                row["opened_at"] = row["opened_at"] or datetime.now(timezone.utc)
                row["open_count"] += 1
            self._result = None
        elif text.startswith("UPDATE iu_form_tokens SET used_at"):
            deal_id, token = params
            row = self.rows.get(token)
            if row and not row["used_at"]:
                row["used_at"] = datetime.now(timezone.utc)
                row["used_deal_id"] = deal_id or row["used_deal_id"]
                self._result = {"token": token}
            else:
                self._result = None
        else:  # pragma: no cover — незнакомый запрос в тесте это ошибка теста
            raise AssertionError(f"неожиданный запрос: {text[:80]}")

    def fetchone(self):
        return self._result


class FakeConn:
    def __init__(self):
        self.rows: dict = {}

    def cursor(self):
        return FakeCursor(self.rows)


# --- адреса ----------------------------------------------------------------------------------

def test_mark_carries_the_token_and_is_recognisable():
    """Своя метка обязана отличаться от рекламной: ячейка utm в браузере одна на весь сайт."""
    assert link.mark_value("abc") == "tg-abc"
    assert link.token_from_mark("tg-abc") == "abc"
    assert link.token_from_mark("yandex_direct_42") == ""
    assert link.token_from_mark("") == ""


def test_form_url_carries_the_mark():
    url = link.form_url_for("abc")

    assert "utm_content=tg-abc" in url
    assert "utm_source=" in url
    assert url.startswith(link.FORM_URL)


def test_client_link_points_at_our_page():
    assert link.link_for("abc") == f"{link.LINK_BASE}/iu/abc"


# --- жизненный цикл токена -------------------------------------------------------------------

def test_repeat_press_returns_the_same_link():
    """Иначе одноразовость — фикция: каждое нажатие плодило бы токен, а старые оставались бы
    рабочими, и «анкета уже заполнена» не срабатывало бы никогда."""
    conn = FakeConn()

    first = link.issue(conn, 555)
    second = link.issue(conn, 555)

    assert second["token"] == first["token"]
    assert second["reused"] is True
    assert len(conn.rows) == 1


def test_each_person_gets_his_own_link():
    conn = FakeConn()

    assert link.issue(conn, 555)["token"] != link.issue(conn, 777)["token"]


def test_opening_the_link_is_not_filling_it():
    """«Перешёл с телефона — не заполнил»: переход отмечается, но ссылка остаётся рабочей."""
    conn = FakeConn()
    token = link.issue(conn, 555)["token"]

    link.mark_opened(conn, token)
    link.mark_opened(conn, token)
    row = link.resolve(conn, token)

    assert row["open_count"] == 2
    assert row["opened_at"] is not None
    assert link.state_of(row) == "open"
    assert link.issue(conn, 555)["token"] == token


def test_filling_burns_the_link():
    conn = FakeConn()
    token = link.issue(conn, 555)["token"]

    assert link.burn(conn, token, deal_id=264) is True
    assert link.state_of(link.resolve(conn, token)) == "used"


def test_the_same_form_is_not_merged_twice():
    """Вотчер может увидеть одну заявку дважды — перенос полей обязан случиться один раз."""
    conn = FakeConn()
    token = link.issue(conn, 555)["token"]

    assert link.burn(conn, token, deal_id=264) is True
    assert link.burn(conn, token, deal_id=264) is False


def test_a_new_link_is_issued_after_the_old_one_burned():
    """Анкету могли удалить в Битриксе — тогда человеку нужна новая ссылка.

    Решение, показывать её или ответить «вы уже заполнили», принимает сценарий по ЖИВОЙ
    сверке со сделкой, а не по этой отметке."""
    conn = FakeConn()
    token = link.issue(conn, 555)["token"]
    link.burn(conn, token, deal_id=264)

    assert link.issue(conn, 555)["token"] != token


def test_expired_link_is_not_reused():
    conn = FakeConn()
    token = link.issue(conn, 555)["token"]
    conn.rows[token]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert link.state_of(link.resolve(conn, token)) == "expired"
    assert link.issue(conn, 555)["token"] != token


def test_unknown_token_is_not_a_crash():
    conn = FakeConn()

    assert link.resolve(conn, "нет-такого") is None
    assert link.resolve(conn, "") is None
    assert link.state_of(None) == "unknown"


# --- что отвечает бот ------------------------------------------------------------------------

def test_answer_hides_the_link_when_the_real_anketa_exists():
    """Владелец 29.07.2026: «я в битриксе анкету удалил, а бот всё равно говорит, что
    человек её заполнил». Правда об анкете живёт в сделке, а не в нашей отметке."""
    import iu_client_bot

    body, filled = iu_client_bot.join_answer(
        "Вижу анкету:\n\n• Категории товара — Одежда\n\nВсё верно?", "")

    assert filled is True
    assert "• Категории товара — Одежда" in body
    assert "Если нужно что-то исправить или обсудить" in body
    assert "Если всё верно и помощь не требуется" in body
    assert "http" not in body


def test_answer_gives_the_link_when_the_anketa_is_gone():
    """Анкету удалили — человек обязан получить возможность заполнить её снова."""
    import iu_client_bot

    body, filled = iu_client_bot.join_answer("", "https://www.m4s.ru/iu/abc")

    assert filled is False
    assert "https://www.m4s.ru/iu/abc" in body


def test_blank_anketa_block_is_not_a_filled_anketa():
    import iu_client_bot

    assert iu_client_bot.join_answer("   \n ", "https://x/iu/1")[1] is False
