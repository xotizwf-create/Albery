"""Исполнение решения хода: отправка, документы, кулдаун, повтор при сбое.

Три защиты здесь — те, что при переключении рантайма на новый конвейер были потеряны и найдены
падением старых тестов. Каждая оплачена разбором реального случая, поэтому закрывается своим
тестом заново.
"""
from __future__ import annotations

import iu_contract
import iu_funnel
import iu_runtime
import iu_turn

AUTHOR = {"id": 555, "first_name": "Александр", "username": "alex"}


def outcome(action=iu_contract.REPLY_ONLY, reply="Готово.", escalate=False):
    return iu_turn.Outcome(reply=reply, action=action, escalate=escalate, trace={})


class FakeTG:
    """Минимальный tg_agent: запоминает отправленное вместо похода в Telegram."""

    TELEGRAM_SAFE_TEXT_LIMIT = 3500
    LEAD_FORM_URL = "https://b24-9qcm4m.bitrix24site.ru/"
    FORM_TAIL = "\n\n———\nЗаполните анкету: {url}"
    MANAGER_CHANNEL = "albery-ai-bot"
    CONTRACT_FILE_FIELD = "UF_CRM_F84792018"

    def __init__(self, terms="Условия компании."):
        self.sent = []
        self.terms = terms
        self.marked_terms = False
        self.marked_invite = False
        self.invited_recently = False
        self.escalations = []
        self.send_ok = True

    # --- то, что вызывает iu_runtime._execute ---
    def terms_text(self):
        return self.terms

    def send_html(self, uid, body_html, plain):
        self.sent.append(plain)
        return (True, "") if self.send_ok else (False, "Bad Request")

    def as_html(self, text):
        return text

    def _strip_markup(self, text):
        return text

    def _mark_terms_sent(self, uid):
        self.marked_terms = True

    def _mark_invited(self, uid):
        self.marked_invite = True

    def _invite_already_sent(self, uid):
        return self.invited_recently

    def escalate_to_human(self, author, question, text, answered=False):
        self.escalations.append(question)

    def journal(self, *a, **kw):
        pass

    def _move_deal_stage(self, deal_id, stage, comment=""):
        pass


def run(monkeypatch, out, tg=None, facts=None):
    tg = tg or FakeTG()
    monkeypatch.setitem(__import__("sys").modules, "tg_agent", tg)
    ok = iu_runtime._execute(AUTHOR, ["вопрос"], None,
                             facts or iu_funnel.DealFacts(), out)
    return ok, tg


# --- длинный документ не обрезается -------------------------------------------------------

def test_long_terms_are_sent_as_a_separate_message(monkeypatch):
    """Telegram режет по лимиту молча: склейка отдала бы клиенту оборванный документ."""
    tg = FakeTG(terms="У" * 3490)

    ok, tg = run(monkeypatch, outcome(iu_contract.SEND_TERMS, reply="Вот условия."), tg)

    assert ok
    assert len(tg.sent) == 2, "документ обязан уйти отдельным сообщением"
    assert tg.sent[1] == "У" * 3490, "документ не должен быть обрезан"
    assert tg.marked_terms


def test_oversized_terms_are_never_truncated_nor_marked(monkeypatch):
    """Документ, не влезающий и в отдельное сообщение, режется молча — значит его не шлём."""
    tg = FakeTG(terms="У" * 4000)

    ok, tg = run(monkeypatch, outcome(iu_contract.SEND_TERMS, reply="Вот условия."), tg)

    assert not ok
    assert tg.sent == [], "оборванные условия клиенту не уходят"
    assert not tg.marked_terms
    assert tg.escalations, "документ должен уйти людям"


def test_short_terms_stay_in_one_message(monkeypatch):
    ok, tg = run(monkeypatch, outcome(iu_contract.SEND_TERMS, reply="Вот условия."))

    assert len(tg.sent) == 1
    assert "Условия компании." in tg.sent[0]


def test_undelivered_terms_are_not_marked_as_sent(monkeypatch):
    """Отметка о доставке навсегда закрывает повторную отправку — ставить её авансом нельзя."""
    tg = FakeTG()
    tg.send_ok = False

    ok, tg = run(monkeypatch, outcome(iu_contract.SEND_TERMS), tg)

    assert not ok and not tg.marked_terms


# --- кулдаун анкеты -------------------------------------------------------------------------

def test_form_is_not_sent_twice(monkeypatch):
    """Одну и ту же ссылку нельзя слать каждый ход — анкета превращалась в рассылку."""
    tg = FakeTG()
    tg.invited_recently = True

    ok, tg = run(monkeypatch, outcome(iu_contract.SEND_FORM, reply="Давайте оформим."), tg)

    assert ok
    assert iu_runtime.__name__  # sanity
    assert "Заполните анкету" not in tg.sent[0]
    assert not tg.marked_invite


def test_resend_request_bypasses_the_form_cooldown(monkeypatch):
    """«Ссылка не пришла» не должно упираться в защиту от навязчивости."""
    tg = FakeTG()
    tg.invited_recently = True
    out = iu_turn.Outcome(reply="Конечно, дублирую.", action=iu_contract.SEND_FORM,
                          trace={"resend": True})

    ok, tg = run(monkeypatch, out, tg)

    assert "Заполните анкету" in tg.sent[0]
    assert tg.marked_invite


def test_first_form_invite_goes_through(monkeypatch):
    ok, tg = run(monkeypatch, outcome(iu_contract.SEND_FORM, reply="Давайте оформим."))

    assert "Заполните анкету" in tg.sent[0]
    assert tg.marked_invite


# --- повтор при сбое модели -------------------------------------------------------------------

def test_transient_model_failure_is_retried(monkeypatch):
    """500/503 живут секунды: без повтора всплеск провайдера уводил бы клиента к людям."""
    calls = []

    class Flaky(FakeTG):
        def customer_hermes_answer(self, prompt, session):
            calls.append(session)
            if len(calls) == 1:
                raise RuntimeError("hermes turn failed rc=1: 503")
            return "ответ модели"

    monkeypatch.setitem(__import__("sys").modules, "tg_agent", Flaky())
    monkeypatch.setattr(iu_runtime, "MODEL_RETRY_PAUSE_S", 0)

    assert iu_runtime._ask_with_retry("промпт", 555) == "ответ модели"
    assert len(calls) == 2


def test_persistent_model_failure_still_raises(monkeypatch):
    """Второй сбой подряд — это уже не всплеск: ход обязан уйти человеку, а не молчать."""
    class Dead(FakeTG):
        def customer_hermes_answer(self, prompt, session):
            raise RuntimeError("hermes turn failed rc=1: 503")

    monkeypatch.setitem(__import__("sys").modules, "tg_agent", Dead())
    monkeypatch.setattr(iu_runtime, "MODEL_RETRY_PAUSE_S", 0)

    try:
        iu_runtime._ask_with_retry("промпт", 555)
    except RuntimeError:
        return
    raise AssertionError("сбой должен был подняться наверх")


# --- разговор про наш продукт ---------------------------------------------------------------

def test_deal_is_opened_only_for_our_topic():
    """Раньше «сколько стоит доставка?» заводило сделку в воронке ИУ."""
    assert not iu_runtime.about_our_product(outcome())
    assert iu_runtime.about_our_product(
        iu_turn.Outcome(reply="ok", sources=("комиссия",), trace={}))
    assert iu_runtime.about_our_product(outcome(iu_contract.SEND_TERMS))
