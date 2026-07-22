"""Telegram-агенты, которых владелец заводит сам.

Требование владельца 22.07.2026: в Telegram агенты создаются и настраиваются ТАК ЖЕ, как в
Битриксе — с инструментами, инструкциями и знаниями; отличается только мост. Поэтому такой
агент — обычная запись в таблице `agents` (как субагент Битрикса), у которой вместо
bitrix_bot_id заполнен telegram_bot_token, и он работает на своём коннекторе agent-<slug>.

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
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: "Здравствуйте! Слушаю вас.")

    multi._answer(AGENT, 555, {"id": 555, "username": "alexxandrn"}, "привет")

    assert sent[-1]["method"] == "sendMessage" and sent[-1]["chat_id"] == 555
    assert sent[-1]["token"] == "111:AAA", "агент обязан писать СВОИМ токеном"
    assert [r["direction"] for r in rows] == ["in", "out"]
    assert all(r["bot"] == "prodazhi-bot" for r in rows), "журнал ведётся по своему каналу"


def test_stranger_is_refused_and_the_brain_is_not_called(multi, sent, rows, monkeypatch):
    import tg_agent
    calls = []
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: {"alexxandrn"})
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")

    multi._answer(AGENT, 777, {"id": 777, "username": "chuzhoy"}, "пусти")

    assert calls == [], "постороннему модель не запускаем"
    assert rows[-1]["meta"]["denied"] is True


def test_role_prompt_reaches_the_brain(multi, sent, rows, monkeypatch):
    import tg_agent
    prompts = []
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: set())
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: prompts.append((p, toolsets)) or "ок")

    multi._answer(AGENT, 555, {"id": 555, "username": "kto_ugodno"}, "вопрос")

    assert "консультант по продажам" in prompts[0][0]


def test_agent_runs_on_its_own_connector(multi, sent, rows, monkeypatch):
    """Ради этого Telegram-агент и живёт в общей таблице agents: коннектор agent-<slug> даёт
    ему ИМЕННО его набор MCP-инструментов, подключённые инструкции и знания. Без него агент
    был бы говорящей головой без инструментов — не то же самое, что агент в Битриксе."""
    import tg_agent
    seen = []
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: set())
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: seen.append(toolsets) or "ок")

    multi._answer(AGENT, 555, {"id": 555, "username": "kto"}, "вопрос")

    assert seen[0].startswith("agent-prodazhi-bot"), seen


def test_empty_access_list_does_not_lock_everyone_out(multi, sent, rows, monkeypatch):
    """Пустой список = ограничений нет. Иначе новый агент был бы нем сразу после создания."""
    import tg_agent
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: set())
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: "Здравствуйте!")

    multi._answer(AGENT, 555, {"id": 555, "username": "kto_ugodno"}, "привет")

    assert any(r["direction"] == "out" and r.get("status", "ok") == "ok" for r in rows)


def test_brain_failure_is_journalled_and_does_not_crash(multi, sent, rows, monkeypatch):
    import tg_agent
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: set())

    def boom(p, s, toolsets=None):
        raise RuntimeError("мозг недоступен")

    monkeypatch.setattr(tg_agent, "hermes_answer", boom)

    multi._answer(AGENT, 555, {"id": 555, "username": "kto"}, "вопрос")

    assert rows[-1]["status"] == "error"


def test_undelivered_answer_is_marked_as_error(multi, rows, monkeypatch):
    import tg_agent
    monkeypatch.setattr(tg_agent, "access_usernames", lambda bot: set())
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: "ответ")

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


# --- регистрация бота через @BotFather ---------------------------------------------------
# Проверено на живом Telegram 22.07.2026: аккаунт компании пишет BotFather от бизнес-подключения,
# и его ответы возвращаются в бизнес-журнал. Поэтому агент проводит диалог /newbot сам.

@pytest.fixture
def botfather(multi, monkeypatch, tmp_path):
    """Поддельный BotFather: пишет ответы в тот же журнал, который читает агент."""
    import tg_agent
    log_path = tmp_path / "business.jsonl"
    log_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(tg_agent, "BUSINESS_LOG_PATH", log_path)
    monkeypatch.setattr(multi.core, "BUSINESS_LOG_PATH", log_path, raising=False)
    said = []
    replies = {}

    def fake_send(uid, text, parse_mode=""):
        said.append(text)
        reply = replies.get(len(said))
        if reply is not None:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(_json.dumps({"at": _dt.now(_tz.utc).isoformat(),
                                      "from_id": multi.BOTFATHER_ID, "chat_id": multi.BOTFATHER_ID,
                                      "text": reply}, ensure_ascii=False) + "\n")
        return (True, "")

    monkeypatch.setattr(multi.core, "send_as_account", fake_send)
    monkeypatch.setattr(multi.time, "sleep", lambda s: None)
    return {"said": said, "replies": replies}


def test_bot_is_registered_through_botfather(multi, botfather):
    botfather["replies"].update({
        1: "Alright, a new bot. How are we going to call it? Please choose a name for your bot.",
        2: "Good. Now let's choose a username for your bot. It must end in `bot`.",
        3: ("Done! Congratulations on your new bot.\n\nUse this token to access the HTTP API:\n"
            "8123456789:AAFakeTokenForTestsOnly-000111222333\n\nKeep your token secure."),
    })

    made = multi.create_bot_via_botfather("Агент продаж", "albery_sales_bot")

    assert made["token"] == "8123456789:AAFakeTokenForTestsOnly-000111222333"
    assert botfather["said"][0] == "/newbot"
    assert botfather["said"][1] == "Агент продаж"
    assert botfather["said"][2] == "albery_sales_bot"


def test_taken_username_returns_botfathers_own_words(multi, botfather):
    """Гадать за BotFather нельзя: его текст точнее объясняет, что не так."""
    botfather["replies"].update({
        1: "Alright, a new bot. How are we going to call it?",
        2: "Good. Now let's choose a username for your bot.",
        3: "Sorry, this username is already taken. Please try something different.",
    })

    with pytest.raises(RuntimeError, match="already taken"):
        multi.create_bot_via_botfather("Агент продаж", "albery_sales_bot")


def test_username_must_end_with_bot(multi, botfather):
    """Telegram откажет всё равно — отсекаем до диалога, чтобы не мусорить в чате BotFather."""
    with pytest.raises(ValueError, match="bot"):
        multi.create_bot_via_botfather("Агент", "albery_sales")

    assert botfather["said"] == []


def test_silent_botfather_does_not_hang_forever(multi, botfather, monkeypatch):
    """Молчание BotFather должно давать понятную ошибку, а не вечное ожидание."""
    monkeypatch.setattr(multi, "_botfather_wait", lambda from_line, timeout_s=25: "")

    with pytest.raises(RuntimeError, match="молчит"):
        multi.create_bot_via_botfather("Агент", "albery_x_bot")


# --- удаление агента ------------------------------------------------------------------------
# Владелец удалил агента в кабинете, а бот в Telegram продолжал отвечать (22.07.2026): поток
# опроса жил своей жизнью, потому что супервизор только ПОДНИМАЛ потоки и никогда их не гасил.

def test_deleted_agent_stops_answering(multi, monkeypatch):
    """Самое неприятное для владельца: удалил — а бот продолжает говорить от имени компании."""
    import tg_agent
    monkeypatch.setattr(multi, "_is_wanted", lambda slug: False)
    calls = []
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")
    sent = []
    monkeypatch.setattr(multi, "api", lambda token, method, http_timeout=35, **p: (
        sent.append(p) or ([] if method == "getUpdates" else {"message_id": 1})))

    multi._poll(AGENT)      # должен выйти сам, а не крутиться вечно

    assert calls == [], "удалённый агент не должен обращаться к модели"
    assert multi._threads.get("prodazhi-bot") is None


def test_database_outage_does_not_silence_a_live_agent(multi, monkeypatch):
    """Обрыв базы — не повод глушить работающего агента: иначе сбой связи = молчащий бот."""
    import tg_agent

    def broken_db():
        raise RuntimeError("postgres недоступен")

    monkeypatch.setattr(tg_agent, "_db", broken_db)

    assert multi._is_wanted("prodazhi-bot") is True
