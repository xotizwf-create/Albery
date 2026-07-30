from __future__ import annotations

from types import SimpleNamespace

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


def test_merge_comment_is_not_added_twice_when_timeline_already_has_it():
    crm = watch.BitrixCrm.__new__(watch.BitrixCrm)
    added: list[tuple] = []
    crm.cs = SimpleNamespace(
        _crm_call=lambda _method, _args: {
            "result": [{"ID": "91", "COMMENT": "Итог склейки"}]
        }
    )
    crm._call = lambda name, args: added.append((name, args)) or {}

    result = crm.comment(284, "  Итог склейки  ")

    assert result == {"added": False, "duplicate": True, "comment_id": "91"}
    assert added == []


def test_merge_comment_is_added_once_when_timeline_has_no_exact_match():
    crm = watch.BitrixCrm.__new__(watch.BitrixCrm)
    added: list[tuple] = []
    crm.cs = SimpleNamespace(
        _crm_call=lambda _method, _args: {
            "result": [{"ID": "90", "COMMENT": "Другой итог"}]
        }
    )
    crm._call = lambda name, args: (
        added.append((name, args))
        or {"added": True, "comment_id": "92"}
    )

    result = crm.comment(284, "Итог склейки")

    assert result == {"added": True, "comment_id": "92"}
    assert added == [
        (
            "add_deal_comment",
            {"deal_id": 284, "comment": "Итог склейки"},
        )
    ]


def test_calculator_form_completion_asks_before_calling_manager(monkeypatch):
    cursor = FakeCursor(
        [{"form_deal_id": 264, "target_deal_id": 284, "telegram_id": 555}]
    )
    monkeypatch.setattr(db, "connect", lambda: FakeConnection(cursor))
    monkeypatch.setattr(
        store,
        "find_conversation",
        lambda **_kwargs: {"id": 311},
    )
    monkeypatch.setattr(
        store,
        "get_conversation",
        lambda _conversation_id: {"display_name": "Александр Никитенко"},
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
        "_reply_to_client",
        lambda conversation_id, text, **kwargs: (
            calls.append((conversation_id, text, kwargs))
            or {"message": {"id": 99}}
        ),
    )

    assert watch.notify_bot_clients() == 1

    conversation_id, text, kwargs = calls[0]
    assert conversation_id == 311
    assert text == bot.CALCULATOR_FORM_RECEIVED
    assert kwargs["metadata"]["iu_event"] == "calculator_form_received"
    assert kwargs["metadata"]["manager_notification_form_deal_id"] == 284
    assert kwargs["metadata"]["form_deal_id"] == 264
    assert kwargs["metadata"]["calculator_origin"] is True
    assert kwargs["metadata"]["form_questions_pending"] is True
    assert "notify_manager_after_delivery" not in kwargs["metadata"]
    assert "escalate_after_delivery" not in kwargs["metadata"]
    assert kwargs["reply_markup"] == bot.form_questions_menu()
    assert cursor.updates[-1] == ("", "", 264)


def test_regular_form_completion_also_asks_before_calling_manager(monkeypatch):
    cursor = FakeCursor(
        [{"form_deal_id": 265, "target_deal_id": 285, "telegram_id": 556}]
    )
    monkeypatch.setattr(db, "connect", lambda: FakeConnection(cursor))
    monkeypatch.setattr(
        store,
        "find_conversation",
        lambda **_kwargs: {"id": 312},
    )
    monkeypatch.setattr(
        store,
        "get_conversation",
        lambda _conversation_id: {"display_name": "Александр Никитенко"},
    )
    monkeypatch.setattr(store, "list_messages", lambda *_args, **_kwargs: [])
    calls: list[tuple] = []
    monkeypatch.setattr(
        gateway,
        "_reply_to_client",
        lambda conversation_id, text, **kwargs: (
            calls.append((conversation_id, text, kwargs))
            or {"message": {"id": 100}}
        ),
    )

    assert watch.notify_bot_clients() == 1

    conversation_id, text, kwargs = calls[0]
    assert conversation_id == 312
    assert text == bot.FORM_RECEIVED
    assert kwargs["metadata"]["iu_event"] == "form_received"
    assert kwargs["metadata"]["manager_notification_form_deal_id"] == 285
    assert kwargs["metadata"]["form_deal_id"] == 265
    assert kwargs["metadata"]["form_questions_pending"] is True
    assert "notify_manager_after_delivery" not in kwargs["metadata"]
    assert "escalate_after_delivery" not in kwargs["metadata"]
    assert kwargs["reply_markup"] == bot.form_questions_menu()
    assert cursor.updates[-1] == ("", "", 265)
