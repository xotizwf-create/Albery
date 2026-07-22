"""Журнал Telegram-переписок и доступ к агенту — то, что видно в кабинете.

Требование владельца 22.07.2026: переписки в Telegram должны логироваться так же, как в Битриксе
(bitrix_bot_messages с 052), с разделением на два агента-канала одного бот-токена
@Albery_AI2_Bot: личка бота и бизнес-аккаунт @AlberyAIManager. У менеджера разговор с самим
агентом («в боте») хранится отдельно от переписок с пользователями.

Границы, заданные владельцем: в журнал попадают ТОЛЬКО чаты, где участвовал агент. Бизнес-режим
видит и личную переписку аккаунта с поставщиками и знакомыми — ей не место в корпоративном
кабинете.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def tg(monkeypatch, tmp_path):
    import tg_agent

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"business": {"C1": {"user_id": 8715335144}}}), encoding="utf-8")
    monkeypatch.setattr(tg_agent, "STATE_PATH", state_file)
    monkeypatch.setattr(tg_agent, "BUSINESS_LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(tg_agent, "load_state", lambda: json.loads(state_file.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg_agent, "save_state",
                        lambda s: state_file.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8"))
    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    monkeypatch.setenv("TG_LEAD_INVITE", "1")
    tg_agent._LEADS_CACHE.update({"at": 0.0, "map": {}, "ok": True})
    tg_agent._ACCESS_CACHE.update({"at": 0.0, "by_bot": {}})
    monkeypatch.setattr(tg_agent, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})
    return tg_agent


@pytest.fixture
def rows(tg, monkeypatch):
    """Перехват записей журнала вместо похода в PostgreSQL."""
    box = []
    monkeypatch.setattr(tg, "journal",
                        lambda bot, dialog_id, direction, text, **kw: box.append(
                            {"bot": bot, "dialog_id": dialog_id, "direction": direction,
                             "text": text, **kw}))
    return box


def _biz(username="griaznov.d", uid=555, text="привет"):
    return {"business_connection_id": "C1", "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "username": username, "first_name": "Дмитрий"}, "text": text}


def test_lead_conversation_is_journalled_both_ways(tg, rows, monkeypatch):
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: "Здравствуйте! Уточните оборот.")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (True, ""))

    tg.maybe_autoreply(_biz(text="какие условия?"))

    assert [r["direction"] for r in rows] == ["in", "out"]
    assert all(r["bot"] == tg.MANAGER_CHANNEL and r["kind"] == "lead_chat" for r in rows)
    assert rows[0]["text"] == "какие условия?"
    assert rows[0]["meta"]["deal_id"] == 82, "переписку надо связать со сделкой"


def test_supplier_chat_is_not_journalled_while_agent_stays_silent(tg, rows, monkeypatch):
    """Главная граница приватности: молчит агент — записи нет."""
    monkeypatch.delenv("TG_LEAD_INVITE", raising=False)   # незнакомцам агент не отвечает
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: "ответ")

    tg.maybe_autoreply(_biz(username="postavshik", uid=999, text="привезём завтра"))

    assert rows == [], "личная переписка аккаунта в кабинет попадать не должна"


def test_stranger_is_journalled_once_the_agent_replies(tg, rows, monkeypatch):
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: "Здравствуйте! Чем помочь?")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (True, ""))

    tg.maybe_autoreply(_biz(username="ivan_novy", uid=999, text="хочу подключить"))

    assert [r["direction"] for r in rows] == ["in", "out"]
    assert rows[1]["meta"]["stranger"] is True


def test_owner_talk_to_the_agent_is_a_separate_stream(tg, rows):
    """Подвкладка «в боте» не должна смешиваться с перепиской по лидам."""
    tg.handle_business_message({"business_connection_id": "C1",
                                "chat": {"id": 8886445861, "type": "private"},
                                "from": {"id": 8715335144, "username": "alberyaimanager"},
                                "text": "покажи сделки"})

    assert len(rows) == 1
    assert rows[0]["bot"] == tg.MANAGER_CHANNEL and rows[0]["kind"] == "bot_dm"


def test_failed_delivery_is_marked_as_error(tg, rows, monkeypatch):
    """В кабинете сбойный ход должен быть видно, а не выглядеть обычным ответом."""
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: "ответ")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (False, "сеть"))

    tg.maybe_autoreply(_biz(text="вопрос"))

    assert rows[-1]["status"] == "error"


def test_brain_failure_is_journalled_too(tg, rows, monkeypatch):
    """Иначе в кабинете будет вопрос клиента без единого следа ответа."""
    def boom(p, s):
        raise RuntimeError("мозг недоступен")

    monkeypatch.setattr(tg, "hermes_answer", boom)

    tg.maybe_autoreply(_biz(text="вопрос"))

    assert rows[-1]["direction"] == "out" and rows[-1]["status"] == "error"


def test_journal_failure_never_breaks_the_agent(tg, monkeypatch):
    """Логирование — побочная функция: упавшая база не должна ронять ответ клиенту."""
    def broken_db():
        raise RuntimeError("postgres недоступен")

    monkeypatch.setattr(tg, "_db", broken_db)

    tg.journal(tg.MANAGER_CHANNEL, 1, "in", "текст")   # не должно бросить исключение


# --- доступ к агенту -----------------------------------------------------------------------

def test_access_list_comes_from_the_database(tg, monkeypatch):
    monkeypatch.setattr(tg, "access_usernames", lambda bot: {"alexxandrn", "evgeniy_pal"})

    assert tg.is_owner({"id": 1, "username": "Evgeniy_Pal"}) is True
    assert tg.is_owner({"id": 2, "username": "postoronniy"}) is False


def test_env_is_the_fallback_when_the_database_is_empty(tg, monkeypatch):
    """Пустая база не должна закрыть агента для всех — иначе один сбой обрывает связь."""
    monkeypatch.setattr(tg, "access_usernames", lambda bot: set())
    monkeypatch.setenv("TG_AGENT_OWNER_USERNAMES", "AlberyAIManager")

    assert tg.is_owner({"id": 1, "username": "alberyaimanager"}) is True


def test_access_cache_survives_a_database_outage(tg, monkeypatch):
    """Пока список известен, обрыв базы не должен менять поведение агента."""
    tg._ACCESS_CACHE["by_bot"][tg.BOT_CHANNEL] = {"alexxandrn"}
    tg._ACCESS_CACHE["at"] = 0.0     # кэш просрочен -> пойдёт в базу

    def broken_db():
        raise RuntimeError("postgres недоступен")

    monkeypatch.setattr(tg, "_db", broken_db)

    assert tg.access_usernames(tg.BOT_CHANNEL) == {"alexxandrn"}


def test_stranger_message_to_the_bot_is_journalled_with_the_refusal(tg, rows, monkeypatch):
    """Отказ — тоже работа агента: в кабинете должно быть видно, кто ломился и что получил."""
    monkeypatch.setattr(tg, "access_usernames", lambda bot: {"alexxandrn"})
    monkeypatch.setattr(tg, "send_text", lambda chat_id, text: None)

    tg.handle_message({"chat": {"id": 777, "type": "private"},
                       "from": {"id": 777, "username": "chuzhoy"}, "text": "пусти"})

    assert [r["direction"] for r in rows] == ["in", "out"]
    assert all(r["bot"] == tg.BOT_CHANNEL for r in rows)
    assert rows[1]["meta"]["denied"] is True
