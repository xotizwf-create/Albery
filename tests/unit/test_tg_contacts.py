"""Справочник контактов Telegram: как получить числовой id и написать от лица аккаунта.

Bot API не умеет находить человека по @username: sendMessage принимает только числовой id,
а getChat на чужой username отвечает «chat not found» — проверено на проде 21.07.2026, это
ограничение платформы, а не прав. Штатный обходной путь — кнопка выбора контакта
(KeyboardButtonRequestUsers): владелец тыкает человека, Telegram сам присылает его user_id.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def tg(monkeypatch, tmp_path):
    import tg_agent

    state_file = tmp_path / "state.json"
    state_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tg_agent, "STATE_PATH", state_file)
    monkeypatch.setattr(tg_agent, "load_state", lambda: json.loads(state_file.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg_agent, "save_state",
                        lambda s: state_file.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8"))
    return tg_agent


def test_contact_is_remembered_by_username_and_id(tg):
    tg.remember_contact({"user_id": 777, "username": "alexxandrn", "first_name": "Александр"})

    by_name = tg.find_contact("@alexxandrn")
    by_id = tg.find_contact("777")

    assert by_name["id"] == 777 and by_id["id"] == 777
    assert by_name["username"] == "alexxandrn"


def test_lookup_is_case_insensitive_and_tolerates_missing_at(tg):
    tg.remember_contact({"user_id": 42, "username": "GriaznovD", "first_name": "Дмитрий"})

    assert tg.find_contact("griaznovd")["id"] == 42
    assert tg.find_contact("@GRIAZNOVD")["id"] == 42


def test_unknown_contact_returns_nothing(tg):
    assert tg.find_contact("@ktoto") is None
    assert tg.find_contact("") is None


def test_contact_without_username_is_still_usable_by_id(tg):
    """У людей без @username есть только числовой id — их тоже надо помнить."""
    tg.remember_contact({"user_id": 555, "first_name": "Без", "last_name": "Юзернейма"})

    entry = tg.find_contact("555")
    assert entry["id"] == 555 and entry["name"] == "Без Юзернейма"


def test_users_shared_saves_the_real_id(tg, monkeypatch):
    """Точный сценарий: владелец выбрал человека кнопкой — Telegram прислал его id."""
    replies = []
    monkeypatch.setattr(tg, "send_text", lambda cid, t: replies.append(t))

    tg.handle_users_shared(100, {"request_id": 1, "users": [
        {"user_id": 8899, "username": "alexxandrn", "first_name": "Александр"}]})

    assert tg.find_contact("@alexxandrn")["id"] == 8899
    assert "8899" in replies[0] and "alexxandrn" in replies[0]


def test_users_shared_handles_bare_ids(tg, monkeypatch):
    """Старый формат апдейта отдаёт только числа — данные терять нельзя."""
    monkeypatch.setattr(tg, "send_text", lambda cid, t: None)

    tg.handle_users_shared(100, {"user_ids": [4242]})

    assert tg.find_contact("4242")["id"] == 4242


def test_send_as_account_uses_the_business_connection(tg, monkeypatch):
    """Сообщение должно уходить ОТ ЛИЦА аккаунта, а не от бота."""
    calls = {}
    tg.save_state({"business": {"CONN-1": {"user_id": 1, "enabled": True}}})
    monkeypatch.setattr(tg, "api", lambda method, **p: calls.update(method=method, **p) or {"ok": True})

    ok, err = tg.send_as_account(8899, "привет")

    assert ok and not err
    assert calls["method"] == "sendMessage"
    assert calls["business_connection_id"] == "CONN-1", "без этого напишет бот, а не аккаунт"
    assert calls["chat_id"] == 8899


def test_send_as_account_without_business_connection_explains_why(tg):
    tg.save_state({})

    ok, err = tg.send_as_account(8899, "привет")

    assert not ok and "бизнес-подключение" in err


def test_send_as_account_reports_telegram_refusal(tg, monkeypatch):
    tg.save_state({"business": {"CONN-1": {}}})

    def boom(method, **p):
        raise RuntimeError("Bad Request: chat not found")

    monkeypatch.setattr(tg, "api", boom)

    ok, err = tg.send_as_account(1, "привет")
    assert not ok and "chat not found" in err


def test_forwarded_message_gives_the_real_id(tg, monkeypatch):
    """Так и работают публичные «боты для получения id»: автор пересланного сообщения."""
    replies = []
    monkeypatch.setattr(tg, "send_text", lambda cid, t: replies.append(t))

    handled = tg.handle_forward(100, {"forward_origin": {
        "type": "user",
        "sender_user": {"id": 9911, "username": "alexxandrn", "first_name": "Александр"}}})

    assert handled
    assert tg.find_contact("@alexxandrn")["id"] == 9911
    assert "9911" in replies[0]


def test_old_style_forward_field_also_works(tg, monkeypatch):
    monkeypatch.setattr(tg, "send_text", lambda cid, t: None)

    assert tg.handle_forward(100, {"forward_from": {"id": 7, "username": "someone"}})
    assert tg.find_contact("@someone")["id"] == 7


def test_hidden_forward_is_explained_not_silently_ignored(tg, monkeypatch):
    """Если человек закрыл пересылку — id не придёт, и надо сказать почему."""
    replies = []
    monkeypatch.setattr(tg, "send_text", lambda cid, t: replies.append(t))

    handled = tg.handle_forward(100, {"forward_origin": {
        "type": "hidden_user", "sender_user_name": "Александр"}})

    assert handled
    assert "приватности" in replies[0] and "/id" in replies[0]


def test_ordinary_message_is_not_treated_as_forward(tg):
    assert tg.handle_forward(100, {"text": "привет"}) is False


def test_keyboard_asks_telegram_for_username_and_name(tg):
    kb = tg._request_contact_keyboard()
    req = kb["keyboard"][0][0]["request_users"]

    assert req["request_username"] and req["request_name"], "иначе вернётся голый id без имени"
    assert req["user_is_bot"] is False
