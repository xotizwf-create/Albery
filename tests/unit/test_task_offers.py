"""Unit tests for task_offers (offer comments on agent-created tasks): decline detection and
the deterministic no-Groq fallback of the composer. DB/network-free."""
from __future__ import annotations


def test_is_decline_short_no_only():
    import app  # noqa: F401

    import task_offers as to

    for yes in ("нет", "Нет.", "не надо", "НЕ НУЖНО", "нет, спасибо", "сам", "сама"):
        assert to.is_decline(yes), yes
    for no in ("да", "да, давай", "нет времени объяснять, ставь задачи",
               "не понял, что ты предлагаешь?", ""):
        assert not to.is_decline(no), no


def _raise(*a, **k):
    raise RuntimeError("engine down")


def test_extract_json_handles_fences_and_prose():
    import app  # noqa: F401

    import task_offers as to

    assert to._extract_json('```json\n{"agent": "main", "message": "ok"}\n```') == {"agent": "main", "message": "ok"}
    assert to._extract_json('Вот ответ: {"agent": "main", "message": "ok"}') == {"agent": "main", "message": "ok"}
    assert to._extract_json("никакого json") == {}
    assert to._extract_json("") == {}


def test_compose_offer_falls_back_without_groq(monkeypatch):
    import app  # noqa: F401

    import task_offers as to

    monkeypatch.setattr(to, "_codex_chat", _raise)
    monkeypatch.setattr(to, "_groq_chat", lambda prompt: "")
    candidates = [
        {"slug": None, "name": "Агент Албери", "bot_id": 24, "role": "универсальный", "is_main": True},
        {"slug": "agent-sklad", "name": "Агент-юрист", "bot_id": 70, "role": "юрист", "is_main": False},
    ]
    agent, msg = to.compose_offer(
        {"title": "Поставить задачи исполнителям", "description": "распределить план"},
        candidates, "Артур Степанян")
    assert agent["is_main"] is True  # fallback prefers main
    assert msg.startswith("Артур, ")
    assert "могу помочь выполнить и закрыть" in msg


def test_compose_offer_uses_groq_when_codex_down(monkeypatch):
    import json

    import app  # noqa: F401

    import task_offers as to

    monkeypatch.setattr(to, "_codex_chat", _raise)
    monkeypatch.setattr(to, "_groq_chat", lambda prompt: json.dumps(
        {"agent": "agent-sklad", "message": "Артур, могу помочь выполнить и закрыть вам эту задачу. "
                                            "Могу подготовить договор — начать? Ответьте прямо здесь — я увижу ваше сообщение."},
        ensure_ascii=False))
    candidates = [
        {"slug": None, "name": "Агент Албери", "bot_id": 24, "role": "универсальный", "is_main": True},
        {"slug": "agent-sklad", "name": "Агент-юрист", "bot_id": 70, "role": "юрист", "is_main": False},
    ]
    agent, msg = to.compose_offer({"title": "Договор с подрядчиком", "description": "нужен договор"},
                                  candidates, "Артур Степанян")
    assert agent["slug"] == "agent-sklad"
    assert "Ответьте прямо здесь" in msg


def test_schedule_offer_disabled_or_bad_ids_is_noop(monkeypatch):
    import app  # noqa: F401

    import task_offers as to

    calls = []
    monkeypatch.setattr(to, "_post_offer", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("B24_TASK_OFFER", "0")
    to.schedule_offer(1, title="t", responsible_id=16)
    monkeypatch.setenv("B24_TASK_OFFER", "1")
    to.schedule_offer("not-a-number", title="t", responsible_id=16)
    to.schedule_offer(5, title="t", responsible_id=None)
    assert calls == []
