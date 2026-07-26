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


def test_terms_plus_explicit_join_use_the_form_as_the_only_next_step(tg, monkeypatch):
    sent, invited = [], []
    monkeypatch.setattr(tg, "send_html",
                        lambda uid, html, plain: sent.append(plain) or (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg, "_invite_already_sent", lambda uid: False)
    monkeypatch.setattr(tg, "_mark_invited", lambda uid: invited.append(uid))
    monkeypatch.setattr(tg, "_mark_terms_sent", lambda uid: None)

    tg.send_terms(0, 555, offer_form=True)

    assert sent[0].index("Индивидуальные условия") < sent[0].index(tg.LEAD_FORM_URL)
    assert sent[0].count(tg.LEAD_FORM_URL) == 1
    assert tg.TERMS_QUESTION not in sent[0], "вопрос и CTA анкеты рядом запрещены"
    assert invited == [555]


def test_long_terms_split_the_form_and_mark_only_delivered_assets(tg, monkeypatch):
    sent, terms_marked, invited = [], [], []
    monkeypatch.setattr(tg, "terms_text", lambda: "X" * 3300)
    monkeypatch.setattr(tg, "send_html",
                        lambda uid, html, plain: sent.append(plain) or (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg, "_invite_already_sent", lambda uid: False)
    monkeypatch.setattr(tg, "_mark_terms_sent", lambda uid: terms_marked.append(uid))
    monkeypatch.setattr(tg, "_mark_invited", lambda uid: invited.append(uid))

    tg.send_terms(0, 555, offer_form=True)

    assert len(sent) == 2
    assert "X" * 3300 in sent[0] and tg.LEAD_FORM_URL not in sent[0]
    assert sent[1].count(tg.LEAD_FORM_URL) == 1
    assert terms_marked == [555] and invited == [555]


def test_oversized_terms_are_not_truncated_or_marked_as_sent(tg, monkeypatch):
    sent, terms_marked, invited = [], [], []
    monkeypatch.setattr(tg, "terms_text", lambda: "X" * 3490)
    monkeypatch.setattr(tg, "send_html",
                        lambda uid, html, plain: sent.append(plain) or (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg, "_invite_already_sent", lambda uid: False)
    monkeypatch.setattr(tg, "_mark_terms_sent", lambda uid: terms_marked.append(uid))
    monkeypatch.setattr(tg, "_mark_invited", lambda uid: invited.append(uid))

    with pytest.raises(RuntimeError, match="превышает безопасный размер"):
        tg.send_terms(0, 555, offer_form=True)

    assert sent == []
    assert terms_marked == [] and invited == []


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


def test_on_the_terms_stage_questions_are_answered_before_requisites(tg, monkeypatch):
    """С 25.07.2026 вопросы по условиям разбираются до анкеты, а на этап согласования сделка
    приходит уже готовой к реквизитам. Отвечать на оставшиеся вопросы агент обязан и здесь."""
    monkeypatch.setattr(tg, "TERMS_SENT_FIELD", "UF_CRM_TERMS")
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:S84294149",
                              "custom_fields": {"UF_CRM_TERMS": "2026-07-23"}})

    assert st["step"] == "Сбор реквизитов"
    assert "search_company_knowledge" in st["action"]
    assert "реквизиты" in st["action"].lower(), "следующий шаг назван прямо"


def test_requisites_already_collected_means_terms_are_behind(tg):
    """Старые сделки без поля-отметки не должны застрять на условиях."""
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:S84294149",
                              "custom_fields": {tg.CONTRACT_REQUISITES_FIELD: "ИНН 7704123456"}})

    assert st["step"] == "Отправка договора"


def test_turn_facts_restore_terms_dedup_from_the_crm_field(tg, monkeypatch):
    monkeypatch.setattr(tg, "TERMS_SENT_FIELD", "UF_CRM_TERMS")
    monkeypatch.setattr(tg, "load_state", lambda: {})
    deal = {
        "deal_id": 86,
        "stage_id": "C16:S84294149",
        "custom_fields": {"UF_CRM_TERMS": "2026-07-25"},
    }

    facts = tg._facts_for_turn({"id": 555}, "Какие условия?", 86, deal=deal)

    assert facts.terms_sent


def test_turn_facts_use_requisites_as_terms_evidence_when_field_is_unset(tg, monkeypatch):
    monkeypatch.setattr(tg, "TERMS_SENT_FIELD", "")
    monkeypatch.setattr(tg, "load_state", lambda: {})
    deal = {
        "deal_id": 86,
        "stage_id": "C16:S84294149",
        "custom_fields": {tg.CONTRACT_REQUISITES_FIELD: "ИНН 7704123456"},
    }

    facts = tg._facts_for_turn({"id": 555}, "Какие условия?", 86, deal=deal)

    assert facts.terms_sent


def test_requisites_remain_terms_evidence_after_explicit_field_is_configured(tg, monkeypatch):
    """Миграция поля не должна повторно отправить условия старым/идущим сделкам."""
    monkeypatch.setattr(tg, "TERMS_SENT_FIELD", "UF_CRM_TERMS")
    monkeypatch.setattr(tg, "load_state", lambda: {})
    deal = {
        "deal_id": 86,
        "stage_id": "C16:S84294149",
        "custom_fields": {tg.CONTRACT_REQUISITES_FIELD: "ИНН 7704123456"},
    }

    facts = tg._facts_for_turn({"id": 555}, "Какие условия?", 86, deal=deal)

    assert facts.terms_sent


# --- условия незнакомцу: дословно из файла (владелец, 24.07.2026) -----------------------------

def _stranger(tg, monkeypatch, answer, client_text="какие условия подключения?"):
    """Незнакомец пишет в личку; ловим, что уйдёт клиенту."""

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
                        "text": client_text})
    return box




def test_conditions_alone_do_not_force_the_form(tg, monkeypatch):
    """Интерес к условиям — ещё не согласие клиента отдавать данные через анкету."""
    box = _stranger(tg, monkeypatch, tg.TERMS_REQUEST_MARKER)

    assert tg.LEAD_FORM_URL not in box[0]








def test_stranger_rules_forbid_promises_and_article_questions(tg):
    """Живой сбой: клиенту с оборотом 200 млн агент пообещал «посмотрим экономику по артикулу»."""
    rules = tg.STRANGER_RULES.lower()

    assert "не проси артикул" in rules
    assert "посчитать экономику" in rules
    assert tg.TERMS_REQUEST_MARKER in tg.STRANGER_RULES




def test_terms_marker_rule_reaches_both_branches(tg):
    """Правило про дословные условия лежит в общих правилах тона — значит и лид, и незнакомец."""
    assert tg.TERMS_REQUEST_MARKER in tg.STYLE_RULES
    assert "не пересказывай" in tg.STYLE_RULES.lower()


# --- документ условий ОДИН раз, вопрос поверх него — людям (владелец, 24.07.2026) --------------

def _lead_turn(tg, monkeypatch, client_text, state_extra=None, answer=None,
               *, deal_fetch_error=False):
    """Ход лида воронки; возвращаем (что ушло клиенту, что унесли людям)."""
    sent, to_humans = [], []
    state = {"business": {"C1": {"user_id": 871}}, **(state_extra or {})}
    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    monkeypatch.setattr(tg, "load_state", lambda: state)
    monkeypatch.setattr(tg, "save_state", lambda s: None)
    monkeypatch.setattr(tg, "lead_deal_for_username", lambda u: 120)
    if deal_fetch_error:
        def unavailable_deal(_deal_id):
            raise RuntimeError("CRM unavailable")
        monkeypatch.setattr(tg, "_deal_for_watch", unavailable_deal)
    else:
        monkeypatch.setattr(
            tg,
            "_deal_for_watch",
            lambda deal_id: {
                "id": deal_id,
                "stage_id": "C16:S84294149",
                "custom_fields": {},
            },
        )
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










def test_crm_fetch_failure_with_empty_local_state_does_not_resend_terms(tg, monkeypatch):
    sent, to_humans = _lead_turn(
        tg, monkeypatch,
        "Какие условия подключения к ИУ?",
        deal_fetch_error=True,
    )

    assert sent and tg.TERMS_ASK_HUMAN_REPLY in sent[0]
    assert not any("Индивидуальные условия снижают комиссию" in message for message in sent)
    assert to_humans, "неизвестное CRM-состояние обязан увидеть живой менеджер"




def test_resend_detector_tells_a_repeat_request_from_a_new_question(tg):
    """Границу решает один разбор текста — проверяем обе стороны на живых формулировках."""
    assert tg._wants_terms_again("пришлите ещё раз")
    assert tg._wants_terms_again("не получил документ")
    assert tg._wants_terms_again("продублируйте условия")
    assert not tg._wants_terms_again("Какой дрр нужно держать и как происходит управление?")
    assert not tg._wants_terms_again("а какая комиссия ваша по этой программе?")




def test_question_words_are_told_apart_from_confirmations(tg):
    """Граница «вопрос / подтверждение» решается одним разбором — проверяем обе стороны."""
    assert tg._looks_like_question("Какой дрр нужно держать?")
    assert tg._looks_like_question("а что по срокам")
    assert tg._looks_like_question("сколько это стоит")
    assert not tg._looks_like_question("Все верно")
    assert not tg._looks_like_question("да")
    assert not tg._looks_like_question("Заполнил")
