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
    tg_agent._LEADS_CACHE.update({"at": 0.0, "map": {}})
    return tg_agent


def _msg(username="griaznov.d", uid=555, text="Здравствуйте, интересуют условия"):
    return {"business_connection_id": "C1", "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "username": username, "first_name": "Дмитрий"}, "text": text}


def test_lead_from_the_funnel_gets_an_answer(tg, monkeypatch):
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})
    sent = {}
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: "Здравствуйте! Уточните оборот, пожалуйста.")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t: (sent.update(uid=uid, text=t), (True, ""))[1])

    tg.maybe_autoreply(_msg())

    assert sent["uid"] == 555 and "оборот" in sent["text"]


def test_stranger_is_ignored(tg, monkeypatch):
    """Поставщику или знакомому агент писать не должен."""
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: calls.append(1) or "ответ")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t: (True, ""))

    tg.maybe_autoreply(_msg(username="postavshik_ivan", uid=999))

    assert calls == [], "не лид — агент обязан молчать"


def test_user_without_username_is_ignored(tg, monkeypatch):
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: calls.append(1) or "ответ")

    tg.maybe_autoreply(_msg(username="", uid=1234))

    assert calls == []


def test_crm_unavailable_means_silence_not_reply_to_everyone(tg, monkeypatch):
    """Безопасный отказ: если воронку не прочитать, молчим, а не отвечаем всем подряд."""
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {})
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: calls.append(1) or "ответ")

    tg.maybe_autoreply(_msg())

    assert calls == []


def test_deal_number_is_given_to_the_agent(tg, monkeypatch):
    """Агент должен знать, по какой сделке идёт разговор."""
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})
    prompts = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: prompts.append(p) or "ок")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t: (True, ""))

    tg.maybe_autoreply(_msg())

    assert "№82" in prompts[0]


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


def test_funnel_usernames_are_read_from_crm(tg, monkeypatch):
    """Проверяем сам запрос в Bitrix: фильтр по воронке и нужное поле."""
    captured = {}

    class Resp:
        content = b"{}"
        @staticmethod
        def json():
            return {"result": [{"ID": "82", tg.CRM_TELEGRAM_FIELD: "@Griaznov.D"},
                               {"ID": "80", tg.CRM_TELEGRAM_FIELD: ""}]}

    monkeypatch.setenv("BITRIX_WEBHOOK_BASE", "https://portal/rest/1/token")
    monkeypatch.setattr(tg.requests, "post",
                        lambda url, json=None, timeout=0: captured.update(url=url, body=json) or Resp())

    out = tg.crm_lead_usernames(force=True)

    assert out == {"griaznov.d": 82}, "пустые значения не попадают в список"
    assert captured["body"]["filter"]["CATEGORY_ID"] == tg.CRM_LEAD_CATEGORY_ID
    assert tg.CRM_TELEGRAM_FIELD in captured["body"]["select"]


def test_crm_failure_falls_back_to_cached_list(tg, monkeypatch):
    """Разрыв связи с Bitrix не должен мгновенно затыкать агента для уже известных лидов."""
    tg._LEADS_CACHE.update({"at": 9e9, "map": {"griaznov.d": 82}})

    def boom(*a, **kw):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setenv("BITRIX_WEBHOOK_BASE", "https://portal/rest/1/token")
    monkeypatch.setattr(tg.requests, "post", boom)

    assert tg.crm_lead_usernames(force=True) == {"griaznov.d": 82}
