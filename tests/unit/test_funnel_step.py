"""Шаг воронки считается по фактам сделки, а не по памяти агента.

Владелец 23.07.2026: клиент спросил «а что такое ЭДО?» между вопросом и ответом. Агент объяснил,
клиент сказал «давайте ЭДО» — а задачу на отправку договора агент так и не поставил: вопрос
вклинился, и привязанное к ответу действие потерялось. Теперь шаг приходит в промпте КАЖДОГО
сообщения, поэтому любое число вопросов по дороге ничего не ломает.
"""
from __future__ import annotations

from tg_agent import (CONTRACT_NUMBER_FIELD, CONTRACT_REQUISITES_FIELD, SIGNING_FIELD,
                      funnel_next_step)


def _deal(stage, **uf):
    return {"id": 86, "stage": stage, "custom_fields": uf}


def test_new_deal_leads_to_collecting_requisites():
    st = funnel_next_step(_deal("C16:CONTACTED"))

    assert "C16:S84294149" in st["action"]
    assert "реквизиты" in st["action"].lower()


def test_requisites_missing_is_the_step():
    st = funnel_next_step(_deal("C16:S84294149"))

    assert "реквизиты" in st["need"].lower()
    assert "send_contract" in st["action"], "как придут — сразу собрать договор"


def test_requisites_present_means_send_the_contract():
    st = funnel_next_step(_deal("C16:S84294149", **{CONTRACT_REQUISITES_FIELD: "ИНН 7704123456"}))

    assert st["step"] == "Отправка договора"
    assert "send_contract(deal_id=86" in st["action"]


def test_signing_method_is_the_pending_step_until_it_is_recorded():
    """Ровно тот шаг, который агент потерял из-за вопроса про ЭДО."""
    st = funnel_next_step(_deal("C16:NDA", **{CONTRACT_REQUISITES_FIELD: "ИНН",
                                              CONTRACT_NUMBER_FIELD: "23.07.2026"}))

    assert st["step"] == "Выбор способа подписания"
    assert "create_bitrix_task" in st["action"], "за ответом обязана идти задача"
    assert "notify_client_when_task_done" in st["action"], "и уведомление клиенту"
    assert "вопросы" in st["action"], "агент предупреждён, что вопросы по дороге не отменяют шаг"


def test_unset_enumeration_field_is_not_a_choice():
    """Незаполненный список Битрикса приходит нулём — «0» это НЕ выбранный способ подписания."""
    st = funnel_next_step(_deal("C16:NDA", **{CONTRACT_REQUISITES_FIELD: "ИНН",
                                              CONTRACT_NUMBER_FIELD: "23.07.2026",
                                              SIGNING_FIELD: "0"}))

    assert st["step"] == "Выбор способа подписания", "иначе шаг считался бы пройденным"


def test_deal_id_is_read_from_any_of_the_crm_shapes():
    """list_crm_deals отдаёт deal_id, get_crm_deal — id: шаг не должен зависеть от формы ответа."""
    for key in ("deal_id", "id", "ID"):
        st = funnel_next_step({key: 86, "stage_id": "C16:S84294149",
                               "custom_fields": {CONTRACT_REQUISITES_FIELD: "ИНН"}})
        assert "deal_id=86" in st["action"], key


def test_after_the_method_is_chosen_the_task_must_exist(monkeypatch):
    monkeypatch.setattr("mcp.context_server._crm_enum_items",
                        lambda: {SIGNING_FIELD: {"эдо": "84", "бумага": "86"}})
    st = funnel_next_step(_deal("C16:NDA", **{CONTRACT_REQUISITES_FIELD: "ИНН",
                                              CONTRACT_NUMBER_FIELD: "23.07.2026",
                                              SIGNING_FIELD: "84"}))

    assert st["step"] == "Договор на подписании"
    assert "не поставлена" in st["action"], "страховка на случай, если задачу всё же забыли"
    assert "(ЭДО)" in st["action"], "агент не должен говорить клиенту «способ подписания 84»"
    assert "84" not in st["action"]


def test_payment_is_confirmed_only_by_the_accountant():
    st = funnel_next_step(_deal("C16:PREPAYMENT_INVOIC"))

    assert "не деньги на счету" in st["action"]
    assert "бухгалтер" in st["need"].lower()


def test_unknown_stage_does_not_invent_actions():
    st = funnel_next_step(_deal("C16:SOMETHING_NEW"))

    assert "не двигай без факта" in st["action"]


def test_step_block_tells_the_agent_to_come_back_after_questions(monkeypatch):
    """Главная строка защиты: вопросы клиента не отменяют текущий шаг."""
    import tg_agent

    monkeypatch.setattr(
        tg_agent, "funnel_next_step",
        lambda deal: {"step": "Выбор способа подписания", "need": "ЭДО или бумага",
                      "action": "поставь задачу"})
    monkeypatch.setitem(
        __import__("mcp.context_server", fromlist=["TOOLS"]).TOOLS, "get_crm_deal",
        {"handler": lambda a: {"deal": _deal("C16:NDA")}})

    block = tg_agent.funnel_step_block(86)

    assert "ТЕКУЩИЙ ШАГ ВОРОНКИ" in block
    assert "важнее твоей памяти" in block
    assert "возвращайся к этому шагу" in block


def test_step_block_survives_crm_failure(monkeypatch):
    """Недоступная CRM не должна оставить клиента без ответа вообще."""
    import tg_agent

    monkeypatch.setitem(
        __import__("mcp.context_server", fromlist=["TOOLS"]).TOOLS, "get_crm_deal",
        {"handler": lambda a: (_ for _ in ()).throw(RuntimeError("CRM недоступна"))})

    assert tg_agent.funnel_step_block(86) == ""
