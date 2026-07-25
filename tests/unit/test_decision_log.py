"""Трасса решений: по одной строке видно, ПОЧЕМУ агент поступил так (фаза 3).

Владелец 25.07.2026: «нужно аккуратно отслеживать логику». Журнал сообщений показывает, что ушло
клиенту; трасса — какое правило сработало и на каких фактах. Разбор жалобы начинается отсюда, а
не с чтения кода.
"""
from __future__ import annotations

import contextlib
import json

import decision_log
import funnel_rules as fr


class FakeCursor:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, sink):
        self.sink = sink

    def cursor(self):
        return FakeCursor(self.sink)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_db(sink):
    @contextlib.contextmanager
    def db():
        yield FakeConn(sink)
    return db


def test_decision_is_written_with_rule_and_facts():
    """Живой случай: на «Все верно» агент продолжает разговор — в трассе видно правило и факты."""
    sink = []
    decision = fr.decide(fr.Facts(uid=555, deal_id=148, stage="C16:UC_ANKETA",
                                  text="Все верно", terms_sent=True, wants_terms=True))

    decision_log.record(fake_db(sink), decision, slot="message",
                        outcome="разговор продолжен по шагу воронки")

    assert len(sink) == 1
    sql, params = sink[0]
    assert "INSERT INTO agent_decisions" in sql
    dialog_id, deal_id, slot, rule, action, origin, facts_json, outcome = params
    assert (dialog_id, deal_id, slot) == (555, 148, "message")
    assert rule == "подтверждение, а не вопрос"
    assert action == fr.CONTINUE_STEP
    assert "148" in origin, "в трассе есть причина появления правила"
    facts = json.loads(facts_json)
    assert facts["terms_sent"] is True and facts["is_question"] is False
    assert facts["stage"] == "C16:UC_ANKETA"
    assert outcome == "разговор продолжен по шагу воронки"


def test_client_text_is_not_copied_into_the_trace():
    """Текст клиента живёт в журнале переписки; в трассе — только признаки, по которым решали."""
    sink = []
    decision = fr.decide(fr.Facts(uid=1, text="секретные реквизиты ИНН 7704123456",
                                  wants_terms=True, terms_sent=True))

    decision_log.record(fake_db(sink), decision, slot="message")

    assert "7704123456" not in sink[0][1][6]


def test_silence_of_the_watcher_is_also_recorded():
    """Молчание — тоже решение: раньше «почему не прислал сверку» разбиралось по коду."""
    sink = []
    decision = fr.decide(fr.Facts(uid=7, deal_id=120, stage="C16:S84294149",
                                  anketa="Вижу анкету: …", anketa_fingerprint="a"),
                         slot="watch")

    decision_log.record(fake_db(sink), decision, slot="watch", outcome="ничего не отправлено")

    rule, action = sink[0][1][3], sink[0][1][4]
    assert action == fr.STAY_SILENT
    assert rule == "сделка ушла дальше сверки"


def test_broken_database_never_breaks_the_turn():
    """Трасса — вспомогательная вещь: её сбой не имеет права мешать разговору с клиентом."""
    @contextlib.contextmanager
    def broken():
        raise RuntimeError("база недоступна")
        yield  # pragma: no cover

    decision_log.record(broken, fr.decide(fr.Facts(uid=1)), slot="message")   # не падает


def test_migration_is_registered_so_prod_gets_the_table():
    """Таблица без регистрации в ensure_postgres не появится на проде — и трасса потеряется."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    assert (root / "database/migrations/064_agent_decisions.sql").exists()
    runner = (root / "scripts/ensure_postgres.py").read_text(encoding="utf-8")
    assert '"agent_decisions": "064_agent_decisions.sql"' in runner


def test_same_decision_is_not_written_again():
    """25.07.2026: сторож писал одинаковое «ничего не отправлять» каждую минуту — за сутки 708
    записей по двум диалогам, и лента решений в кабинете стала бесполезной."""
    decision_log._last.clear()
    sink = []
    decision = fr.decide(fr.Facts(uid=42, deal_id=120, stage="C16:S84294149",
                                  anketa="Вижу анкету: …", anketa_fingerprint="a"), slot="watch")

    for _ in range(5):
        decision_log.record(fake_db(sink), decision, slot="watch", outcome="ничего не отправлено")

    assert len(sink) == 1, "повтор того же решения в трассу не пишем"


def test_changed_decision_is_written():
    """А вот изменение решения по тому же человеку — это событие, его писать обязательно."""
    decision_log._last.clear()
    sink = []
    silent = fr.decide(fr.Facts(uid=42, anketa="Вижу анкету: …", anketa_fingerprint="a",
                                anketa_seen="a", stage="C16:UC_ANKETA"), slot="watch")
    survey = fr.decide(fr.Facts(uid=42, anketa="Вижу анкету: …", anketa_fingerprint="b",
                                stage="C16:UC_ANKETA"), slot="watch")

    decision_log.record(fake_db(sink), silent, slot="watch", outcome="ничего не отправлено")
    decision_log.record(fake_db(sink), survey, slot="watch", outcome="сверка анкеты отправлена")

    assert len(sink) == 2
    assert sink[1][1][4] == fr.SEND_SURVEY


def test_different_people_are_tracked_separately():
    decision_log._last.clear()
    sink = []
    for uid in (1, 2, 3):
        decision_log.record(fake_db(sink), fr.decide(fr.Facts(uid=uid), slot="watch"),
                            slot="watch", outcome="ничего не отправлено")

    assert len(sink) == 3, "дедуп не должен глотать решения по другим клиентам"
