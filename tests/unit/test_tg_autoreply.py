"""Агент сам отвечает лидам в личке аккаунта компании.

Схема, подтверждённая на живом человеке 22.07.2026: лид пишет на @AlberyAIManager →
бизнес-подключение ловит сообщение → контакт записывается сам → агент отвечает ОТ ЛИЦА
аккаунта. Так закрывается воронка «Партнёрская программа WB» без участия менеджера.

Главная опасность — зацикливание: исходящие самого аккаунта приходят тем же апдейтом, и без
фильтра агент отвечал бы сам себе бесконечно.
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
    return tg_agent


def _incoming(uid=1451982360, text="Здравствуйте, интересуют условия", **kw):
    msg = {"business_connection_id": "C1", "chat": {"id": uid, "type": "private"},
           "from": {"id": uid, "username": "lead", "first_name": "Дмитрий"}, "text": text}
    msg.update(kw)
    return msg


def test_lead_gets_an_answer_from_the_company_account(tg, monkeypatch):
    sent = {}
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: "Здравствуйте! Расскажите про ваш оборот.")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t: (sent.update(uid=uid, text=t), (True, ""))[1])

    tg.maybe_autoreply(_incoming())

    assert sent["uid"] == 1451982360
    assert "оборот" in sent["text"]


def test_own_outgoing_messages_never_trigger_a_reply(tg, monkeypatch):
    """Самое опасное: иначе агент отвечает сам себе по кругу."""
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: calls.append(1) or "ответ")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t: (True, ""))

    tg.maybe_autoreply(_incoming(uid=8715335144))  # id самого владельца аккаунта

    assert calls == [], "на своё же исходящее отвечать нельзя"


def test_bots_are_ignored(tg, monkeypatch):
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: calls.append(1) or "ответ")

    msg = _incoming()
    msg["from"]["is_bot"] = True
    tg.maybe_autoreply(msg)

    assert calls == []


def test_group_chats_are_ignored(tg, monkeypatch):
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: calls.append(1) or "ответ")

    tg.maybe_autoreply(_incoming(chat={"id": -100, "type": "group"}))

    assert calls == []


def test_empty_message_is_ignored(tg, monkeypatch):
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: calls.append(1) or "ответ")

    tg.maybe_autoreply(_incoming(text="   "))

    assert calls == []


def test_autoreply_is_off_by_default(tg, monkeypatch):
    monkeypatch.delenv("TG_BUSINESS_AUTOREPLY", raising=False)
    assert tg.business_autoreply_enabled() is False

    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    assert tg.business_autoreply_enabled() is True


def test_incoming_still_registers_contact_when_autoreply_off(tg, monkeypatch):
    """Справочник должен пополняться независимо от автоответа."""
    monkeypatch.delenv("TG_BUSINESS_AUTOREPLY", raising=False)

    tg.handle_business_message(_incoming(uid=777))

    assert tg.find_contact("777")["id"] == 777


def test_brain_failure_does_not_crash_the_pipeline(tg, monkeypatch):
    def boom(p, s):
        raise RuntimeError("мозг недоступен")

    monkeypatch.setattr(tg, "hermes_answer", boom)
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t: (True, ""))

    tg.maybe_autoreply(_incoming())  # не должно бросить исключение


def test_answer_is_sent_as_plain_text(tg, monkeypatch):
    """В мессенджере разметка выглядит мусором."""
    sent = {}
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: "**Жирный** текст")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t: (sent.update(text=t), (True, ""))[1])

    tg.maybe_autoreply(_incoming())

    assert "**" not in sent["text"]
