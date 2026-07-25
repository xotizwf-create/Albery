"""Реестр правил воронки: решение принимается по фактам, приоритет задан числом (фаза 1).

Владелец 25.07.2026: «менять схему на корню, а не фиксить каждый шаг». Правила переехали из
россыпи `if`-ов в данные — здесь проверяется, что каждое из них срабатывает на ТОМ САМОМ живом
случае, из которого родилось, и что они не перекрывают друг друга.
"""
from __future__ import annotations

import funnel_rules as fr


def facts(**kw) -> fr.Facts:
    return fr.Facts(**kw)


# --- ход по сообщению клиента ----------------------------------------------------------------

def test_first_terms_question_sends_the_document():
    d = fr.decide(facts(text="какие условия подключения к иу?", wants_terms=True))

    assert d.action == fr.SEND_TERMS
    assert d.rule == "условия ещё не отправляли"


def test_resend_request_sends_the_document_again():
    d = fr.decide(facts(text="условия не пришли, пришлите ещё раз", wants_terms=True,
                        terms_sent=True))

    assert d.action == fr.SEND_TERMS
    assert d.rule == "просят выслать условия заново"


def test_questions_on_top_of_terms_are_answered_from_sources():
    """Диалог 764181402: «Какой дрр нужно держать и как происходит управление?»

    С фазы 2 агент не молчит и не вываливает документ второй раз: он отвечает на то, что есть в
    источниках, а остальное уносит людям."""
    d = fr.decide(facts(text="Какой дрр нужно держать и как происходит управление?",
                        wants_terms=True, terms_sent=True))

    assert d.action == fr.ANSWER_QUESTIONS
    assert "764181402" in d.origin, "правило несёт причину появления"


def test_thanks_without_a_question_does_not_dump_the_document():
    """Живой случай 25.07.2026: на «Поняла, спасибо» реестр был готов выслать условия."""
    d = fr.decide(facts(text="Поняла, спасибо", wants_terms=True))

    assert d.action == fr.CONTINUE_STEP
    assert d.rule == "подтверждение без интереса к ИУ"


def test_confirmation_continues_the_conversation():
    """Александр, сделка 148: на «Все верно» ушло «Уточню это у команды»."""
    d = fr.decide(facts(text="Все верно", wants_terms=True, terms_sent=True))

    assert d.action == fr.CONTINUE_STEP
    assert d.rule == "подтверждение, а не вопрос"


def test_iu_interest_opens_a_deal():
    d = fr.decide(facts(text="Присоединение к иу"))

    assert d.action == fr.OPEN_DEAL


def test_supplier_talk_does_not_open_a_deal():
    for text in ("Приветствую", "Мы поставщик тканей, интересует закупка",
                 "Скиньте номер бухгалтера"):
        d = fr.decide(facts(text=text))
        assert d.action == fr.CONTINUE_STEP, text


def test_deal_is_not_opened_twice():
    d = fr.decide(facts(text="а какие условия?", deal_id=148))

    assert d.action != fr.OPEN_DEAL, "сделка у человека уже есть"


def test_terms_win_over_opening_a_deal():
    """Приоритет важен: спросил про условия, сделки нет — сначала условия, сделку заводит код
    отдельным действием. Иначе клиент получал бы вместо ответа тишину."""
    d = fr.decide(facts(text="какие условия?", wants_terms=True))

    assert d.action == fr.SEND_TERMS


# --- сторож анкеты ---------------------------------------------------------------------------

def test_new_anketa_triggers_the_survey():
    d = fr.decide(facts(anketa="Вижу анкету: …", anketa_fingerprint="abc",
                        stage="C16:UC_ANKETA"), slot="watch")

    assert d.action == fr.SEND_SURVEY


def test_same_anketa_is_silent():
    d = fr.decide(facts(anketa="Вижу анкету: …", anketa_fingerprint="abc", anketa_seen="abc",
                        stage="C16:UC_ANKETA"), slot="watch")

    assert d.action == fr.STAY_SILENT
    assert d.rule == "эти данные анкеты уже сверяли"


def test_refilled_anketa_is_surveyed_again():
    d = fr.decide(facts(anketa="Вижу анкету: …", anketa_fingerprint="new", anketa_seen="old",
                        stage="C16:UC_ANKETA"), slot="watch")

    assert d.action == fr.SEND_SURVEY


def test_deal_past_the_survey_stage_is_left_alone():
    d = fr.decide(facts(anketa="Вижу анкету: …", anketa_fingerprint="abc", stage="C16:NDA"),
                 slot="watch")

    assert d.action == fr.STAY_SILENT


def test_already_surveyed_before_fingerprints_is_not_spammed():
    d = fr.decide(facts(anketa="Вижу анкету: …", anketa_fingerprint="abc", legacy_surveyed=True,
                        stage="C16:UC_ANKETA"), slot="watch")

    assert d.action == fr.STAY_SILENT


def test_no_anketa_means_silence():
    d = fr.decide(facts(stage="C16:CONTACTED"), slot="watch")

    assert d.action == fr.STAY_SILENT
    assert d.rule == "анкеты ещё нет"


# --- свойства самого реестра ------------------------------------------------------------------

def test_every_rule_carries_a_reason_and_a_date():
    """Правило без причины через месяц никто не решится тронуть — и оно копится как хлам."""
    for rule in fr.RULES:
        assert rule.origin.strip(), rule.name
        assert "202" in rule.origin, f"{rule.name}: в причине должна быть дата"


def test_rule_names_are_unique():
    names = [f"{r.slot}:{r.name}" for r in fr.RULES]
    assert len(names) == len(set(names))


def test_each_slot_always_reaches_a_decision():
    """Каждый слот обязан иметь замыкающее правило: агент не должен зависать без решения."""
    for slot in ("message", "watch"):
        d = fr.decide(facts(), slot=slot)
        assert d.rule != "нет подходящего правила", slot


def test_decision_explains_itself():
    """Основа разбора: из журнала видно, что решили и почему."""
    line = fr.explain(fr.decide(facts(text="Все верно", wants_terms=True, terms_sent=True)))

    assert "решение:" in line and "правило:" in line
    assert "условия_отправлены=True" in line


# --- реестр и код не должны разъезжаться -----------------------------------------------------

def test_stages_have_a_single_source_of_truth():
    """Этапы заданы в реестре, а код отправки берёт их оттуда.

    24.07.2026 список этапов сверки жил отдельно от констант — они разошлись, и сторож анкеты
    замолчал на этапе «Анкета заполнена», который сам же и ставился склейкой."""
    import tg_agent as tg

    assert tg.STAGE_NEW is fr.STAGE_NEW
    assert tg.STAGE_CONTACTED is fr.STAGE_CONTACTED
    assert tg.STAGE_FORM_DONE is fr.STAGE_FORM_DONE
    assert fr.STAGE_FORM_DONE in fr.SURVEY_STAGES, "сверка возможна на «Анкета заполнена»"


def test_text_recognition_is_not_duplicated_in_the_agent():
    """Разбор текста должен быть один: копия регулярки — это будущее расхождение поведения."""
    import inspect

    import tg_agent as tg

    for func in (tg._iu_intent, tg._wants_terms_again, tg._looks_like_question):
        src = inspect.getsource(func)
        assert "funnel_rules." in src, f"{func.__name__} обязан спрашивать реестр"
        assert "re.compile" not in src, f"{func.__name__} завёл свою копию разбора"
