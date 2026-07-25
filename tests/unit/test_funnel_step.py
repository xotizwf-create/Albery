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


def test_first_contact_leads_to_terms():
    """С 25.07.2026 условия идут ПЕРВЫМ шагом, до анкеты (решение владельца).

    Было наоборот: на первом контакте отправлялась сверка анкеты, а условия — только после
    её подтверждения. Порядок переставлен, потому что во всех живых диалогах первый вопрос
    клиента — про условия."""
    st = funnel_next_step(_deal("C16:CONTACTED"))

    assert st["step"] == "Ответ и условия"
    assert "send_terms" in st["action"]


def test_requisites_are_asked_after_the_form_is_verified(monkeypatch):
    """Реквизиты идут после анкеты: вопросы по условиям разобраны ещё до неё."""
    import tg_agent

    monkeypatch.setattr(tg_agent, "TERMS_SENT_FIELD", "UF_CRM_TERMS")
    st = funnel_next_step(_deal("C16:S84294149", **{"UF_CRM_TERMS": "2026-07-23"}))

    assert st["step"] == "Сбор реквизитов"
    assert "реквизиты" in st["action"].lower()
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
    """Незнакомый этап не даёт агенту выдумывать обещания и самому двигать сделку.

    Формулировка запасного сценария изменена 24.07.2026: раньше он ещё и молчал о том, ЧТО
    делать, из-за чего агент вставал в тупик (см. тест ниже)."""
    st = funnel_next_step(_deal("C16:SOMETHING_NEW"))

    assert "Стадию сам не двигай" in st["action"]
    assert "не выдумывай" in st["action"].lower()


def test_step_block_tells_the_agent_to_come_back_after_questions(monkeypatch):
    """Главная строка защиты: вопросы клиента не отменяют текущий шаг."""
    import tg_agent

    monkeypatch.setattr(
        tg_agent, "funnel_next_step",
        lambda deal, terms_sent_to_client=False: {"step": "Выбор способа подписания",
                                                  "need": "ЭДО или бумага",
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


# --- тупик после анкеты (владелец, 24.07.2026: «агент словил тупик и не знает что сказать») ---

ANKETA_DEAL = {
    "deal_id": 148, "stage_id": "C16:UC_ANKETA",
    "custom_fields": {"UF_CRM_1784297026": "Test", "UF_CRM_1784297137": "Одежда"},
}


def test_anketa_stage_has_a_real_step():
    """Живой случай (Александр, сделка 148): склейка поставила «Анкета заполнена», а этого
    этапа не было ни в одной ветке — шаг сваливался в заглушку «Стадия C16:UC_ANKETA / ждёшь:
    — / веди разговор по маршруту». Агенту было буквально нечего делать, и он завис."""
    st = funnel_next_step(ANKETA_DEAL)

    assert st["step"] != "Стадия C16:UC_ANKETA", "этап без шага = тупик"
    assert st["need"] != "—", "агент обязан знать, чего ждёт от клиента"
    assert "148" in st["action"], "в шаге есть конкретное действие со сделкой"


def test_after_anketa_with_terms_already_sent_the_deal_moves_on_without_resending():
    """С новым порядком условия человек читает ДО анкеты — значит к моменту сверки они у него
    всегда есть. Сверив данные, агент двигает сделку дальше и второй раз документ не шлёт.
    Оставшиеся вопросы разбирает следующий шаг («Сбор реквизитов» начинается именно с них)."""
    st = funnel_next_step(ANKETA_DEAL, terms_sent_to_client=True)

    assert st["step"] == "Сверка анкеты"
    assert "C16:S84294149" in st["action"], "перевести сделку на согласование условий"
    assert "send_terms" not in st["action"], "второй раз условия не отправляем"


def test_after_anketa_without_terms_agent_sends_them():
    """Если условия ещё НЕ отправляли — после подтверждения анкеты их надо отправить."""
    st = funnel_next_step(ANKETA_DEAL)

    assert "send_terms" in st["action"]


def test_unknown_stage_never_leaves_the_agent_without_instructions():
    """Класс сбоя, а не один случай: в воронку добавили этап, а шаг в коде не написали.

    Так вышло с «Анкета заполнена» 24.07.2026 — агент получал «ждёшь: — / веди разговор по
    маршруту» и вставал в тупик. Запасной сценарий обязан говорить, что делать."""
    st = funnel_next_step(_deal("C16:СОВСЕМ_НОВЫЙ_ЭТАП"))

    assert st["need"] != "—", "агент обязан знать, чего ждёт от клиента"
    assert "вопрос" in st["action"].lower(), "минимум — ответить и спросить про вопросы"
    assert "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ" in st["action"], "не понимает — зовёт людей, а не молчит"


def test_connected_client_gets_support_not_silence():
    """Найдено страницей воронки в кабинете (25.07.2026): у этапа «Подключён» не было шага, и
    агент работал по запасному сценарию. Клиент уже платит — бросать его нельзя."""
    st = funnel_next_step(_deal("C16:CONNECTED"))

    assert st["step"] == "Подключён — сопровождение"
    assert "ничего не продавай" in st["action"]
    assert "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ" in st["action"], "чего нет в базе — к живым людям"
    assert "Стадию не двигай" in st["action"]
