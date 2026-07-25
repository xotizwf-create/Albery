"""Сбой модели не оставляет клиента в тишине (живой случай 25.07.2026).

Провайдер отдавал 500/503, три хода упали. Клиент 377640060 не получил НИЧЕГО и написал:
«Но вы какие-то супер не торопливые..». Ошибка была записана в журнал — и всё: ни повтора, ни
людей. Здесь закреплено правильное поведение: пауза и повтор, а если не вышло — карточка живым
людям, чтобы ответил человек.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def tg(monkeypatch, tmp_path):
    import tg_agent

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"business": {"C1": {"user_id": 871}},
                                 "contacts": {"lead": {"id": 555, "username": "lead",
                                                       "name": "Пётр"}}}), encoding="utf-8")
    monkeypatch.setattr(tg_agent, "STATE_PATH", state)
    monkeypatch.setattr(tg_agent, "load_state",
                        lambda: json.loads(state.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg_agent, "save_state", lambda s: None)
    monkeypatch.setattr(tg_agent, "_MODEL_RETRY_PAUSE_S", 0)      # тесту незачем спать
    monkeypatch.setattr(tg_agent, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg_agent, "react", lambda *a, **k: None)
    monkeypatch.setattr(tg_agent, "chat_history", lambda *a, **k: "")
    monkeypatch.setattr(tg_agent, "lead_deal_for_username", lambda u: 80)
    monkeypatch.setattr(tg_agent, "funnel_step_block", lambda d, uid=None: "Шаг: вопросы")
    monkeypatch.setattr(tg_agent, "_dialog_out_watermark", lambda d: 5)
    monkeypatch.setattr(tg_agent, "_out_messages_after", lambda d, s: 0)
    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    return tg_agent


def _turn(tg, monkeypatch, answers):
    """Ход лида; answers — что делает модель на каждый вызов (строка или исключение)."""
    calls, sent, to_humans = [], [], []

    def model(prompt, session, toolsets=None):
        calls.append(1)
        value = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(tg, "hermes_answer", model)
    monkeypatch.setattr(tg, "send_html", lambda uid, html, plain: sent.append(plain) or (True, ""))
    monkeypatch.setattr(tg, "escalate_to_human",
                        lambda author, q, ctext, answered=False: to_humans.append(q))
    tg.maybe_autoreply({"business_connection_id": "C1", "chat": {"id": 555, "type": "private"},
                        "from": {"id": 555, "username": "lead", "first_name": "Пётр"},
                        "text": "Да, актуально."})
    return calls, sent, to_humans


def test_transient_failure_is_retried_and_the_client_gets_an_answer(tg, monkeypatch):
    """503 — обычно секундная история: повтор спасает разговор, и клиент даже не замечает."""
    calls, sent, to_humans = _turn(
        tg, monkeypatch, [RuntimeError("HTTP 503: Service Unavailable"), "Да, всё актуально!"])

    assert len(calls) == 2, "после сбоя обязан быть повтор"
    assert sent and "Да, всё актуально!" in sent[0]
    assert not to_humans, "повтор удался — людей не дёргаем"


def test_persistent_failure_goes_to_humans_not_to_silence(tg, monkeypatch):
    """Живой случай: модель недоступна оба раза. Клиент не должен остаться без ответа вовсе."""
    calls, sent, to_humans = _turn(
        tg, monkeypatch, [RuntimeError("HTTP 500: server error"),
                          RuntimeError("HTTP 503: Service Unavailable")])

    assert len(calls) == 2
    assert to_humans, "вопрос клиента обязан уйти живым людям"
    assert "модель недоступна" in to_humans[0]
    assert sent == [], "клиенту пустых обещаний не пишем — правило владельца от 22.07.2026"


def test_empty_model_answer_also_reaches_humans(tg, monkeypatch):
    """Модель ответила пустотой — для клиента это та же тишина."""
    calls, sent, to_humans = _turn(tg, monkeypatch, [RuntimeError("HTTP 503"), ""])

    assert to_humans and sent == []
