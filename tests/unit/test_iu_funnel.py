"""Воронка: этап двигают факты, а не слова клиента и не мнение модели.

Владелец 26.07.2026: «Новый клиент — Согласование условий — Анкета — Подписание договора —
Договор подписан — дальше как есть», и «опираясь только на факты».

Главное, что здесь закреплено: «я заполнил анкету» не двигает сделку — двигают данные анкеты в
полях сделки. Раньше этап менялся по факту доставки ответа («ответили — значит связались»), и
состояние расходилось с реальностью.
"""
from __future__ import annotations

import iu_contract
import iu_funnel as fn

NEW = fn.STAGE_NEW
TERMS = fn.STAGE_TERMS
FORM = fn.STAGE_FORM
SIGNING = fn.STAGE_SIGNING
SIGNED = fn.STAGE_SIGNED


# --- цепочка -------------------------------------------------------------------------------

def test_chain_is_exactly_what_the_owner_asked_for():
    assert [s.title for s in fn.CHAIN] == [
        "Новый клиент", "Согласование условий", "Анкета",
        "Подписание договора", "Договор подписан",
    ]


def test_removed_contacted_stage_reads_as_new():
    """Сделки, стоящие на убранном «Связались», не должны потеряться до чистки в Битриксе."""
    facts = fn.DealFacts(stage=fn.STAGE_CONTACTED_LEGACY)

    assert fn.normalize(facts.stage) == NEW
    assert fn.title_of(facts) == "Новый клиент"


# --- переходы только по фактам ---------------------------------------------------------------

def test_delivered_terms_move_the_deal():
    assert fn.next_stage(fn.DealFacts(stage=NEW, terms_delivered=True)) == TERMS


def test_undelivered_terms_move_nothing():
    assert fn.next_stage(fn.DealFacts(stage=NEW)) == ""


def test_form_stage_needs_filled_data_not_a_promise():
    """Владелец выбрал: «Анкета» = анкета ЗАПОЛНЕНА, а не отправлена ссылка."""
    sent_link_only = fn.DealFacts(stage=TERMS, terms_delivered=True)
    assert fn.next_stage(sent_link_only) == ""

    filled = fn.DealFacts(stage=TERMS, terms_delivered=True, form_filled=True)
    assert fn.next_stage(filled) == FORM


def test_contract_stages_follow_their_own_facts():
    facts = fn.DealFacts(stage=FORM, terms_delivered=True, form_filled=True,
                         contract_sent=True)
    assert fn.next_stage(facts) == SIGNING

    signed = fn.DealFacts(stage=SIGNING, terms_delivered=True, form_filled=True,
                          contract_sent=True, contract_signed=True)
    assert fn.next_stage(signed) == SIGNED


def test_deal_never_moves_backwards():
    """Клиент снова спросил про условия — это не повод вернуть сделку на согласование."""
    facts = fn.DealFacts(stage=SIGNING, terms_delivered=True, form_filled=True,
                         contract_sent=True)

    assert fn.next_stage(facts) == ""


def test_stage_outside_the_chain_is_left_alone():
    """Счёт, подключение и отказ ведут люди и другие этапы — трогать их нельзя."""
    facts = fn.DealFacts(stage="C16:UC_INVOICE_PAID", terms_delivered=True, form_filled=True)

    assert fn.next_stage(facts) == ""


def test_several_facts_at_once_jump_to_the_furthest_earned_stage():
    """Анкета пришла раньше, чем агент успел отметить условия, — берём дальний подтверждённый."""
    facts = fn.DealFacts(stage=NEW, terms_delivered=True, form_filled=True)

    assert fn.next_stage(facts) == FORM


# --- разрешённые действия ---------------------------------------------------------------------

def test_new_client_may_be_offered_terms_and_form():
    actions = fn.allowed_actions(fn.DealFacts(stage=NEW))

    assert iu_contract.SEND_TERMS in actions and iu_contract.SEND_FORM in actions
    assert iu_contract.SEND_CONTRACT not in actions


def test_terms_are_not_offered_twice():
    """Повторная отправка того же документа была одной из главных жалоб."""
    actions = fn.allowed_actions(fn.DealFacts(stage=TERMS, terms_delivered=True))

    assert iu_contract.SEND_TERMS not in actions


def test_form_is_not_offered_after_it_is_filled():
    facts = fn.DealFacts(stage=FORM, terms_delivered=True, form_filled=True)

    assert iu_contract.SEND_FORM not in fn.allowed_actions(facts)


def test_contract_becomes_available_only_after_the_form():
    before = fn.DealFacts(stage=TERMS, terms_delivered=True)
    after = fn.DealFacts(stage=FORM, terms_delivered=True, form_filled=True)

    assert iu_contract.SEND_CONTRACT not in fn.allowed_actions(before)
    assert iu_contract.SEND_CONTRACT in fn.allowed_actions(after)


def test_handoff_is_always_available():
    for stage in (NEW, TERMS, FORM, SIGNING, SIGNED):
        assert iu_contract.HANDOFF in fn.allowed_actions(fn.DealFacts(stage=stage))


def test_may_is_the_last_gate_before_execution():
    """Промпт не граница безопасности: даже попросив send_contract, модель его не получит."""
    facts = fn.DealFacts(stage=NEW)

    assert fn.may(iu_contract.SEND_CONTRACT, facts) is False
    assert fn.may(iu_contract.REPLY_ONLY, facts) is True


def test_goal_describes_the_next_step_not_a_script():
    assert "анкет" in fn.goal_of(fn.DealFacts(stage=TERMS, terms_delivered=True)).lower()


def test_model_cannot_move_the_stage_at_all():
    """В контракте хода нет поля этапа — переход считает только этот модуль."""
    assert not hasattr(iu_contract.TurnPlan, "stage_transition")
    assert "stage" not in iu_contract._ALLOWED
