"""Детерминированный контракт одного хода ИУ-консультанта.

Модель формулирует ответ, но не решает, можно ли выслать документ или анкету. Эти границы
проверяются отдельно от Telegram/CRM, чтобы слова «цена», «ссылка» или обычное «и у» не
превращались в согласие клиента на следующий этап.
"""
from __future__ import annotations

import pytest

import iu_turn_policy as policy


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Здравствуйте", False),
        ("Какие условия подключения к ИУ?", False),
        ("Хочу узнать условия ИУ", False),
        ("Какая комиссия на WB?", False),
        ("Не хочу подключаться к ИУ", False),
        ("Подключаться не хочу", False),
        ("Не планирую подключаться к ИУ", False),
        ("Мне не нужно подключение к ИУ", False),
        ("Не согласен подключаться к ИУ", False),
        ("Не намерен подключаться к ИУ", False),
        ("Не подключайте меня к ИУ", False),
        ("ИУ мне не нужен", False),
        ("Мне ИУ не подходит", False),
        ("Отказываюсь подключаться к ИУ", False),
        ("Я против подключения к ИУ", False),
        ("Подключение к ИУ не требуется", False),
        ("Пока без ИУ", False),
        ("ИУ мне неинтересен", False),
        ("Не предлагайте мне ИУ", False),
        ("ИУ мне не предлагайте", False),
        ("Я не разрешаю подключать меня к ИУ", False),
        ("Я не подключусь к ИУ", False),
        ("К ИУ не подключусь", False),
        ("Я не присоединюсь к ИУ", False),
        ("Не стану подключаться к ИУ", False),
        ("Подключаться к ИУ не стану", False),
        ("Я не подключаюсь к ИУ", False),
        ("Я отказался от ИУ", False),
        ("Я отказалась от ИУ", False),
        ("Я не заинтересован в ИУ", False),
        ("ИУ меня не заинтересовали", False),
        ("Не отправляйте форму", False),
        ("Анкету не отправляйте", False),
        ("Где ссылка на доставку?", False),
        ("Где форма оплаты?", False),
        ("Где заявка на возврат?", False),
        ("Отправьте анкету кандидата", False),
        ("Где анкета сотрудника?", False),
        ("Хочу подключить интернет", False),
        ("Хочу подключить онлайн-кассу", False),
        ("Готов сотрудничать по доставке тканей", False),
        ("Начинаем работать по поставке", False),
        ("Хочу подключиться к ИУ", True),
        ("Да, давайте подключаться", True),
        ("Готов передать данные для подключения к ИУ", True),
        ("Пришлите анкету", True),
        ("Да, пришлите анкету", True),
        ("Отправьте, пожалуйста, анкету", True),
        ("Где анкета для подключения?", True),
        ("Пришлите форму для подключения", True),
        ("Подключите меня к ИУ", True),
        ("Подключи меня к ИУ", True),
        ("Можете подключить меня к ИУ", True),
        ("Прошу подключить меня к ИУ", True),
        ("Начните подключение к ИУ", True),
        ("Оформите подключение к ИУ", True),
        ("Запускайте подключение к ИУ", True),
        ("Я выбираю ИУ", True),
        ("Я согласен на ИУ", True),
        ("Хочу вступить в партнёрскую программу", True),
    ],
)
def test_form_requires_explicit_readiness_or_direct_request(text, expected):
    assert policy.wants_form(text) is expected


def test_plain_lowercase_and_u_is_not_the_iu_acronym():
    assert not policy.has_iu_context("И у нас завтра обычная поставка")
    assert policy.has_iu_context("Расскажите про ИУ")
    assert policy.has_iu_context("Расскажите про иу")


@pytest.mark.parametrize(
    "text",
    [
        "Не уверен, что хочу подключаться к ИУ",
        "Не знаю, хочу ли подключаться к ИУ",
        "Ещё не решил, подключаться ли к ИУ",
        "Пока не решил подключаться к ИУ",
        "Готов обсудить подключение к ИУ",
        "Давайте сначала обсудим, стоит ли подключаться к ИУ",
        "Нужно узнать про подключение к ИУ",
        "Планирую изучить подключение к ИУ",
    ],
)
def test_exploration_is_interest_but_not_readiness(text):
    intent = policy.classify(text)
    assert intent.open_deal
    assert not intent.offer_form


def test_short_confirmation_needs_relevant_last_agent_question():
    relevant = ("Клиент: Какие следующие шаги?\n"
                "Ты: Расскажу, как подключиться.\n\n"
                "Готовы заполнить анкету для подключения к ИУ?")
    irrelevant = "Клиент: Когда доставка?\nТы: Доставить завтра удобно?"

    assert policy.wants_form("Да, давайте", relevant)
    assert not policy.wants_form("Да, давайте", irrelevant)
    assert not policy.wants_form("Нет, не готов", relevant)
    assert not policy.wants_form("Да", "Ты: Анкету повторно заполнять не нужно.")
    assert not policy.wants_form("Да", "Ты: Вы уже заполнили анкету?")
    assert not policy.wants_form("Да", "Ты: Где ваша анкета?")


@pytest.mark.parametrize(
    ("offer", "confirmation"),
    [
        ("Хотите, я пришлю анкету для подключения к ИУ?", "Да"),
        ("Прислать вам анкету для подключения к ИУ?", "Да, давайте"),
        ("Отправить ссылку на анкету?", "Хорошо"),
        ("Давайте я пришлю анкету.", "Да"),
        ("Предлагаю заполнить анкету для подключения.", "Да"),
        ("Могу отправить форму для подключения.", "Конечно"),
        ("Готовы заполнить анкету?", "Конечно"),
    ],
)
def test_common_prior_form_offer_accepts_a_natural_confirmation(offer, confirmation):
    assert policy.wants_form(confirmation, f"Ты: {offer}", known_lead=True)


@pytest.mark.parametrize(
    "offer",
    [
        "Я не могу прислать ссылку на анкету для подключения.",
        "Не можем отправить форму сейчас.",
    ],
)
def test_confirmation_does_not_accept_a_negated_form_offer(offer):
    assert not policy.wants_form("Да", f"Ты: {offer}", known_lead=True)


@pytest.mark.parametrize(
    "text",
    [
        "Можно анкету?",
        "Анкету, пожалуйста",
        "Нужна анкета",
        "Дайте ссылку",
        "Ссылку, пожалуйста",
        "Можно ссылку?",
        "Да, пришлите",
        "Хорошо, пришлите",
        "Давайте анкету",
        "Готов, присылайте",
        "Да, заполню",
        "Заполню",
        "Я заполню анкету",
        "Могу заполнить анкету",
        "Хочу анкету",
    ],
)
def test_normal_reply_confirms_a_real_prior_form_offer(text):
    history = "Ты: Могу прислать ссылку на анкету для подключения к ИУ."
    assert policy.wants_form(text, history, known_lead=True)


@pytest.mark.parametrize(
    "text",
    [
        "Можно анкету?",
        "Анкету, пожалуйста",
        "Нужна анкета",
        "Давайте анкету",
        "Я заполню анкету",
        "Могу заполнить анкету",
        "Заполню анкету",
        "Давайте заполним анкету",
    ],
)
def test_unambiguous_named_form_intent_needs_no_prior_offer(text):
    assert policy.wants_form(text)


@pytest.mark.parametrize(
    "text",
    [
        "Могу передать данные для подключения к ИУ",
        "Передам данные для подключения к ИУ",
        "Предоставлю данные для подключения к ИУ",
    ],
)
def test_explicit_data_transfer_readiness_offers_the_form(text):
    assert policy.wants_form(text)


def test_crm_stage_alone_does_not_turn_any_form_into_the_iu_questionnaire():
    assert not policy.wants_form("Где форма оплаты?", known_lead=True)
    assert policy.wants_form(
        "Где форма?",
        "Ты: Обсуждаем подключение к ИУ; могу прислать ссылку на анкету.",
        known_lead=True,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Пришлите форму оплаты ИУ",
        "Отправьте форму договора ИУ",
        "Пришлите анкету кандидата для ИУ",
        "Где анкета сотрудника ИУ?",
        "Пришлите форму регистрации на ИУ",
    ],
)
def test_foreign_named_form_is_not_reinterpreted_by_an_iu_suffix(text):
    intent = policy.classify(text, known_lead=True)

    assert not intent.asks_terms
    assert not intent.asks_form
    assert not intent.offer_form


@pytest.mark.parametrize(
    "text",
    [
        "Пришлите анкету для подключения к ИУ",
        "Где анкета для подключения?",
        "Пришлите форму для ИУ",
    ],
)
def test_relevant_iu_form_tail_remains_allowed(text):
    assert policy.wants_form(text)


@pytest.mark.parametrize(
    "text",
    [
        "Повторно пришлите анкету кандидата",
        "Снова отправьте анкету сотрудника",
        "Продублируйте форму оплаты",
        "Продублируйте форму договора",
        "Повторите форму регистрации на мероприятие",
    ],
)
def test_foreign_form_resend_is_not_reinterpreted_by_the_crm_stage(text):
    intent = policy.classify(text, known_lead=True)

    assert not intent.resend_form
    assert not intent.offer_form
    assert not intent.open_deal


@pytest.mark.parametrize(
    "text",
    [
        "Снова отправьте ссылку",
        "Продублируйте ссылку",
        "Повторите ссылку",
        "Не получил ссылку",
    ],
)
def test_bare_link_resend_needs_prior_form_context_not_just_crm_stage(text):
    assert not policy.wants_form(text, known_lead=True)
    assert not policy.wants_form(
        text,
        "Ты: Договор доступен по ссылке https://example.com.",
        known_lead=True,
    )
    assert policy.wants_form(
        text,
        "Ты: Могу отправить ссылку на анкету для подключения к ИУ.",
        known_lead=True,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Пришлите анкету ещё раз",
        "Анкета не пришла, пришлите ещё раз",
        "Скиньте ссылку на анкету повторно",
        "Можно повторно отправить анкету?",
        "Повторите ссылку на форму",
        "Продублируйте анкету",
        "Отправьте, пожалуйста, анкету ещё раз",
    ],
)
def test_explicit_form_resend_is_a_recovery_intent(text):
    intent = policy.classify(
        text,
        "Ты: Ранее отправил ссылку на анкету для подключения к ИУ.",
        known_lead=True,
    )
    assert intent.resend_form
    assert intent.offer_form


@pytest.mark.parametrize(
    ("text", "known_lead", "expected"),
    [
        ("Какие условия подключения к ИУ?", False, True),
        ("Сколько стоит ИУ?", False, True),
        ("Расскажите про индивидуальные условия", False, True),
        ("Какая комиссия на WB?", False, True),
        ("Сколько стоит доставка документов?", False, False),
        ("Какая цена рекламы?", True, False),
        ("Сколько стоит ремонт офиса?", True, False),
        ("Какая комиссия банка?", True, False),
        ("Какие условия возврата?", True, False),
        ("Сервер сколько стоит?", True, False),
        ("Подписка сколько стоит?", True, False),
        ("Хостинг какая цена?", True, False),
        ("У брокера какая комиссия?", True, False),
        ("Условия ИУ мне не интересны", False, False),
        ("Не хочу знать условия ИУ", False, False),
        ("Я не спрашивал про условия ИУ", False, False),
        ("Какая комиссия? Хотя нет, условия не присылайте", True, False),
        ("Условия не присылайте", True, False),
        ("Условия не нужны", True, False),
        ("Мне не нужны условия ИУ", False, False),
        ("Мне не интересны условия ИУ", False, False),
        ("Не рассказывайте условия ИУ", False, False),
        ("Мне не подходят условия ИУ", False, False),
        ("Условия ИУ мне не подходят", False, False),
        ("Не согласен с условиями ИУ", False, False),
        ("Условия ИУ меня не устраивают", False, False),
        ("Меня не устраивает комиссия ИУ", False, False),
        ("Не принимаю условия ИУ", False, False),
        ("От условий ИУ отказываюсь", False, False),
        ("Условия ИУ отвергаю", False, False),
        ("Мне неинтересны условия ИУ", False, False),
        ("Условия ИУ неинтересны", False, False),
        ("Неинтересно, какие условия ИУ", False, False),
        ("Мне неинтересна комиссия ИУ", False, False),
        ("Файл не пришёл, отправьте ещё раз условия", True, True),
        ("какая комиссия?", True, True),
        ("Здравствуйте", True, False),
    ],
)
def test_terms_document_requires_an_iu_commercial_question(text, known_lead, expected):
    assert policy.asks_terms(text, known_lead=known_lead) is expected


@pytest.mark.parametrize(
    "text",
    [
        "Какие условия партнёрской программы?",
        "Какая комиссия по партнёрской программе?",
        "Сколько стоит партнёрская программа?",
        "Расскажите об условиях партнёрской программы",
        "Партнёрская программа, какие условия?",
    ],
)
def test_scoped_partner_program_commercial_questions_are_iu_terms(text):
    assert policy.asks_terms(text)
    assert policy.has_iu_interest(text)


@pytest.mark.parametrize(
    "text",
    [
        "Какие условия партнёрской программы школы?",
        "Какая комиссия партнёрской программы клуба?",
        "Сколько стоит партнёрская программа фитнес-центра?",
        "Расскажите условия партнёрской программы магазина",
        "Какие условия партнёрской программы школы для продавцов?",
    ],
)
def test_foreign_partner_program_does_not_open_iu_or_send_terms(text):
    intent = policy.classify(text)

    assert not intent.asks_terms
    assert not intent.open_deal


@pytest.mark.parametrize(
    "text",
    [
        "Школа предлагает партнёрскую программу",
        "Какие условия: школа предлагает партнёрскую программу?",
        "Хочу вступить: школа предлагает партнёрскую программу",
        "Фитнес-центр запустил партнёрскую программу",
        "Какая комиссия, если магазин запустил партнёрскую программу?",
    ],
)
def test_foreign_partner_program_owner_before_the_alias_is_not_iu(text):
    intent = policy.classify(text)

    assert not intent.asks_terms
    assert not intent.offer_form
    assert not intent.open_deal


@pytest.mark.parametrize(
    "text",
    [
        "Хочу вступить в партнёрскую программу школы",
        "Хочу вступить в партнёрскую программу клуба",
        "Хочу вступить в партнёрскую программу фитнес-центра",
        "Хочу вступить в партнёрскую программу магазина",
        "Хочу вступить в партнёрскую программу мероприятия",
    ],
)
def test_foreign_partner_program_join_does_not_offer_iu_form(text):
    intent = policy.classify(text)

    assert not intent.offer_form
    assert not intent.open_deal


def test_partner_program_join_accepts_only_the_bare_product_or_known_qualifier():
    assert policy.wants_form("Хочу вступить в партнёрскую программу")
    assert policy.wants_form("Хочу вступить в партнёрскую программу WB")


@pytest.mark.parametrize(
    "text",
    [
        "Меня интересует партнёрская программа",
        "Мне интересна партнёрская программа",
        "Я продавец, интересует партнёрская программа",
        "Интересует партнёрская программа",
    ],
)
def test_natural_first_person_partner_interest_opens_an_iu_conversation(text):
    intent = policy.classify(text)

    assert intent.open_deal
    assert not intent.offer_form


@pytest.mark.parametrize(
    "text",
    [
        "Готов вступить в партнёрскую программу",
        "Согласен вступить в партнёрскую программу",
        "Решил вступить в партнёрскую программу",
        "Готов участвовать в партнёрской программе",
    ],
)
def test_natural_partner_commitment_offers_the_form(text):
    assert policy.wants_form(text)


@pytest.mark.parametrize(
    "text",
    [
        "Хочу сотрудничать",
        "Готов сотрудничать",
        "Давайте сотрудничать",
        "Хочу начать работать",
        "Готов начать работать",
        "Давайте начать работать",
    ],
)
def test_generic_cooperation_is_not_bare_iu_readiness(text):
    intent = policy.classify(text)

    assert not intent.offer_form
    assert not intent.open_deal


@pytest.mark.parametrize(
    "text",
    [
        "Пришлите условия",
        "Отправьте документ с условиями",
        "Скиньте условия",
        "Можно условия?",
        "Условия, пожалуйста",
        "Дайте условия",
        "Покажите тарифы",
        "Хочу посмотреть условия",
        "Где посмотреть условия?",
        "Где условия?",
    ],
)
def test_bare_terms_request_is_allowed_only_in_known_iu_context(text):
    history = "Ты: Обсуждаем индивидуальные условия ИУ."
    assert policy.asks_terms(text, history, known_lead=True)
    assert not policy.asks_terms(text)


def test_explicit_iu_asset_survives_an_unrelated_second_clause():
    assert policy.asks_terms("Какие условия ИУ и сколько стоит доставка?")
    assert policy.asks_terms("Какая комиссия ИУ? И ещё нужен возврат")
    assert policy.wants_form("Хочу подключиться к ИУ, а доставка завтра")
    assert policy.wants_form("Готов подключаться к ИУ, но есть вопрос по логистике")
    assert policy.wants_form("Пришлите анкету для ИУ и скажите по доставке")


@pytest.mark.parametrize(
    "text",
    [
        "Пришлите анкету, а какие условия ИУ?",
        "Пришлите анкету, и сколько стоит ИУ?",
        "Пришлите анкету, какая комиссия по ИУ?",
        "Пришлите анкету, хочу узнать условия ИУ",
    ],
)
def test_form_tail_parser_preserves_a_separate_commercial_clause(text):
    intent = policy.classify(text)

    assert intent.asks_terms
    assert intent.offer_form


def test_additional_question_is_detected_even_when_all_words_belong_to_iu():
    text = "Какой ДРР нужно держать и как происходит управление? Какая комиссия по ИУ?"

    assert policy.asks_terms(text)
    assert policy.has_additional_question(text)
    assert not policy.has_additional_question("Какие условия и какая комиссия по ИУ?")


@pytest.mark.parametrize(
    "text",
    [
        "Какие условия ИУ, какой ДРР держать?",
        "Какая комиссия ИУ; кто управляет кабинетом?",
        "Какие условия ИУ — какой порог оборота?",
        "Какие условия ИУ, расскажите про управление кабинетом",
        "Пришлите условия ИУ и объясните, кто отвечает за кабинет",
        "Какая комиссия ИУ, управление кабинетом как устроено?",
    ],
)
def test_additional_question_detects_punctuation_and_request_verb_boundaries(text):
    assert policy.asks_terms(text)
    assert policy.has_additional_question(text)


def test_asset_specific_rejection_does_not_cancel_a_separate_requested_asset():
    first = policy.classify("Анкету не присылайте, но какие условия ИУ?")
    assert first.asks_terms and not first.offer_form
    plain_comma = policy.classify("Не хочу анкету, расскажите про условия ИУ")
    assert plain_comma.asks_terms and not plain_comma.offer_form
    second = policy.classify("Условия не нужны, но пришлите анкету для ИУ")
    assert not second.asks_terms and second.offer_form
    third = policy.classify("Документ не присылайте, но хочу подключиться к ИУ")
    assert not third.asks_terms and third.offer_form


def test_pure_refusal_does_not_cancel_a_separate_later_positive_clause():
    terms = policy.classify("Я не подключусь к ИУ, но какие условия ИУ?")
    form = policy.classify("Я отказался от ИУ, но теперь хочу подключиться к ИУ")

    assert terms.asks_terms
    assert form.offer_form


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Здравствуйте", False),
        ("Сколько стоит?", False),
        ("Отгрузили паллеты вчера", False),
        ("Не хочу подключаться к ИУ", False),
        ("Хочу подключить интернет", False),
        ("Готов сотрудничать по доставке тканей", False),
        ("Какие условия доставки на Wildberries?", False),
        ("Какие условия возврата на WB?", False),
        ("Условия поставки для Вайлдберриз", False),
        ("Какая комиссия банка на WB?", False),
        ("Условия рекламы на Wildberries", False),
        ("Партнёрская программа доставки WB", False),
        ("Какие условия подключения интернета?", False),
        ("Сколько стоит подключение онлайн-кассы?", False),
        ("Какие условия подключения электричества?", False),
        ("Сколько стоит подключение телефонии?", False),
        ("Какая цена подключения CRM?", False),
        ("Расскажите про тариф подключения API?", False),
        ("Сколько стоит подключить сотрудника?", False),
        ("Какая комиссия за подключение платежей?", False),
        ("Какие условия подключения магазина?", False),
        ("Какие индивидуальные условия труда?", False),
        ("Какие индивидуальные условия проживания?", False),
        ("Какие индивидуальные условия обучения?", False),
        ("Индивидуальные условия договора", False),
        ("Индивидуальные условия кредита", False),
        ("Расскажите о партнёрской программе лояльности", False),
        ("Как работает партнёрская программа авиакомпании?", False),
        ("Что такое партнёрская программа университета?", False),
        ("Какие условия подключения?", True),
        ("Сколько стоит подключение?", True),
        ("Какие индивидуальные условия?", True),
        ("Расскажите о партнёрской программе", True),
        ("Какие условия подключения к ИУ?", True),
        ("Хочу подключиться", True),
        ("Пришлите анкету", True),
    ],
)
def test_crm_interest_uses_the_same_narrow_product_intent(text, expected):
    assert policy.has_iu_interest(text) is expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Помогу с подключением.", False),
        ("Какой у вас оборот?", True),
        ("Пришлите ссылку на магазин.", True),
        ("Конечно, пришлите ссылку на магазин.", True),
        ("Для начала заполните анкету.", True),
        ("Подробнее: https://example.com", True),
    ],
)
def test_competing_next_step_is_detected_mechanically(answer, expected):
    assert policy.has_next_step(answer) is expected


def test_impersonal_modal_fact_is_not_mistaken_for_a_client_cta():
    for answer in (
        "Договор можно подписать по ЭДО.",
        "Можно выбрать ЭДО или бумагу.",
    ):
        assert not policy.has_next_step(answer)
        assert policy.without_next_steps(answer) == answer


def test_runtime_keeps_the_answer_but_drops_extra_model_ctas():
    answer = "Подключение доступно. Какой у вас вопрос? Пришлите ссылку на магазин."

    result = policy.keep_one_next_step(answer)

    assert result == "Подключение доступно. Какой у вас вопрос?"
    assert result.count("?") == 1
    assert "Пришлите" not in result


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Ознакомьтесь с условиями и обратитесь к менеджеру.",
         "Ознакомьтесь с условиями."),
        ("Выберите вариант и оформите заявку.", "Выберите вариант."),
        ("Проверьте данные и подтвердите их.", "Проверьте данные."),
        ("Оставьте заявку и ждите звонка.", "Оставьте заявку."),
        ("Посмотрите документ и дождитесь ответа.", "Посмотрите документ."),
    ],
)
def test_runtime_recognizes_common_imperatives_as_competing_ctas(answer, expected):
    assert policy.keep_one_next_step(answer) == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Зайдите в кабинет и пришлите данные.", "Зайдите в кабинет."),
        ("Скачайте шаблон и заполните его.", "Скачайте шаблон."),
        ("Введите ИНН и нажмите кнопку.", "Введите ИНН."),
        ("Раскройте ссылку и заполните форму.", "Раскройте ссылку."),
    ],
)
def test_runtime_detects_generic_plural_imperatives(answer, expected):
    assert policy.keep_one_next_step(answer) == expected


def test_generic_imperative_detector_does_not_eat_a_greeting():
    assert policy.keep_one_next_step(
        "Здравствуйте! Чем могу помочь?"
    ) == "Здравствуйте! Чем могу помочь?"


def test_system_form_removes_all_model_imperatives_before_its_canonical_cta():
    assert policy.without_next_steps(
        "Ознакомьтесь с условиями и обратитесь к менеджеру."
    ) == ""


def test_runtime_splits_questions_without_spaces_and_limits_ctas_in_one_sentence():
    assert policy.keep_one_next_step("Что уточнить?Какой оборот?") == "Что уточнить?"
    result = policy.keep_one_next_step(
        "Всё готово. Для начала заполните анкету и пришлите ссылку на магазин."
    )
    assert result == "Всё готово. Для начала заполните анкету."


def test_runtime_can_remove_model_steps_before_the_system_form():
    assert policy.without_next_steps(
        "Подключение доступно. Что хотите уточнить? Заполните старую форму."
    ) == "Подключение доступно."
    assert policy.without_form_steps(
        "Здравствуйте! Заполните анкету. Чем могу помочь?"
    ) == "Здравствуйте! Чем могу помочь?"
    assert policy.without_form_steps(
        "Здравствуйте! Вот анкета: https://example.com"
    ) == "Здравствуйте!"
    for offer in (
        "Могу прислать анкету.",
        "Можем перейти к анкете.",
        "Хотите анкету.",
        "Предлагаю анкету.",
        "Советую анкету.",
        "Рекомендую анкету.",
        "Я пришлю анкету.",
    ):
        assert policy.without_form_steps(offer) == ""
    assert policy.without_form_steps(
        "Форма оплаты доступна в личном кабинете."
    ) == "Форма оплаты доступна в личном кабинете."
    assert policy.without_form_steps(
        "Форма договора доступна после проверки."
    ) == "Форма договора доступна после проверки."
    assert policy.without_form_steps(
        "Анкета кандидата доступна в HR-системе."
    ) == "Анкета кандидата доступна в HR-системе."
    assert policy.without_form_steps(
        "Анкета для подключения доступна здесь."
    ) == ""
    assert not policy.wants_form("Да", "Ты: Чем могу помочь?")


def test_runtime_puts_information_before_the_single_next_step():
    assert policy.keep_one_next_step(
        "Пришлите ссылку. ИУ подключается после проверки."
    ) == "ИУ подключается после проверки. Пришлите ссылку."
    assert policy.keep_one_next_step(
        "Пришлите ссылку и какой у вас оборот?"
    ) == "Пришлите ссылку."
    assert policy.keep_one_next_step(
        "Лучше заполнить анкету. Какой у вас вопрос?"
    ) == "Лучше заполнить анкету."
    assert policy.keep_one_next_step(
        "Направьте ИНН и заполните анкету."
    ) == "Направьте ИНН."
    assert policy.keep_one_next_step(
        "Покажите карточку и пришлите реквизиты."
    ) == "Покажите карточку."
    assert policy.keep_one_next_step(
        "Ответьте менеджеру и отправьте данные."
    ) == "Ответьте менеджеру."
    assert policy.keep_one_next_step(
        "Пришлите ИНН и после проверки ИУ подключается."
    ) == "После проверки ИУ подключается. Пришлите ИНН."
    assert policy.keep_one_next_step(
        "Заполните анкету — подключение доступно после проверки."
    ) == "Подключение доступно после проверки. Заполните анкету."
    assert policy.keep_one_next_step(
        "Напишите нам: комиссия составляет 10%."
    ) == "Комиссия составляет 10%. Напишите нам."
    assert policy.keep_one_next_step(
        "Перейдите по ссылке и условия применятся после проверки."
    ) == "Условия применятся после проверки. Перейдите по ссылке."


def test_runtime_does_not_reask_fields_that_belong_to_the_form():
    assert policy.keep_one_next_step(
        "ИУ доступно продавцам WB. Какой у вас оборот? Что хотите уточнить?"
    ) == "ИУ доступно продавцам WB. Что хотите уточнить?"
    assert policy.keep_one_next_step(
        "Помогу с подключением. Пришлите ссылку на магазин."
    ) == "Помогу с подключением."


def test_runtime_counts_urls_and_bare_action_labels_as_next_steps():
    assert policy.keep_one_next_step(
        "Условия описаны здесь: https://example.com Что хотите уточнить?"
    ) == "Условия описаны здесь. https://example.com"
    assert policy.keep_one_next_step(
        "Перейти по ссылке. Какой у вас вопрос?"
    ) == "Перейти по ссылке."
