from __future__ import annotations

import funnel_telegram_gateway as gateway
import funnel_workspace_store as store
import iu_client_bot as bot
from scripts import iu_form_merge_watch as watch
from shared import db


class FakeCursor:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.updates: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql: str, params=()):
        if "UPDATE iu_form_merges" in sql:
            self.updates.append(tuple(params))

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self):
        return self._cursor


def test_calculator_form_completion_confirms_and_calls_manager(monkeypatch):
    cursor = FakeCursor([{"form_deal_id": 264, "telegram_id": 555}])
    monkeypatch.setattr(db, "connect", lambda: FakeConnection(cursor))
    monkeypatch.setattr(
        store,
        "find_conversation",
        lambda **_kwargs: {"id": 311},
    )
    monkeypatch.setattr(
        store,
        "list_messages",
        lambda *_args, **_kwargs: [
            {
                "id": 10,
                "author_type": "agent",
                "metadata": {"iu_event": "calculator_discussion_unfilled"},
            }
        ],
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        gateway,
        "_reply_and_hand_over",
        lambda conversation_id, text, **kwargs: (
            calls.append((conversation_id, text, kwargs))
            or {"message": {"id": 99}}
        ),
    )

    assert watch.notify_bot_clients() == 1

    conversation_id, text, kwargs = calls[0]
    assert conversation_id == 311
    assert text == bot.CALCULATOR_FORM_RECEIVED
    assert kwargs["event"] == "calculator_form_received"
    assert kwargs["metadata"] == {
        "form_deal_id": 264,
        "calculator_origin": True,
    }
    assert kwargs["reply_markup"] == bot.remove_keyboard()
    assert cursor.updates[-1] == ("", "", 264)


def test_regular_form_completion_also_notifies_manager(monkeypatch):
    cursor = FakeCursor([{"form_deal_id": 265, "telegram_id": 556}])
    monkeypatch.setattr(db, "connect", lambda: FakeConnection(cursor))
    monkeypatch.setattr(
        store,
        "find_conversation",
        lambda **_kwargs: {"id": 312},
    )
    monkeypatch.setattr(store, "list_messages", lambda *_args, **_kwargs: [])
    calls: list[tuple] = []
    monkeypatch.setattr(
        gateway,
        "_reply_and_hand_over",
        lambda conversation_id, text, **kwargs: (
            calls.append((conversation_id, text, kwargs))
            or {"message": {"id": 100}}
        ),
    )

    assert watch.notify_bot_clients() == 1

    conversation_id, text, kwargs = calls[0]
    assert conversation_id == 312
    assert text == bot.FORM_RECEIVED
    assert kwargs["event"] == "form_received"
    assert kwargs["metadata"] == {"form_deal_id": 265}
    assert kwargs["reply_markup"] == bot.remove_keyboard()
