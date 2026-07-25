"""Порядок воронки: условия → вопросы → анкета, а не анкета → условия.

Владелец 25.07.2026: «Все люди сначала спрашивают про условия — поменяем местами этапы
"анкета заполнена" и "согласование условий". Сначала человеку даются все условия, он задаёт
вопросы, и только потом анкета».

Основание — выгрузка `telegram_bot_messages` за 24–25.07.2026: во ВСЕХ диалогах, где клиент
писал первым, первая реплика была про условия («Здравствуйте какие условия подключения к иу?»,
«на каких условиях можно к ИУ присоединиться?»), а в ответ уходило приглашение на анкету.

Признак шага здесь — состояние данных (условия отправлены? анкета есть?), а не формулировка
клиента: ловить фразу «вопросов нет» регулярками нельзя, в реальных переписках её не пишет никто.
"""
from __future__ import annotations

from tg_agent import CONTRACT_REQUISITES_FIELD, funnel_next_step

STAGE_FIRST_TOUCH = "C16:NEW"
STAGE_CONTACTED = "C16:CONTACTED"
STAGE_ANKETA = "C16:UC_ANKETA"
STAGE_TERMS = "C16:S84294149"


def _deal(stage, **uf):
    return {"id": 86, "stage": stage, "custom_fields": uf}


def test_first_touch_sends_terms_not_the_form():
    """Диалог 5195962532: «Здравствуйте какие условия подключения к иу?»

    Клиент спросил про условия — первым шагом уходит документ условий. Анкету на этом шаге
    не предлагаем: раньше в ответ на такой вопрос уходила ссылка на анкету."""
    st = funnel_next_step(_deal(STAGE_FIRST_TOUCH))

    assert st["step"] == "Ответ и условия"
    assert "send_terms" in st["action"]
    assert "анкет" not in st["action"].lower(), "анкета на первом шаге не предлагается"


def test_terms_are_sent_without_the_form_tail():
    """Условия и анкета не идут одним сообщением — сначала человек читает условия."""
    st = funnel_next_step(_deal(STAGE_CONTACTED))

    assert "offer_form=False" in st["action"].replace(" ", "")


def test_after_terms_the_step_is_questions_not_survey():
    """Диалог 212850563: после условий клиент спросил «Какой взнос?».

    Пока анкеты нет, шаг — вопросы по условиям, а не сверка анкеты."""
    st = funnel_next_step(_deal(STAGE_CONTACTED), terms_sent_to_client=True)

    assert st["step"] == "Вопросы по условиям"
    assert "search_company_knowledge" in st["action"]


def test_questions_step_offers_the_form_when_the_client_is_ready():
    """Выход из вопросов — приглашение на анкету, а не просьба реквизитов.

    Реквизиты идут после анкеты: без неё у менеджера нет ни магазина, ни оборота."""
    st = funnel_next_step(_deal(STAGE_CONTACTED), terms_sent_to_client=True)

    assert "анкет" in st["action"].lower()
    assert "реквизит" not in st["action"].lower(), "реквизиты — не следующий шаг после вопросов"


def test_questions_without_an_answer_do_not_leave_the_client_in_silence():
    """База знаний пока пустая: вопрос уходит людям, но клиент должен получить строку.

    Владелец 25.07.2026 разрешил перестройку до наполнения базы — значит на этом шаге
    молчание становится основным сценарием, а не редким."""
    st = funnel_next_step(_deal(STAGE_CONTACTED), terms_sent_to_client=True)

    assert "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ" in st["action"]
    assert "не молчи" in st["action"].lower()


def test_filled_form_is_verified_and_moves_the_deal_to_terms_stage():
    """Анкета пришла — сверяем данные и двигаем сделку на согласование условий."""
    import tg_agent

    # Признак «анкета есть» — сами данные анкеты в сделке, как их видит anketa_block.
    deal = _deal(STAGE_ANKETA, **{tg_agent.FORM_FIELDS[0]: "Одежда: брюки, юбки"})
    st = funnel_next_step(deal, terms_sent_to_client=True)

    assert st["step"] == "Сверка анкеты"
    assert STAGE_TERMS in st["action"]


def test_requisites_are_still_asked_after_the_form():
    """Порядок после анкеты не меняется: реквизиты → договор."""
    st = funnel_next_step(_deal(STAGE_TERMS), terms_sent_to_client=True)

    assert st["step"] == "Сбор реквизитов"
    assert "send_contract" in st["action"]


def test_a_deal_with_requisites_is_never_pulled_back_to_the_form():
    """Уже собранные реквизиты означают, что анкета и условия давно позади."""
    st = funnel_next_step(_deal(STAGE_TERMS, **{CONTRACT_REQUISITES_FIELD: "ИНН 7704123456"}))

    assert st["step"] == "Отправка договора"
