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
import requests


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
    monkeypatch.setattr(tg_multi, "_react", lambda *_args, **_kwargs: None)
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
    monkeypatch.setattr(multi, "_access_identity", lambda slug, sender: {
        "allowed": sender.get("username") == "alexxandrn",
        "reason": "allowed" if sender.get("username") == "alexxandrn" else "denied",
        "bitrix_user_id": 17,
    })
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: "Здравствуйте! Слушаю вас.")

    multi._answer(AGENT, 555, {"id": 555, "username": "alexxandrn"}, "привет")

    assert sent[-1]["method"] == "sendMessage" and sent[-1]["chat_id"] == 555
    assert sent[-1]["token"] == "111:AAA", "агент обязан писать СВОИМ токеном"
    assert [r["direction"] for r in rows] == ["in", "out"]
    assert all(r["bot"] == "prodazhi-bot" for r in rows), "журнал ведётся по своему каналу"


def test_stranger_is_refused_and_the_brain_is_not_called(multi, sent, rows, monkeypatch):
    import tg_agent
    calls = []
    monkeypatch.setattr(multi, "_access_identity", lambda slug, sender: {
        "allowed": sender.get("username") == "alexxandrn",
        "reason": "not_allowlisted",
        "bitrix_user_id": None,
    })
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")

    multi._answer(AGENT, 777, {"id": 777, "username": "chuzhoy"}, "пусти")

    assert calls == [], "постороннему модель не запускаем"
    assert rows[-1]["meta"]["denied"] is True


def test_role_prompt_reaches_the_brain(multi, sent, rows, monkeypatch):
    import tg_agent
    prompts = []
    monkeypatch.setattr(multi, "_access_identity", lambda slug, sender: {
        "allowed": True, "reason": "allowed", "bitrix_user_id": 17,
    })
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: prompts.append((p, toolsets)) or "ок")

    multi._answer(AGENT, 555, {"id": 555, "username": "kto_ugodno"}, "вопрос")

    assert "консультант по продажам" in prompts[0][0]


def test_agent_runs_on_its_own_connector(multi, sent, rows, monkeypatch):
    """Ради этого Telegram-агент и живёт в общей таблице agents: коннектор agent-<slug> даёт
    ему ИМЕННО его набор MCP-инструментов, подключённые инструкции и знания. Без него агент
    был бы говорящей головой без инструментов — не то же самое, что агент в Битриксе."""
    import tg_agent
    seen = []
    monkeypatch.setattr(multi, "_access_identity", lambda slug, sender: {
        "allowed": True, "reason": "allowed", "bitrix_user_id": 17,
    })
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: seen.append(toolsets) or "ок")

    multi._answer(AGENT, 555, {"id": 555, "username": "kto"}, "вопрос")

    assert seen[0].startswith("agent-prodazhi-bot"), seen


def test_empty_access_list_fails_closed(multi, sent, rows, monkeypatch):
    """Новый внутренний бот закрыт, пока администратор явно не выдаст доступ."""
    import tg_agent
    calls = []
    monkeypatch.setattr(multi, "_access_identity", lambda slug, sender: {
        "allowed": False, "reason": "empty_allowlist", "bitrix_user_id": None,
    })
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "answer")

    multi._answer(AGENT, 555, {"id": 555, "username": "kto_ugodno"}, "привет")

    assert calls == []
    assert rows[-1]["meta"]["denied"] is True


def test_brain_failure_is_journalled_and_does_not_crash(multi, sent, rows, monkeypatch):
    import tg_agent
    monkeypatch.setattr(multi, "_access_identity", lambda slug, sender: {
        "allowed": True, "reason": "allowed", "bitrix_user_id": 17,
    })

    def boom(p, s, toolsets=None):
        raise RuntimeError("мозг недоступен")

    monkeypatch.setattr(tg_agent, "hermes_answer", boom)

    multi._answer(AGENT, 555, {"id": 555, "username": "kto"}, "вопрос")

    assert rows[-1]["status"] == "error"


def test_undelivered_answer_is_marked_as_error(multi, rows, monkeypatch):
    import tg_agent
    monkeypatch.setattr(multi, "_access_identity", lambda slug, sender: {
        "allowed": True, "reason": "allowed", "bitrix_user_id": 17,
    })
    monkeypatch.setattr(tg_agent, "hermes_answer", lambda p, s, toolsets=None: "ответ")

    def broken(token, method, http_timeout=35, **p):
        raise RuntimeError("Telegram отказал")

    monkeypatch.setattr(multi, "api", broken)

    multi._answer(AGENT, 555, {"id": 555, "username": "kto"}, "вопрос")

    assert rows[-1]["status"] == "error"


def test_network_timeout_is_ambiguous_and_never_leaks_bot_token(multi, monkeypatch):
    secret = "123456:SUPER-SECRET-TOKEN"

    def timeout(*_args, **_kwargs):
        raise requests.Timeout("request URL contained a secret")

    monkeypatch.setattr(multi.requests, "post", timeout)

    with pytest.raises(multi.TelegramDeliveryAmbiguous) as caught:
        multi.api(secret, "sendMessage", chat_id=1, text="test")

    assert secret not in str(caught.value)


def test_access_username_is_bootstrap_only_after_stable_id_is_bound(multi):
    rows = [{
        "id": 1,
        "username": "anna",
        "tg_user_id": 77,
        "bitrix_user_id": 17,
        "display_name": "Анна",
    }]

    assert multi._select_access_row(rows, 77, "anna") == rows[0]
    assert multi._select_access_row(rows, 88, "anna") is None


def test_access_username_can_bootstrap_only_a_row_without_telegram_id(multi):
    rows = [{
        "id": 1,
        "username": "anna",
        "tg_user_id": None,
        "bitrix_user_id": 17,
        "display_name": "Анна",
    }]

    assert multi._select_access_row(rows, 77, "anna") == rows[0]
    assert multi._select_access_row(rows, None, "anna") is None


def test_durable_turn_uses_same_profile_and_creates_delivery_once(multi, monkeypatch):
    update = {
        "id": 31,
        "agent_slug": "prodazhi-bot",
        "provider_update_id": 700,
        "payload": {
            "message": {
                "message_id": 9,
                "chat": {"id": 555, "type": "private"},
                "from": {"id": 77, "username": "anna"},
                "text": "проверь задачи",
            }
        },
    }
    finished = []
    monkeypatch.setattr(multi, "_agent_for_slug", lambda slug: AGENT if slug == AGENT["slug"] else None)
    monkeypatch.setattr(multi, "_access_identity", lambda slug, sender: {
        "allowed": True, "reason": "allowed", "bitrix_user_id": 17,
    })
    monkeypatch.setattr(multi, "_run_agent_turn",
                        lambda agent, chat, sender, text, identity, **_kw: "готово")
    monkeypatch.setattr(multi, "_finish_update", lambda row, **kw: finished.append((row, kw)))
    monkeypatch.setattr(multi.core, "journal", lambda *_args, **_kwargs: None)

    multi._process_update(update)

    assert len(finished) == 1
    assert finished[0][0]["provider_update_id"] == 700
    assert finished[0][1] == {"chat_id": 555, "answer": "готово"}


def test_durable_turn_acknowledges_authorized_message_before_brain(multi, monkeypatch):
    update = {
        "id": 311,
        "agent_slug": "prodazhi-bot",
        "provider_update_id": 7011,
        "payload": {
            "message": {
                "message_id": 91,
                "chat": {"id": 555, "type": "private"},
                "from": {"id": 77, "username": "anna"},
                "text": "ping",
            }
        },
    }
    events = []
    monkeypatch.setattr(multi, "_agent_for_slug", lambda _slug: AGENT)
    monkeypatch.setattr(multi, "_access_identity", lambda *_args: {
        "allowed": True, "reason": "allowed", "bitrix_user_id": 17,
    })
    monkeypatch.setattr(
        multi,
        "_react",
        lambda token, chat_id, message_id, emoji: events.append(
            ("reaction", token, chat_id, message_id, emoji)
        ),
    )
    monkeypatch.setattr(
        multi,
        "_run_agent_turn",
        lambda *_args, **_kwargs: events.append(("brain",)) or "pong",
    )
    monkeypatch.setattr(multi, "_finish_update", lambda *_args, **_kwargs: events.append(("done",)))
    monkeypatch.setattr(multi.core, "journal", lambda *_args, **_kwargs: None)

    multi._process_update(update)

    assert events[0] == ("reaction", "111:AAA", 555, 91, "👀")
    assert events[1] == ("brain",)
    assert events[2] == ("done",)


def test_final_reaction_changes_to_thumbs_up_only_when_all_parts_are_sent(multi, monkeypatch):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params):
            assert "u.status = 'done'" in sql
            assert "o.status <> 'sent'" in sql
            assert params == (311, "prodazhi-bot")
        def fetchone(self):
            return {"payload": {"message": {
                "message_id": 91,
                "chat": {"id": 555, "type": "private"},
                "from": {"id": 77, "username": "anna"},
            }}}

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor()

    reactions = []
    monkeypatch.setattr(multi.core, "_db", lambda: Conn())
    monkeypatch.setattr(multi, "_access_identity", lambda *_args: {"allowed": True})
    monkeypatch.setattr(
        multi,
        "_react",
        lambda token, chat_id, message_id, emoji: reactions.append(
            (token, chat_id, message_id, emoji)
        ),
    )

    changed = multi._finalize_update_reaction(
        AGENT,
        {"update_id": 311, "agent_slug": "prodazhi-bot", "chat_id": "555"},
    )

    assert changed is True
    assert reactions == [("111:AAA", 555, 91, "👍")]


def test_final_reaction_never_marks_a_denied_sender_as_answered(multi, monkeypatch):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, *_args): return None
        def fetchone(self):
            return {"payload": {"message": {
                "message_id": 91,
                "chat": {"id": 555, "type": "private"},
                "from": {"id": 88, "username": "stranger"},
            }}}

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor()

    reactions = []
    monkeypatch.setattr(multi.core, "_db", lambda: Conn())
    monkeypatch.setattr(multi, "_access_identity", lambda *_args: {"allowed": False})
    monkeypatch.setattr(multi, "_react", lambda *_args: reactions.append(True))

    changed = multi._finalize_update_reaction(
        AGENT,
        {"update_id": 311, "agent_slug": "prodazhi-bot", "chat_id": "555"},
    )

    assert changed is False
    assert reactions == []


def test_final_reaction_stays_eyes_for_incomplete_or_failed_parts(multi, monkeypatch):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, *_args): return None
        def fetchone(self): return None

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor()

    reactions = []
    monkeypatch.setattr(multi.core, "_db", lambda: Conn())
    monkeypatch.setattr(multi, "_react", lambda *_args: reactions.append(True))

    changed = multi._finalize_update_reaction(
        AGENT,
        {"update_id": 311, "agent_slug": "prodazhi-bot", "chat_id": "555"},
    )

    assert changed is False
    assert reactions == []


def test_manual_outbox_without_update_never_changes_reaction(multi, monkeypatch):
    monkeypatch.setattr(
        multi.core,
        "_db",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be touched")),
    )

    assert multi._finalize_update_reaction(
        AGENT,
        {"update_id": None, "agent_slug": "prodazhi-bot", "chat_id": "555"},
    ) is False


def test_ambiguous_brain_turn_is_never_replayed_automatically(multi, monkeypatch):
    update = {
        "id": 32,
        "agent_slug": "prodazhi-bot",
        "provider_update_id": 701,
        "payload": {"message": {
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 77, "username": "anna"},
            "text": "измени задачу",
        }},
    }
    finished = []
    monkeypatch.setattr(multi, "_agent_for_slug", lambda _slug: AGENT)
    monkeypatch.setattr(multi, "_access_identity", lambda *_args: {
        "allowed": True, "reason": "allowed", "bitrix_user_id": 17,
    })
    monkeypatch.setattr(multi, "_run_agent_turn", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("connection lost after tool call")
    ))
    monkeypatch.setattr(multi, "_finish_update", lambda row, **kw: finished.append(kw))
    monkeypatch.setattr(multi.core, "journal", lambda *_args, **_kwargs: None)

    multi._process_update(update)

    assert len(finished) == 1
    assert finished[0]["review"] is True
    assert "connection lost" in finished[0]["error"]


def test_photo_is_read_by_media_provider_then_sent_to_same_agent_profile(multi, monkeypatch):
    import attachments
    import shared.media_ingestion as media

    update = {
        "id": 33,
        "agent_slug": "prodazhi-bot",
        "provider_update_id": 702,
        "payload": {"message": {
            "message_id": 10,
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 77, "username": "anna"},
            "caption": "что на скрине?",
            "photo": [{"file_id": "small"}, {"file_id": "large", "file_size": 100}],
        }},
    }
    seen = []
    finished = []
    monkeypatch.setattr(multi, "_agent_for_slug", lambda _slug: AGENT)
    monkeypatch.setattr(multi, "_access_identity", lambda *_args: {
        "allowed": True, "reason": "allowed", "bitrix_user_id": 17,
    })
    monkeypatch.setattr(multi, "_telegram_file_bytes", lambda token, file_id, size: b"JPEG")
    monkeypatch.setattr(media, "recognize_image", lambda data, name: "На скрине задача №42")
    monkeypatch.setattr(attachments, "store_attachment", lambda **_kwargs: "att-42")
    monkeypatch.setattr(multi, "_run_agent_turn", lambda agent, chat, sender, text, identity, **_kw: (
        seen.append((agent["slug"], text, identity["bitrix_user_id"])) or "ответ"
    ))
    monkeypatch.setattr(multi, "_finish_update", lambda row, **kw: finished.append(kw))
    monkeypatch.setattr(multi.core, "journal", lambda *_args, **_kwargs: None)

    multi._process_update(update)

    assert seen[0][0] == "prodazhi-bot"
    assert "что на скрине?" in seen[0][1]
    assert "На скрине задача №42" in seen[0][1]
    assert "attachment_id=att-42" in seen[0][1]
    assert seen[0][2] == 17
    assert finished == [{"chat_id": 555, "answer": "ответ"}]


def test_stranger_media_is_not_downloaded_or_sent_to_groq(multi, monkeypatch):
    update = {
        "id": 34,
        "agent_slug": "prodazhi-bot",
        "provider_update_id": 703,
        "payload": {"message": {
            "chat": {"id": 777, "type": "private"},
            "from": {"id": 88, "username": "stranger"},
            "voice": {"file_id": "secret", "file_size": 100},
        }},
    }
    downloads = []
    finished = []
    monkeypatch.setattr(multi, "_agent_for_slug", lambda _slug: AGENT)
    monkeypatch.setattr(multi, "_access_identity", lambda *_args: {
        "allowed": False, "reason": "user_not_allowed", "bitrix_user_id": None,
    })
    monkeypatch.setattr(multi, "_telegram_file_bytes", lambda *_args: downloads.append(True) or b"VOICE")
    monkeypatch.setattr(multi, "_finish_update", lambda row, **kw: finished.append(kw))
    monkeypatch.setattr(multi.core, "journal", lambda *_args, **_kwargs: None)

    multi._process_update(update)

    assert downloads == []
    assert len(finished) == 1
    assert "нет доступа" in finished[0]["answer"]


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


def test_telegram_can_only_attach_to_an_existing_logical_profile():
    import app  # noqa: F401
    import agent_center
    import inspect

    create_source = inspect.getsource(agent_center.agent_center_create_agent)
    attach_source = inspect.getsource(agent_center.agent_center_agent_attach_telegram)

    assert "create_bot_via_botfather" not in create_source
    assert "telegram-bridge" in create_source
    assert "create_bot_via_botfather" in attach_source


# --- удаление агента ------------------------------------------------------------------------
# Владелец удалил агента в кабинете, а бот в Telegram продолжал отвечать (22.07.2026): поток
# опроса жил своей жизнью, потому что супервизор только ПОДНИМАЛ потоки и никогда их не гасил.

def test_deleted_agent_stops_answering(multi, monkeypatch):
    """Самое неприятное для владельца: удалил — а бот продолжает говорить от имени компании."""
    import tg_agent
    monkeypatch.setattr(multi, "_is_wanted", lambda slug, token=None: False)
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


def test_token_change_restarts_the_polling_thread(multi, monkeypatch):
    """Владелец отозвал токен в BotFather и выпустил новый: поток со старым токеном обязан
    завершиться. Иначе он вечно ловит от Telegram 401 — бот молчит, а в кабинете «работает»."""
    import tg_agent

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **kw): pass
        def fetchone(self): return {"telegram_bot_token": "НОВЫЙ:token"}

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return Cur()

    monkeypatch.setattr(tg_agent, "_db", lambda: Conn())

    assert multi._is_wanted("prodazhi-bot", "СТАРЫЙ:token") is False
    assert multi._is_wanted("prodazhi-bot", "НОВЫЙ:token") is True
