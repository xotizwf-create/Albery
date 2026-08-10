"""Агент отвечает в личке ТОЛЬКО лидам из воронки.

Требование владельца 22.07.2026: username пишущего сверяется с полем Telegram в сделках
воронки «Партнёрская программа WB — индивидуальные условия». Есть в воронке — отвечаем,
нет — молчим. Аккаунт @AlberyAIManager живой: туда пишут поставщики и знакомые, и агент не
должен встревать в эти переписки.
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
    tg_agent._LEADS_CACHE.update({"at": 0.0, "map": {}, "ok": True})
    return tg_agent


def _msg(username="griaznov.d", uid=555, text="Здравствуйте, интересуют условия"):
    return {"business_connection_id": "C1", "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "username": username, "first_name": "Дмитрий"}, "text": text}




def test_stranger_never_gets_a_generated_reply(tg, monkeypatch):
    """С не-лидом агент диалог не ведёт: ему полагается только анкета (test_tg_lead_invite)."""
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (True, ""))

    tg.maybe_autoreply(_msg(username="postavshik_ivan", uid=999))

    assert calls == [], "не лид — переписку с ним агент не ведёт"


def test_user_without_username_gets_no_generated_reply(tg, monkeypatch):
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (True, ""))

    tg.maybe_autoreply(_msg(username="", uid=1234))

    assert calls == []


def test_crm_unavailable_means_total_silence(tg, monkeypatch):
    """Если воронку не прочитать, лида не отличить от незнакомца — не пишем НИЧЕГО.

    Иначе при сбое Bitrix живой лид получил бы приглашение заполнить анкету, которую он уже
    заполнил."""
    monkeypatch.setenv("TG_LEAD_INVITE", "1")
    tg._LEADS_CACHE.update({"at": 0.0, "map": {}, "ok": False})
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {})
    calls, sent = [], []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")
    monkeypatch.setattr(tg, "send_as_account",
                        lambda uid, t, parse_mode="": sent.append(t) or (True, ""))

    tg.maybe_autoreply(_msg())

    assert calls == [] and sent == []




def test_username_matching_tolerates_dots_and_case(tg, monkeypatch):
    """В анкете пишут «Griaznov.D», а в Telegram username без точек — это один человек."""
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})

    assert tg.lead_deal_for_username("@Griaznov_D") == 82
    assert tg.lead_deal_for_username("griaznovd") == 82
    assert tg.lead_deal_for_username("@griaznov.d") == 82


def test_different_person_is_not_matched_by_accident(tg, monkeypatch):
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})

    assert tg.lead_deal_for_username("@ivanov") is None
    assert tg.lead_deal_for_username("griaznov_ivan") is None


def test_username_normalisation(tg):
    assert tg._norm_username("@Test_User") == "test_user"
    assert tg._norm_username("https://t.me/lead_one") == "lead_one"
    assert tg._norm_username("  ") == ""
    assert tg._norm_username("не username с пробелами") == ""


def test_funnel_usernames_are_read_through_allowlisted_internal_dispatch(tg, monkeypatch):
    """The standalone service reuses the CRM handler without a network MCP credential."""
    import internal_tools

    captured = {}

    def fake_call(name, arguments, *, allowed):
        captured.update(name=name, arguments=arguments, allowed=allowed)
        return {"contacts": [
            {"username": "griaznov.d", "deal_id": 82},
            {"username": "", "deal_id": 80},
        ]}

    monkeypatch.setattr(internal_tools, "call_internal_tool", fake_call)

    out = tg.crm_lead_usernames(force=True)

    assert out == {"griaznov.d": 82}, "пустые значения не попадают в список"
    assert captured["name"] == "list_crm_lead_contacts"
    assert captured["arguments"] == {}
    assert captured["allowed"] == internal_tools.TELEGRAM_RUNTIME_TOOLS


def test_internal_result_is_understood(tg, monkeypatch):
    import internal_tools

    monkeypatch.setattr(
        internal_tools,
        "call_internal_tool",
        lambda *args, **kwargs: {"contacts": [{"username": "lead_one", "deal_id": 5}]},
    )

    assert tg.crm_lead_usernames(force=True) == {"lead_one": 5}


def test_unallowlisted_internal_tool_is_rejected():
    import internal_tools

    with pytest.raises(internal_tools.InternalToolError, match="not allowed"):
        internal_tools.call_internal_tool("delete_bitrix_task", {}, allowed=internal_tools.TELEGRAM_RUNTIME_TOOLS)


def test_crm_failure_falls_back_to_cached_list(tg, monkeypatch):
    """Разрыв связи не должен мгновенно затыкать агента для уже известных лидов."""
    tg._LEADS_CACHE.update({"at": 9e9, "map": {"griaznov.d": 82}})

    def boom(*a, **kw):
        raise RuntimeError("сеть недоступна")

    import internal_tools
    monkeypatch.setattr(internal_tools, "call_internal_tool", boom)

    assert tg.crm_lead_usernames(force=True) == {"griaznov.d": 82}


def test_mcp_tool_is_registered(ctx):
    spec = ctx.TOOLS["list_crm_lead_contacts"]
    assert callable(spec["handler"])
    assert "воронки" in spec["description"]
