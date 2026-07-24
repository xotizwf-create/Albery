"""Переписка с клиентом видна в ленте (таймлайне) сделки Битрикса (владелец, 24.07.2026).

Родной чат-виджет (Открытые линии) закрыт правами приложения — нет imopenlines/imconnector.
Поэтому каждое реальное сообщение клиента и агента зеркалим в ленту сделки: открыл карточку —
видишь переписку прямо в Битриксе. Служебные записи (эскалация) и недоставленное — не зеркалим.
"""
from __future__ import annotations

import types

import pytest


@pytest.fixture
def tg(monkeypatch):
    import tg_agent

    monkeypatch.setattr(tg_agent, "_MIRROR_TO_DEAL", True)
    # DB в юнит-тестах нет: вставка в журнал молча падает, но решение о зеркалировании
    # принимается ПОСЛЕ неё и от неё не зависит.
    return tg_agent


def _capture_mirror(tg, monkeypatch):
    calls = []
    monkeypatch.setattr(tg, "_mirror_to_deal",
                        lambda deal_id, direction, text: calls.append((deal_id, direction, text)))
    return calls


def test_client_and_agent_messages_are_mirrored(tg, monkeypatch):
    calls = _capture_mirror(tg, monkeypatch)

    tg.journal(tg.MANAGER_CHANNEL, 555, "in", "Хочу подключиться",
               kind="lead_chat", meta={"deal_id": 82})
    tg.journal(tg.MANAGER_CHANNEL, 555, "out", "Расскажите про оборот",
               kind="lead_chat", meta={"deal_id": 82})

    assert calls == [(82, "in", "Хочу подключиться"), (82, "out", "Расскажите про оборот")]


def test_escalation_and_undelivered_are_not_mirrored(tg, monkeypatch):
    calls = _capture_mirror(tg, monkeypatch)

    # Служебная запись эскалации — клиент её не видел.
    tg.journal(tg.MANAGER_CHANNEL, 555, "out", "вопрос унесён людям",
               kind="lead_chat", meta={"deal_id": 82, "escalated": True})
    # Недоставленное сообщение.
    tg.journal(tg.MANAGER_CHANNEL, 555, "out", "не дошло",
               kind="lead_chat", status="error", meta={"deal_id": 82})
    # Переписка владельца с самим ботом — не воронка.
    tg.journal(tg.MANAGER_CHANNEL, 555, "in", "привет", kind="bot_dm", meta={"deal_id": 82})
    # Лид-чат без сделки (незнакомец).
    tg.journal(tg.MANAGER_CHANNEL, 555, "in", "кто вы", kind="lead_chat", meta={"stranger": True})

    assert calls == [], "в ленту сделки идут только реальные сообщения по сделке"


def test_mirror_disabled_by_flag(tg, monkeypatch):
    monkeypatch.setattr(tg, "_MIRROR_TO_DEAL", False)
    posted = []
    monkeypatch.setattr(tg, "mcp_call", lambda tool, args: posted.append((tool, args)) or {})

    tg._mirror_to_deal(82, "in", "текст")

    assert posted == [], "флаг TG_MIRROR_TO_DEAL=0 полностью выключает зеркалирование"


def test_comment_text_names_the_speaker_with_bb_markup(tg):
    assert tg._deal_comment_text("in", "Здравствуйте") == "[B]Клиент:[/B] Здравствуйте"
    assert tg._deal_comment_text("out", "Добрый день") == "[B]Агент:[/B] Добрый день"


def test_mirror_calls_the_crm_tool_with_the_formatted_comment(tg, monkeypatch):
    posted = []
    monkeypatch.setattr(tg, "mcp_call", lambda tool, args: posted.append((tool, args)) or {})
    # Поток выполняем синхронно, чтобы проверить, что именно уходит в CRM.
    monkeypatch.setattr(tg, "threading",
                        types.SimpleNamespace(Thread=_InlineThread, Lock=__import__("threading").Lock))

    tg._mirror_to_deal(82, "out", "Договор отправил")

    assert posted == [("add_deal_comment",
                       {"deal_id": 82, "comment": "[B]Агент:[/B] Договор отправил"})]


def test_mirror_never_raises_when_crm_is_down(tg, monkeypatch):
    def boom(tool, args):
        raise RuntimeError("CRM недоступна")

    monkeypatch.setattr(tg, "mcp_call", boom)
    monkeypatch.setattr(tg, "threading",
                        types.SimpleNamespace(Thread=_InlineThread, Lock=__import__("threading").Lock))

    tg._mirror_to_deal(82, "in", "текст")  # не должно бросить исключение


# --- бэкфилл существующих сделок -------------------------------------------------------------

def test_backfill_posts_all_messages_then_is_idempotent(tg, monkeypatch):
    rows = [{"direction": "in", "text": "Здравствуйте"},
            {"direction": "out", "text": "Добрый день, расскажите про оборот"},
            {"direction": "in", "text": "40 млн"}]
    posted = []
    monkeypatch.setattr(tg, "_db", _fake_db(rows))
    monkeypatch.setattr(tg, "mcp_call", lambda tool, args: posted.append(args["comment"]) or {})
    state = {}
    monkeypatch.setattr(tg, "load_state", lambda: state)
    monkeypatch.setattr(tg, "save_state", lambda s: state.update(s))

    res = tg.backfill_deal_timeline(82)

    assert res["posted"] == 3 and res["total"] == 3
    assert posted[0] == "[B]Клиент:[/B] Здравствуйте"
    assert posted[2] == "[B]Клиент:[/B] 40 млн"
    assert "82" in state["mirrored_deals"]

    posted.clear()
    again = tg.backfill_deal_timeline(82)
    assert again["posted"] == 0 and "уже" in again["note"]
    assert posted == [], "повторный бэкфилл не задваивает ленту"


class _InlineThread:
    """Поток, который выполняет target сразу — чтобы тест увидел результат синхронно."""

    def __init__(self, target=None, name=None, daemon=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _fake_db(rows):
    import contextlib

    class _Cur:
        def execute(self, sql, params=None):
            self._rows = rows

        def fetchall(self):
            return list(getattr(self, "_rows", []))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

    @contextlib.contextmanager
    def fake():
        yield _Conn()

    return fake
