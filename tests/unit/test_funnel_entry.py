"""Первые этапы воронки ведёт агент (владелец, 24.07.2026).

Спросил про ИУ → сразу сделка на «Новый лид» с его @username; ответили → «Связались»;
заполнил анкету → «Анкета заполнена», а дубль от CRM-формы склеивается с исходной сделкой.
Поставщики и болтовня в воронку не попадают.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def tg(monkeypatch, tmp_path):
    import tg_agent

    state = tmp_path / "s.json"
    state.write_text(json.dumps({"business": {"C1": {"user_id": 871}}}), encoding="utf-8")
    monkeypatch.setattr(tg_agent, "STATE_PATH", state)
    monkeypatch.setattr(tg_agent, "load_state",
                        lambda: json.loads(state.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg_agent, "save_state",
                        lambda s: state.write_text(json.dumps(s, ensure_ascii=False),
                                                   encoding="utf-8"))
    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    monkeypatch.setenv("TG_LEAD_INVITE", "1")
    tg_agent._LEADS_CACHE.update({"at": 0.0, "map": {}, "ok": True})
    monkeypatch.setattr(tg_agent, "crm_lead_usernames", lambda force=False: {})
    monkeypatch.setattr(tg_agent, "crm_leads_reachable", lambda: True)
    monkeypatch.setattr(tg_agent, "journal", lambda *a, **k: None)
    return tg_agent


def test_iu_intent_recognised_but_smalltalk_ignored(tg):
    assert tg._iu_intent(["Здравствуйте какие условия подключения к иу?"])
    assert tg._iu_intent(["хочу подключиться"])
    assert tg._iu_intent(["сколько стоит?"])
    assert not tg._iu_intent(["Привет"])
    assert not tg._iu_intent(["Отгрузили паллеты вчера, накладную пришлю"])


def _wire(tg, monkeypatch, answer="Здравствуйте! Чем помочь?"):
    calls, sent = [], []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: answer)
    monkeypatch.setattr(tg, "send_as_account",
                        lambda uid, t, parse_mode="": sent.append(t) or (True, ""))

    def fake_mcp(tool, args):
        calls.append((tool, args))
        return {"deal_id": 500} if tool == "create_crm_deal" else {}

    monkeypatch.setattr(tg, "mcp_call", fake_mcp)
    return calls, sent


def _msg(text, username="novichok", uid=777):
    return {"business_connection_id": "C1", "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "username": username, "first_name": "Иван"}, "text": text}


def test_asking_about_iu_opens_a_deal_then_moves_to_contacted(tg, monkeypatch):
    calls, sent = _wire(tg, monkeypatch)

    tg.maybe_autoreply(_msg("Здравствуйте, какие условия подключения к ИУ?"))

    created = [a for t, a in calls if t == "create_crm_deal"]
    assert created, "сделка должна завестись сразу"
    assert created[0]["stage"] == tg.STAGE_NEW
    assert created[0]["custom_fields"][tg.CRM_TELEGRAM_FIELD] == "novichok"
    moved = [a for t, a in calls if t == "update_crm_deal"]
    assert moved and moved[0]["stage"] == tg.STAGE_CONTACTED, "ответили — значит «Связались»"
    assert sent, "клиент всё равно получает ответ"


def test_supplier_smalltalk_does_not_open_a_deal(tg, monkeypatch):
    calls, _ = _wire(tg, monkeypatch)

    tg.maybe_autoreply(_msg("Привет, накладную завтра пришлю", username="postavshik"))

    assert [a for t, a in calls if t == "create_crm_deal"] == [], \
        "поставщикам и болтовне в воронке не место"


def test_undelivered_reply_does_not_move_the_stage(tg, monkeypatch):
    calls, _ = _wire(tg, monkeypatch)
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (False, "чат закрыт"))

    tg.maybe_autoreply(_msg("хочу подключиться к ИУ"))

    assert [a for t, a in calls if t == "create_crm_deal"], "сделка заводится всё равно"
    assert [a for t, a in calls if t == "update_crm_deal"] == [], \
        "не доставили — не «Связались»"


def test_form_duplicate_is_merged_into_the_original_deal(tg, monkeypatch):
    """Анкета создаёт свою сделку — её данные переносим в исходную, дубль удаляем."""
    calls = []

    fields = {500: {tg.CRM_TELEGRAM_FIELD: "novichok"},
              520: {tg.CRM_TELEGRAM_FIELD: "novichok", "UF_CRM_1784297137": "Одежда"}}

    def fake_mcp(tool, args):
        calls.append((tool, args))
        if tool == "list_crm_lead_contacts":
            # Реальный формат: отдельная строка на КАЖДУЮ сделку с этим username.
            return {"contacts": [{"username": "novichok", "deal_id": 500},
                                 {"username": "novichok", "deal_id": 520}]}
        if tool == "get_crm_deal":
            return {"deal": {"custom_fields": fields[args["deal_id"]]}}
        return {}

    monkeypatch.setattr(tg, "mcp_call", fake_mcp)

    res = tg.merge_form_duplicate("novichok")

    assert res["merged"] and res["kept"] == 500 and res["deleted"] == 520
    upd = [a for t, a in calls if t == "update_crm_deal"][0]
    assert upd["deal_id"] == 500 and upd["stage"] == tg.STAGE_FORM_DONE
    assert upd["custom_fields"]["UF_CRM_1784297137"] == "Одежда", "данные анкеты перенесены"
    assert [a for t, a in calls if t == "delete_crm_deal"][0]["deal_id"] == 520


def test_nothing_to_merge_when_there_is_one_deal(tg, monkeypatch):
    def fake_mcp(tool, args):
        if tool == "list_crm_lead_contacts":
            return {"contacts": [{"username": "novichok", "deal_id": 500}]}
        if tool == "get_crm_deal":
            return {"deal": {"custom_fields": {tg.CRM_TELEGRAM_FIELD: "novichok"}}}
        return {}

    monkeypatch.setattr(tg, "mcp_call", fake_mcp)

    assert tg.merge_form_duplicate("novichok")["merged"] is False
