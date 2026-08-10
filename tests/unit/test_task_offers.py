"""Unit tests for task_offers: decline detection and deterministic Codex fallback."""
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


def test_compose_offer_falls_back_when_codex_is_unavailable(monkeypatch):
    import app  # noqa: F401

    import task_offers as to

    monkeypatch.setattr(to, "run_quality_json", _raise)
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


def test_compose_offer_uses_codex_quality_result(monkeypatch):
    import app  # noqa: F401

    import task_offers as to

    monkeypatch.setattr(to, "run_quality_json", lambda *a, **k: {
        "agent": "agent-sklad",
        "message": "Артур, могу помочь выполнить и закрыть вам эту задачу. "
                   "Могу подготовить договор — начать? Ответьте прямо здесь — я увижу ваше сообщение.",
    })
    candidates = [
        {"slug": None, "name": "Агент Албери", "bot_id": 24, "role": "универсальный", "is_main": True},
        {"slug": "agent-sklad", "name": "Агент-юрист", "bot_id": 70, "role": "юрист", "is_main": False},
    ]
    agent, msg = to.compose_offer({"title": "Договор с подрядчиком", "description": "нужен договор"},
                                  candidates, "Артур Степанян")
    assert agent["slug"] == "agent-sklad"
    assert "Ответьте прямо здесь" in msg


def test_compose_offer_respects_explicit_no_help(monkeypatch):
    import app  # noqa: F401

    import task_offers as to

    monkeypatch.setattr(to, "run_quality_json", lambda *a, **k: {"agent": "main", "message": ""})
    candidates = [
        {"slug": None, "name": "Агент Албери", "bot_id": 24, "role": "универсальный", "is_main": True},
    ]
    _, msg = to.compose_offer({"title": "Физически отвезти коробки"}, candidates, "Артур")
    assert msg == ""


def test_compose_offer_rejects_unknown_agent_slug(monkeypatch):
    import app  # noqa: F401

    import task_offers as to

    monkeypatch.setattr(to, "run_quality_json", lambda *a, **k: {
        "agent": "invented-lawyer",
        "message": "Подготовлю юридическое заключение.",
    })
    candidates = [
        {"slug": None, "name": "Агент Албери", "bot_id": 24, "role": "универсальный", "is_main": True},
    ]
    agent, msg = to.compose_offer({"title": "Собрать отчёт"}, candidates, "Артур")

    assert agent["is_main"] is True
    assert "юридическое заключение" not in msg
    assert "могу помочь выполнить и закрыть" in msg


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


def test_post_offer_explicit_no_help_has_no_bitrix_or_db_side_effect(monkeypatch):
    import app  # noqa: F401
    import b24bot
    import task_offers as to

    candidate = {"slug": None, "name": "Agent Albery", "bot_id": 24, "is_main": True}
    monkeypatch.setattr(to, "_candidate_agents", lambda responsible_id: [candidate])
    monkeypatch.setattr(b24bot, "_b24_portal_user_directory", lambda: {16: {"name": "Arthur"}})
    monkeypatch.setattr(to, "_task_context", lambda task_id: {
        "checklist": [], "comments": [], "attach_count": 0, "task_files": [],
    })
    monkeypatch.setattr(to, "compose_offer", lambda *a, **k: (candidate, ""))
    monkeypatch.setattr(
        b24bot,
        "_b24_post_task_comment",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Bitrix write must not happen")),
    )
    monkeypatch.setattr(
        to,
        "_save_offer",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB write must not happen")),
    )

    to._post_offer(123, "physical task", "", [], 16, 22)
