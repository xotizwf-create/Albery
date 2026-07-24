"""Условия клиенту — дословно из документа владельца (владелец, 23.07.2026).

Агент пропустил этап условий: сверил анкету и сразу попросил реквизиты. Теперь после
подтверждения данных он обязан отправить условия — и именно СЛОВО В СЛОВО, а не пересказом.
"""
from __future__ import annotations

import pytest

DOC = """Источник: https://docs.google.com/document/d/XXX/edit
Тип: application/vnd.google-apps.document

Условия ИУ — текст для клиента

Как работает этот документ. Всё ниже строки агент отправляет дословно.

--- ТЕКСТ КЛИЕНТУ ---

Индивидуальные условия снижают комиссию до 12% и дают приоритет в выдаче.

Стоимость — 30 000 ₽ в месяц, первый месяц бесплатно."""


@pytest.fixture
def tg(monkeypatch):
    import tg_agent

    def fake_files(_args):
        return {"files": [{"name": "Условия ИУ — текст для клиента", "google_file_id": "doc-1"},
                          {"name": "Шаблон договора ИУ", "google_file_id": "doc-2"}]}

    from mcp import context_server as cs

    monkeypatch.setitem(cs.TOOLS, "list_company_files", {"handler": fake_files})
    monkeypatch.setitem(cs.TOOLS, "get_company_file", {"handler": lambda a: {"content": DOC}})
    return tg_agent


def test_marker_mentioned_in_the_instruction_does_not_split_the_document(tg, monkeypatch):
    """Владелец заполнил условия, а агент всё равно отказывался их слать (23.07.2026).

    Причина была в шапке документа: там маркер упомянут в самой инструкции, и разрез по первому
    вхождению отдавал «клиентской частью» остаток инструкции — вместе с примером пометки
    [ЗАПОЛНИТЬ]."""
    from mcp import context_server as cs

    doc = ("Условия ИУ — текст для клиента\n\n"
           "Всё, что ниже строки «--- ТЕКСТ КЛИЕНТУ ---», агент отправляет ДОСЛОВНО.\n\n"
           "Пока в тексте остаётся пометка [ЗАПОЛНИТЬ], агент ничего не отправит.\n\n"
           "--- ТЕКСТ КЛИЕНТУ ---\n\n"
           "Комиссия WB снижена до 35%.\n\n"
           "Подключение занимает не более 2 рабочих дней")
    monkeypatch.setitem(cs.TOOLS, "get_company_file", {"handler": lambda a: {"content": doc}})

    body = tg.terms_text()

    assert body.startswith("Комиссия WB снижена")
    assert "ДОСЛОВНО" not in body and "[ЗАПОЛНИТЬ]" not in body


def test_document_without_the_marker_is_never_sent(tg, monkeypatch):
    """Иначе клиент получил бы инструкцию для владельца вместо условий."""
    from mcp import context_server as cs

    monkeypatch.setitem(cs.TOOLS, "get_company_file",
                        {"handler": lambda a: {"content": "Просто текст без разметки"}})

    with pytest.raises(ValueError, match="ТЕКСТ КЛИЕНТУ"):
        tg.terms_text()


def test_marker_with_em_dash_from_google_docs_still_works(tg, monkeypatch):
    """Живой сбой 24.07.2026: Google Docs автозаменой превратил хвост «---» в «—»
    («--- ТЕКСТ КЛИЕНТУ —»), точное сравнение сломалось, и клиент получил «техническая
    заминка по условиям» вместо условий. Маркер должен узнаваться по смыслу."""
    from mcp import context_server as cs

    doc = ("Условия ИУ — текст для клиента\n\n"
           "Всё, что ниже строки «--- ТЕКСТ КЛИЕНТУ ---», отправляется дословно.\n\n"
           "--- ТЕКСТ КЛИЕНТУ —\n\n"          # ровно как в документе владельца на проде
           "Комиссия WB снижена до 35%.")
    monkeypatch.setitem(cs.TOOLS, "get_company_file", {"handler": lambda a: {"content": doc}})

    assert tg.terms_text() == "Комиссия WB снижена до 35%."


def test_marker_survives_any_dashes_and_spacing(tg):
    """Разные тире и лишние пробелы вокруг — всё это по-прежнему маркер."""
    for marker in ("--- ТЕКСТ КЛИЕНТУ ---", "— ТЕКСТ КЛИЕНТУ —", "–ТЕКСТ КЛИЕНТУ–",
                   "---   ТЕКСТ  КЛИЕНТУ   ", "ТЕКСТ КЛИЕНТУ", "«--- текст клиенту ---»"):
        assert tg._is_terms_marker(marker), f"должно распознаваться: {marker!r}"


def test_instruction_line_is_not_mistaken_for_the_marker(tg):
    """Упоминание маркера внутри длинной фразы шапки — не маркер."""
    assert not tg._is_terms_marker(
        "Всё, что ниже строки «--- ТЕКСТ КЛИЕНТУ ---», отправляется дословно.")
    assert not tg._is_terms_marker("Здесь будет ТЕКСТ КЛИЕНТУ и ещё что-то")


def test_only_the_client_part_is_taken(tg):
    """Инструкция для владельца в начале документа клиенту уходить не должна."""
    body = tg.terms_text()

    assert "Индивидуальные условия снижают комиссию" in body
    assert "Как работает этот документ" not in body
    assert "Источник:" not in body and "Тип:" not in body


def test_text_goes_to_the_client_word_for_word(tg, monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "send_html", lambda uid, html, plain: sent.append((uid, plain)) or (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)

    res = tg.send_terms(0, 555)

    uid, text = sent[0]
    assert uid == 555 and res["sent"]
    assert "комиссию до 12%" in text and "30 000 ₽ в месяц" in text
    assert text.endswith("Есть вопросы по условиям?"), "вопрос агент добавляет сам"


def test_unfilled_document_is_never_sent(tg, monkeypatch):
    """Неполные условия у клиента хуже паузы."""
    from mcp import context_server as cs

    monkeypatch.setitem(cs.TOOLS, "get_company_file",
                        {"handler": lambda a: {"content": "--- ТЕКСТ КЛИЕНТУ ---\n[ЗАПОЛНИТЬ] цена"}})
    sent = []
    monkeypatch.setattr(tg, "send_html", lambda *a: sent.append(a) or (True, ""))

    with pytest.raises(ValueError, match="ЗАПОЛНИТЬ"):
        tg.send_terms(0, 555)
    assert sent == [], "клиенту не должно уйти ничего"


def test_missing_document_fails_loudly(tg, monkeypatch):
    from mcp import context_server as cs

    monkeypatch.setitem(cs.TOOLS, "list_company_files", {"handler": lambda a: {"files": []}})

    with pytest.raises(ValueError, match="нет документа"):
        tg.terms_text()


def test_undelivered_terms_are_not_recorded_as_sent(tg, monkeypatch):
    monkeypatch.setattr(tg, "send_html", lambda *a: (False, "чат недоступен"))
    journalled = []
    monkeypatch.setattr(tg, "journal", lambda *a, **k: journalled.append(a))

    with pytest.raises(RuntimeError, match="не отправлены"):
        tg.send_terms(0, 555)
    assert journalled == []


# --- место условий в маршруте воронки -------------------------------------------------------

def test_confirmed_form_leads_to_terms_not_to_requisites(tg):
    """Ровно то, что владелец назвал пропуском: сразу после анкеты просили реквизиты."""
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:CONTACTED", "custom_fields": {}})

    assert "send_terms" in st["action"]
    assert "реквизит" not in st["action"].lower(), "реквизиты — только после условий и вопросов"


def test_terms_step_forbids_retelling(tg):
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:S84294149", "custom_fields": {}})

    assert st["step"] == "Отправка условий"
    assert "своими словами" in st["action"].lower()


def test_after_terms_the_agent_answers_questions_and_then_asks_requisites(tg, monkeypatch):
    monkeypatch.setattr(tg, "TERMS_SENT_FIELD", "UF_CRM_TERMS")
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:S84294149",
                              "custom_fields": {"UF_CRM_TERMS": "2026-07-23"}})

    assert st["step"] == "Вопросы по условиям"
    assert "search_company_knowledge" in st["action"]
    assert "помня весь разговор" in st["action"], "контекст предыдущих шагов не теряется"
    assert "реквизиты" in st["action"].lower(), "следующий шаг назван прямо"


def test_requisites_already_collected_means_terms_are_behind(tg):
    """Старые сделки без поля-отметки не должны застрять на условиях."""
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:S84294149",
                              "custom_fields": {tg.CONTRACT_REQUISITES_FIELD: "ИНН 7704123456"}})

    assert st["step"] == "Отправка договора"


# --- условия незнакомцу: дословно из файла (владелец, 24.07.2026) -----------------------------

def _stranger(tg, monkeypatch, answer):
    """Незнакомец пишет в личку; ловим, что уйдёт клиенту."""
    import json as _json

    box = []
    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    monkeypatch.setenv("TG_LEAD_INVITE", "1")
    monkeypatch.setattr(tg, "load_state", lambda: {"business": {"C1": {"user_id": 871}}})
    monkeypatch.setattr(tg, "save_state", lambda s: None)
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {})
    monkeypatch.setattr(tg, "crm_leads_reachable", lambda: True)
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: answer)
    monkeypatch.setattr(tg, "send_as_account",
                        lambda uid, t, parse_mode="": box.append(t) or (True, ""))
    tg.maybe_autoreply({"business_connection_id": "C1", "chat": {"id": 777, "type": "private"},
                        "from": {"id": 777, "username": "novy", "first_name": "Иван"},
                        "text": "какие условия подключения?"})
    return box


def test_conditions_are_sent_word_for_word_from_the_file(tg, monkeypatch):
    """Модель НЕ пересказывает условия: до этого цифры гуляли от диалога к диалогу
    («200 000 ₽ вместо 500 000» в одном чате, «комиссия 44%» в другом)."""
    box = _stranger(tg, monkeypatch, tg.TERMS_REQUEST_MARKER)

    assert box, "клиенту должны уйти условия"
    text = box[0]
    assert "Индивидуальные условия снижают комиссию" in text, "текст ровно из документа"
    assert "30 000 ₽ в месяц" in text
    assert tg.TERMS_REQUEST_MARKER not in text, "служебный маркер клиенту не показываем"


def test_form_invite_follows_the_conditions(tg, monkeypatch):
    """Цель — анкета: после условий приглашение идёт в конце того же сообщения."""
    box = _stranger(tg, monkeypatch, tg.TERMS_REQUEST_MARKER)

    assert tg.LEAD_FORM_URL in box[0]
    assert box[0].index("Индивидуальные условия") < box[0].index(tg.LEAD_FORM_URL), \
        "сначала условия, анкета — в конце"


def test_stranger_rules_forbid_promises_and_article_questions(tg):
    """Живой сбой: клиенту с оборотом 200 млн агент пообещал «посмотрим экономику по артикулу»."""
    rules = tg.STRANGER_RULES.lower()

    assert "не проси артикул" in rules
    assert "посчитать экономику" in rules
    assert tg.TERMS_REQUEST_MARKER in tg.STRANGER_RULES


def test_lead_asking_about_conditions_gets_the_file_word_for_word(tg, monkeypatch):
    """Живой сбой 24.07.2026 (диалог 1451982360, сделка 110): лид спросил «какие условия» и
    получил самодельную выжимку — маркер работал только у незнакомца. У лида он обязан
    работать тоже: условия уходят дословно через send_terms."""
    sent, called = [], []
    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    monkeypatch.setattr(tg, "load_state", lambda: {"business": {"C1": {"user_id": 871}}})
    monkeypatch.setattr(tg, "save_state", lambda s: None)
    monkeypatch.setattr(tg, "lead_deal_for_username", lambda u: 110)
    monkeypatch.setattr(tg, "funnel_step_block", lambda d: "Шаг: вопросы по условиям")
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg, "react", lambda *a, **k: None)
    monkeypatch.setattr(tg, "chat_history", lambda *a, **k: "")
    monkeypatch.setattr(tg, "_dialog_out_watermark", lambda d: 0)
    monkeypatch.setattr(tg, "_out_messages_after", lambda d, s: 0)
    monkeypatch.setattr(tg, "hermes_answer",
                        lambda p, s, toolsets=None: tg.TERMS_REQUEST_MARKER)
    monkeypatch.setattr(tg, "send_html",
                        lambda uid, html, plain: sent.append(plain) or (True, ""))
    monkeypatch.setattr(tg, "send_as_account",
                        lambda uid, t, parse_mode="": called.append(t) or (True, ""))

    tg.maybe_autoreply({"business_connection_id": "C1", "chat": {"id": 555, "type": "private"},
                        "from": {"id": 555, "username": "lead", "first_name": "Пётр"},
                        "text": "Евгений передал контакт, какие условия подключения к ИУ"})

    assert sent, "лиду должны уйти условия"
    assert "Индивидуальные условия снижают комиссию" in sent[0], "текст ровно из документа"
    assert tg.TERMS_REQUEST_MARKER not in sent[0], "служебный маркер клиенту не показываем"


def test_terms_marker_rule_reaches_both_branches(tg):
    """Правило про дословные условия лежит в общих правилах тона — значит и лид, и незнакомец."""
    assert tg.TERMS_REQUEST_MARKER in tg.STYLE_RULES
    assert "не пересказывай" in tg.STYLE_RULES.lower()


# --- документ условий ОДИН раз, вопрос поверх него — людям (владелец, 24.07.2026) --------------

def _lead_turn(tg, monkeypatch, client_text, state_extra=None, answer=None):
    """Ход лида воронки; возвращаем (что ушло клиенту, что унесли людям)."""
    sent, to_humans = [], []
    state = {"business": {"C1": {"user_id": 871}}, **(state_extra or {})}
    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    monkeypatch.setattr(tg, "load_state", lambda: state)
    monkeypatch.setattr(tg, "save_state", lambda s: None)
    monkeypatch.setattr(tg, "lead_deal_for_username", lambda u: 120)
    monkeypatch.setattr(tg, "funnel_step_block", lambda d: "Шаг: вопросы по условиям")
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg, "react", lambda *a, **k: None)
    monkeypatch.setattr(tg, "chat_history", lambda *a, **k: "")
    monkeypatch.setattr(tg, "_dialog_out_watermark", lambda d: 0)
    monkeypatch.setattr(tg, "_out_messages_after", lambda d, s: 0)
    monkeypatch.setattr(tg, "hermes_answer",
                        lambda p, s, toolsets=None: answer or tg.TERMS_REQUEST_MARKER)
    monkeypatch.setattr(tg, "escalate_to_human",
                        lambda author, q, ctext, answered=False: to_humans.append(q))
    monkeypatch.setattr(tg, "send_html",
                        lambda uid, html, plain: sent.append(plain) or (True, ""))
    # Отметку в сделке подменяем: проверяем поведение с клиентом, а не поход в Битрикс.
    from mcp import context_server as cs
    monkeypatch.setitem(cs.TOOLS, "update_crm_deal", {"handler": lambda a: {"ok": True}})

    tg.maybe_autoreply({"business_connection_id": "C1", "chat": {"id": 764181402, "type": "private"},
                        "from": {"id": 764181402, "username": "lead200", "first_name": "Сергей"},
                        "text": client_text})
    return sent, to_humans


def test_question_on_top_of_sent_terms_goes_to_humans_not_a_second_document(tg, monkeypatch):
    """Живой сбой 24.07.2026, диалог 764181402: условия клиент уже получил (запись 394), затем
    спросил «Какой дрр нужно держать и как происходит управление? +какая комиссия ваша по
    партнерской этой программе?» — и агент ВТОРОЙ раз выслал весь документ (запись 402), на
    вопрос не ответил и людям не передал. Слов «ДРР» и «управление» в документе нет."""
    sent, to_humans = _lead_turn(
        tg, monkeypatch,
        "Какой дрр нужно держать и как происходит управление?\n"
        "+какая комиссия ваша по партнерской этой программе?",
        state_extra={"terms_sent": {"764181402": "2026-07-24T16:03:49+00:00"}})

    assert sent, "клиент не должен остаться в тишине"
    assert "Индивидуальные условия снижают комиссию" not in sent[0], \
        "второй раз документ условий не дублируем"
    assert sent[0] == tg.TERMS_ASK_HUMAN_REPLY, "клиенту — одна короткая строка"
    assert to_humans, "вопрос обязан уйти живым людям"
    assert "дрр" in to_humans[0].lower(), "людям уходит именно вопрос клиента"


def test_first_terms_question_still_sends_the_document(tg, monkeypatch):
    """Правило не должно ломать основное: первый вопрос про условия — документ дословно."""
    sent, to_humans = _lead_turn(tg, monkeypatch, "какие условия подключения к ИУ?")

    assert sent and "Индивидуальные условия снижают комиссию" in sent[0]
    assert not to_humans, "людей по первому вопросу не беспокоим"


def test_asking_to_resend_gets_the_document_again(tg, monkeypatch):
    """«Пришлите ещё раз» — это просьба о документе, а не вопрос поверх него."""
    sent, to_humans = _lead_turn(
        tg, monkeypatch, "Условия не пришли, пришлите ещё раз пожалуйста",
        state_extra={"terms_sent": {"764181402": "2026-07-24T16:03:49+00:00"}})

    assert sent and "Индивидуальные условия снижают комиссию" in sent[0]
    assert not to_humans


def test_resend_detector_tells_a_repeat_request_from_a_new_question(tg):
    """Границу решает один разбор текста — проверяем обе стороны на живых формулировках."""
    assert tg._wants_terms_again("пришлите ещё раз")
    assert tg._wants_terms_again("не получил документ")
    assert tg._wants_terms_again("продублируйте условия")
    assert not tg._wants_terms_again("Какой дрр нужно держать и как происходит управление?")
    assert not tg._wants_terms_again("а какая комиссия ваша по этой программе?")


def test_confirming_the_anketa_is_not_a_terms_question(tg, monkeypatch):
    """Живой тупик 24.07.2026 (Александр, сделка 148): клиент написал «Все верно», модель
    вернула маркер условий, а условия ему уже отправляли — и клиент получил «Уточню это у
    команды и вернусь с ответом». Он ничего не спрашивал: людей дёргать не за чем, а разговор
    надо вести дальше."""
    sent, to_humans = _lead_turn(
        tg, monkeypatch, "Все верно",
        state_extra={"terms_sent": {"764181402": "2026-07-24T18:52:48+00:00"}})

    assert sent, "клиент не должен остаться без ответа"
    assert sent[0] != tg.TERMS_ASK_HUMAN_REPLY, "это не вопрос — людям не уносим"
    assert not to_humans, "живых людей дёргать не за чем"
    assert "вопрос" in sent[0].lower(), "менеджер спрашивает, остались ли вопросы по условиям"
    assert "Индивидуальные условия снижают комиссию" not in sent[0], "документ второй раз не шлём"


def test_question_words_are_told_apart_from_confirmations(tg):
    """Граница «вопрос / подтверждение» решается одним разбором — проверяем обе стороны."""
    assert tg._looks_like_question("Какой дрр нужно держать?")
    assert tg._looks_like_question("а что по срокам")
    assert tg._looks_like_question("сколько это стоит")
    assert not tg._looks_like_question("Все верно")
    assert not tg._looks_like_question("да")
    assert not tg._looks_like_question("Заполнил")
