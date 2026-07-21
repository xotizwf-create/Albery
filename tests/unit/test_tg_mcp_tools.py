"""Агент сам пишет лидам в Telegram от лица аккаунта компании.

Задача: лиды воронки «Партнёрская программа WB — индивидуальные условия» обрабатываются без
участия владельца. Telegram не даёт боту искать людей по @username, поэтому контакт попадает
в справочник САМ — из входящего сообщения на аккаунт (там есть и id, и username).
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
    monkeypatch.setattr(tg_agent, "BUSINESS_LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(tg_agent, "load_state", lambda: json.loads(state_file.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg_agent, "save_state",
                        lambda s: state_file.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8"))
    return tg_agent


def test_incoming_message_registers_the_contact_automatically(tg):
    """Ядро автоматизации: лид написал на аккаунт — агент уже может ему отвечать."""
    tg.handle_business_message({
        "business_connection_id": "C1",
        "chat": {"id": 555, "first_name": "Дмитрий"},
        "from": {"id": 555, "username": "griaznov_d", "first_name": "Дмитрий"},
        "text": "Здравствуйте, интересуют условия",
    })

    entry = tg.find_contact("@griaznov_d")
    assert entry and entry["id"] == 555


def test_bots_are_not_added_to_the_contact_book(tg):
    tg.handle_business_message({
        "chat": {"id": 1}, "from": {"id": 1, "is_bot": True, "username": "somebot"}, "text": "x"})

    assert tg.find_contact("@somebot") is None


def test_agent_sends_from_the_company_account(tg, monkeypatch):
    tg.save_state({"business": {"CONN-1": {}},
                   "contacts": {"griaznov_d": {"id": 555, "username": "griaznov_d", "name": "Дмитрий"}}})
    calls = {}
    monkeypatch.setattr(tg, "api", lambda method, **p: calls.update(method=method, **p) or {"ok": True})

    res = tg.telegram_send_as_account("@griaznov_d", "Добрый день! Готовы обсудить условия.")

    assert res["sent"] and res["to_id"] == 555
    assert calls["business_connection_id"] == "CONN-1", "иначе напишет бот, а не аккаунт"
    assert calls["chat_id"] == 555


def test_unknown_username_fails_loudly_with_a_way_out(tg):
    """Агент не должен выдумывать id — он обязан объяснить, что делать."""
    tg.save_state({"business": {"CONN-1": {}}})

    with pytest.raises(ValueError) as e:
        tg.telegram_send_as_account("@nikto", "привет")

    assert "справочник" in str(e.value) and "t.me/AlberyAIManager" in str(e.value)


def test_numeric_id_works_without_the_directory(tg, monkeypatch):
    tg.save_state({"business": {"CONN-1": {}}})
    monkeypatch.setattr(tg, "api", lambda method, **p: {"ok": True})

    assert tg.telegram_send_as_account("555", "привет")["to_id"] == 555


def test_empty_text_is_rejected(tg):
    with pytest.raises(ValueError):
        tg.telegram_send_as_account("555", "   ")


def test_contacts_list_is_deduplicated(tg):
    """Один человек хранится и по username, и по id — в списке он должен быть один раз."""
    tg.save_state({"contacts": {
        "griaznov_d": {"id": 555, "username": "griaznov_d", "name": "Дмитрий"},
        "555": {"id": 555, "username": "griaznov_d", "name": "Дмитрий"},
        "888": {"id": 888, "username": "", "name": "Без юзернейма"},
    }})

    out = tg.telegram_contacts_list()

    assert out["total"] == 2
    assert {c["id"] for c in out["contacts"]} == {555, 888}


def test_mcp_tools_are_registered_and_explain_the_limitation(ctx):
    send = ctx.TOOLS["send_telegram_message"]
    assert callable(send["handler"])
    assert "@AlberyAIManager" in send["description"]
    assert "не позволяет боту искать" in send["description"], "агент должен знать про ограничение"
    assert "не выдумывай id" in send["description"]
    assert set(send["inputSchema"]["required"]) == {"to", "text"}
    assert callable(ctx.TOOLS["list_telegram_contacts"]["handler"])


def test_telegram_tools_are_not_public(ctx):
    """Письмо от личного аккаунта компании — не для публичных коннекторов."""
    assert "send_telegram_message" in ctx.OWNER_ONLY_TOOL_NAMES
    assert "list_telegram_contacts" in ctx.OWNER_ONLY_TOOL_NAMES


def test_tool_reports_unknown_contact_as_a_clear_error(ctx, monkeypatch):
    with pytest.raises(ctx.McpError):
        ctx.tool_send_telegram_message({"to": "", "text": "привет"})
