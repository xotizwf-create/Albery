"""Telegram-агенты, которых владелец заводит сам.

Требование владельца 22.07.2026: в Telegram тоже нужно спокойно создавать агентов, и под это
нужны MCP-инструменты. Каждый такой агент — отдельный бот со своим токеном, своим списком
доступа и своей веткой журнала.

Главное ограничение: основной бот (@Albery_AI2_Bot) несёт бизнес-режим, лидов и воронку —
новые агенты не должны его задевать, поэтому они живут в отдельном модуле и отдельных потоках.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def multi(monkeypatch, tmp_path):
    import tg_agent
    import tg_multi

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"business": {}}), encoding="utf-8")
    monkeypatch.setattr(tg_agent, "STATE_PATH", state_file)
    monkeypatch.setattr(tg_agent, "load_state", lambda: json.loads(state_file.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg_agent, "save_state",
                        lambda s: state_file.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8"))
    tg_agent._ACCESS_CACHE.update({"at": 0.0, "by_bot": {}})
    return tg_multi


AGENT = {"slug": "prodazhi-bot", "name": "Продажи", "username": "prodazhi_bot",
         "bot_token": "111:AAA", "role_prompt": "Ты консультант по продажам.", "bot_user_id": 111}


@pytest.fixture
def sent(multi, monkeypatch):
    box = []
    monkeypatch.setattr(multi, "api",
                        lambda token, method, http_timeout=35, **p: box.append(
                            {"token": token, "method": method, **p}) or {"message_id": 1})
    return box


@pytest.fixture
def rows(multi, monkeypatch):
    import tg_agent
    box = []
    monkeypatch.setattr(tg_agent, "journal",
                        lambda bot, dialog_id, direction, text, **kw: box.append(
                            {"bot": bot, "direction": direction, "text": text, **kw}))
    return box


def test_allowed_person_gets_an_answer(multi, sent, rows, monkeypatch):
    import tg_agent
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: {"alexxandrn"})
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s: "Здравствуйте! Слушаю вас.")

    multi._answer(AGENT, 555, {"id": 555, "username": "alexxandrn"}, "привет")

    assert sent[-1]["method"] == "sendMessage" and sent[-1]["chat_id"] == 555
    assert sent[-1]["token"] == "111:AAA", "агент обязан писать СВОИМ токеном"
    assert [r["direction"] for r in rows] == ["in", "out"]
    assert all(r["bot"] == "prodazhi-bot" for r in rows), "журнал ведётся по своему каналу"


def test_stranger_is_refused_and_the_brain_is_not_called(multi, sent, rows, monkeypatch):
    import tg_agent
    calls = []
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: {"alexxandrn"})
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s: calls.append(1) or "ответ")

    multi._answer(AGENT, 777, {"id": 777, "username": "chuzhoy"}, "пусти")

    assert calls == [], "постороннему модель не запускаем"
    assert rows[-1]["meta"]["denied"] is True


def test_role_prompt_reaches_the_brain(multi, sent, rows, monkeypatch):
    import tg_agent
    prompts = []
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: set())
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s: prompts.append(p) or "ок")

    multi._answer(AGENT, 555, {"id": 555, "username": "kto_ugodno"}, "вопрос")

    assert "консультант по продажам" in prompts[0]


def test_empty_access_list_does_not_lock_everyone_out(multi, sent, rows, monkeypatch):
    """Пустой список = ограничений нет. Иначе новый агент был бы нем сразу после создания."""
    import tg_agent
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: set())
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s: "Здравствуйте!")

    multi._answer(AGENT, 555, {"id": 555, "username": "kto_ugodno"}, "привет")

    assert any(r["direction"] == "out" and r.get("status", "ok") == "ok" for r in rows)


def test_brain_failure_is_journalled_and_does_not_crash(multi, sent, rows, monkeypatch):
    import tg_agent
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: set())

    def boom(p, s):
        raise RuntimeError("мозг недоступен")

    monkeypatch.setattr(tg_agent, "hermes_answer", boom)

    multi._answer(AGENT, 555, {"id": 555, "username": "kto"}, "вопрос")

    assert rows[-1]["status"] == "error"


def test_undelivered_answer_is_marked_as_error(multi, rows, monkeypatch):
    import tg_agent
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: set())
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s: "ответ")

    def broken(token, method, http_timeout=35, **p):
        raise RuntimeError("Telegram отказал")

    monkeypatch.setattr(multi, "api", broken)

    multi._answer(AGENT, 555, {"id": 555, "username": "kto"}, "вопрос")

    assert rows[-1]["status"] == "error"


def test_database_outage_leaves_no_agents_instead_of_crashing(multi, monkeypatch):
    """Служба не должна падать из-за базы: основной бот работает дальше."""
    import tg_agent

    def broken_db():
        raise RuntimeError("postgres недоступен")

    monkeypatch.setattr(tg_agent, "_db", broken_db)

    assert multi.load_agents() == []
