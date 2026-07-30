from __future__ import annotations

import pytest

import funnel_workspace
import funnel_workspace_tools as tools


@pytest.fixture(autouse=True)
def canonical_host(monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_PUBLIC_BASE", "https://www.m4s.ru")


def conversation(**overrides):
    base = {
        "id": 12,
        "display_name": "Иван",
        "username": "ivan",
        "external_chat_id": "9001",
        "external_user_id": 9001,
        "deal_id": 188,
        "stage_id": "C16:NEW",
        "status": "open",
        "control_mode": "ai",
        "state_version": 4,
        "unread_count": 2,
        "last_message_text": "Здравствуйте",
        "last_message_at": None,
        "awaiting_reply_since": None,
    }
    base.update(overrides)
    return base


def test_link_setting_does_not_touch_request_routing(monkeypatch):
    """CANONICAL_WEB_HOST включает 301 на канонический домен и рвёт локальные вызовы
    (это уронило все MCP-эндпоинты на проде 26.07.2026). Ссылки настраиваются своей
    переменной."""
    monkeypatch.delenv("CANONICAL_WEB_HOST", raising=False)
    monkeypatch.setenv("FUNNEL_WORKSPACE_PUBLIC_BASE", "https://www.m4s.ru")

    assert funnel_workspace.conversation_url(5) == "https://www.m4s.ru/agent-funnels/5"


def test_every_conversation_has_its_own_permanent_link():
    assert funnel_workspace.conversation_url(188) == "https://www.m4s.ru/agent-funnels/188"


def test_link_stays_relative_when_the_public_host_is_unknown(monkeypatch):
    # Служебный домен MCP в напоминании увёл бы оператора не туда, поэтому чужой хост
    # не подставляется: относительный путь внутри сайта всё равно верен.
    monkeypatch.delenv("FUNNEL_WORKSPACE_PUBLIC_BASE", raising=False)
    monkeypatch.delenv("CANONICAL_WEB_HOST", raising=False)

    assert funnel_workspace.conversation_url(7) == "/agent-funnels/7"


def test_list_marks_a_long_wait_as_urgent_and_gives_the_link(monkeypatch):
    from datetime import datetime, timedelta, timezone

    waiting = datetime.now(timezone.utc) - timedelta(minutes=42)
    monkeypatch.setattr(
        tools.store,
        "list_conversations",
        lambda **kwargs: {
            "items": [conversation(awaiting_reply_since=waiting)],
            "total": 1,
        },
    )

    payload = tools.list_conversations({"urgency": "urgent"})

    item = payload["conversations"][0]
    assert item["urgency"] == "urgent"
    assert item["urgency_label"] == "Очень срочно"
    assert item["waiting_minutes"] >= 42
    assert item["url"] == "https://www.m4s.ru/agent-funnels/12"


def test_answered_conversation_is_working_not_urgent(monkeypatch):
    monkeypatch.setattr(
        tools.store,
        "list_conversations",
        lambda **kwargs: {"items": [conversation()], "total": 1},
    )

    item = tools.list_conversations({})["conversations"][0]

    assert item["urgency"] == "working"
    assert item["waiting_minutes"] is None


def test_urgent_shortcut_always_filters_by_urgency(monkeypatch):
    captured = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 0}

    monkeypatch.setattr(tools.store, "list_conversations", fake_list)

    tools.list_urgent({"limit": 5})

    assert captured["urgency"] == "urgent"
    assert captured["limit"] == 5


def test_agent_cannot_answer_while_a_human_holds_the_dialog(monkeypatch):
    """Защита от двух ответов одному клиенту: она же в очереди отправки, и инструмент
    не имеет права её обойти."""
    monkeypatch.setattr(
        tools.store,
        "get_conversation",
        lambda conversation_id: conversation(control_mode="human"),
    )
    monkeypatch.setattr(
        tools.store,
        "enqueue_outgoing_agent",
        lambda *args, **kwargs: pytest.fail("отправка запрещена, пока диалог у человека"),
    )

    with pytest.raises(tools.WorkspaceToolError, match="ведёт человек"):
        tools.reply({"conversation_id": 12, "text": "Здравствуйте"})


def test_agent_cannot_answer_in_a_closed_conversation(monkeypatch):
    monkeypatch.setattr(
        tools.store,
        "get_conversation",
        lambda conversation_id: conversation(status="closed"),
    )

    with pytest.raises(tools.WorkspaceToolError, match="закрыто"):
        tools.reply({"conversation_id": 12, "text": "Здравствуйте"})


def test_agent_reply_goes_through_the_delivery_queue(monkeypatch):
    captured = {}

    def fake_enqueue(conversation_id, **kwargs):
        captured["conversation_id"] = conversation_id
        captured.update(kwargs)
        return {
            "message": {"id": 77},
            "outbox": {"id": 5, "delivery_status": "pending"},
        }

    monkeypatch.setattr(tools.store, "get_conversation", lambda conversation_id: conversation())
    monkeypatch.setattr(tools.store, "enqueue_outgoing_agent", fake_enqueue)

    result = tools.reply({"conversation_id": 12, "text": "Готовлю условия"})

    assert result["sent"] is True
    assert result["message_id"] == 77
    assert result["url"] == "https://www.m4s.ru/agent-funnels/12"
    assert captured["text"] == "Готовлю условия"
    assert captured["expected_version"] == 4


def test_unknown_stage_is_refused_with_the_list_of_real_ones(monkeypatch):
    monkeypatch.setattr(tools.store, "get_conversation", lambda conversation_id: conversation())

    with pytest.raises(tools.WorkspaceToolError, match="C16:NEW"):
        tools.set_stage({"conversation_id": 12, "stage": "C16:НЕТ-ТАКОГО"})


def test_stage_change_is_queued_for_bitrix(monkeypatch):
    captured = {}

    def fake_change(conversation_id, **kwargs):
        captured["conversation_id"] = conversation_id
        captured.update(kwargs)
        return {"conversation": conversation(stage_id="C16:NDA"), "crm_action": {"id": 3}}

    monkeypatch.setattr(tools.store, "get_conversation", lambda conversation_id: conversation())
    monkeypatch.setattr(tools.store, "enqueue_operator_stage_change", fake_change)

    result = tools.set_stage({"conversation_id": 12, "stage": "C16:NDA"})

    assert result["stage_id"] == "C16:NDA"
    assert result["stage_label"] == "Подписание договора"
    assert captured["target_stage"] == "C16:NDA"


def test_status_change_refuses_an_unknown_value(monkeypatch):
    monkeypatch.setattr(tools.store, "get_conversation", lambda conversation_id: conversation())

    with pytest.raises(tools.WorkspaceToolError, match="Неизвестный статус"):
        tools.set_status({"conversation_id": 12, "status": "в архив"})


def test_mcp_can_return_public_bot_dialog_to_ai(monkeypatch):
    import funnel_telegram_gateway

    captured = {}
    row = conversation(source_key="telegram_bot", control_mode="human")
    monkeypatch.setattr(tools.store, "get_conversation", lambda _conversation_id: row)
    monkeypatch.setattr(
        funnel_telegram_gateway,
        "ai_allowed_in_channel",
        lambda current, telegram_id: (
            current["source_key"] == "telegram_bot" and telegram_id == 9001
        ),
    )

    def transition(conversation_id, **kwargs):
        captured.update(conversation_id=conversation_id, **kwargs)
        return conversation(source_key="telegram_bot", control_mode="ai", state_version=5)

    monkeypatch.setattr(tools.store, "transition_control", transition)

    result = tools.set_control(
        {"conversation_id": 12, "mode": "ai", "reason": "Вопрос решён"}
    )

    assert result["control"] == "отвечает ИИ"
    assert captured["mode"] == "ai"
    assert captured["actor_type"] == "agent"


def test_mcp_human_takeover_does_not_send_a_transient_message(monkeypatch):
    import funnel_telegram_gateway

    row = conversation(source_key="telegram_bot", control_mode="ai")
    monkeypatch.setattr(tools.store, "get_conversation", lambda _conversation_id: row)
    monkeypatch.setattr(
        tools.store,
        "transition_control",
        lambda *_args, **_kwargs: conversation(
            source_key="telegram_bot",
            control_mode="human",
            state_version=5,
        ),
    )
    monkeypatch.setattr(
        funnel_telegram_gateway,
        "_reply_to_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("taking a dialog must not send a service message")
        ),
    )

    result = tools.set_control({"conversation_id": 12, "mode": "human"})

    assert result["control"] == "отвечает человек"


def test_conversation_card_includes_transcript(monkeypatch):
    monkeypatch.setattr(tools.store, "get_conversation", lambda conversation_id: conversation())
    monkeypatch.setattr(
        tools.store,
        "list_messages",
        lambda conversation_id, **kwargs: [
            {
                "id": 1,
                "author_type": "client",
                "text": "Сколько стоит?",
                "delivery_status": "sent",
                "occurred_at": None,
                "author_name": None,
            }
        ],
    )

    card = tools.get_conversation({"conversation_id": 12})

    assert card["conversation"]["conversation_id"] == 12
    assert card["messages"][0]["author"] == "Клиент"
    assert card["reply_open"] is True


def test_agent_reads_and_writes_lead_notes(monkeypatch):
    """У агента есть инструмент на комментарии по лиду: он их видит и может дописать свой."""
    written = {}

    monkeypatch.setattr(tools.store, "list_lead_notes", lambda conversation_id, limit=20: [
        {"id": 3, "author_type": "operator", "author_name": "Юлия",
         "text": "Клиент про доставку", "bitrix_mirrored": True, "created_at": "2026-07-27"},
        {"id": 2, "author_type": "agent", "author_name": "ИИ-агент",
         "text": "Отправил условия", "bitrix_mirrored": False, "created_at": "2026-07-26"},
    ])
    monkeypatch.setattr(
        tools.store, "add_lead_note",
        lambda conversation_id, text, *, author_type, author_name: written.update(
            conversation_id=conversation_id, text=text, author_type=author_type) or {
                "id": 4, "author_type": author_type, "author_name": author_name,
                "text": text, "bitrix_mirrored": False, "bitrix_error": None})
    monkeypatch.setattr(tools.funnel_workspace, "mirror_lead_note_to_bitrix",
                        lambda conversation_id, note: dict(note, bitrix_mirrored=True))

    listed = tools.list_lead_notes({"conversation_id": 41})
    assert [note["author"] for note in listed["notes"]] == ["Юлия", "агент"]
    # Не доехавший до Битрикса комментарий помечен честно — иначе агент решит, что он в CRM.
    assert [note["in_bitrix"] for note in listed["notes"]] == [True, False]

    added = tools.add_lead_note({"conversation_id": 41, "text": "Договорились созвониться"})
    assert written == {"conversation_id": 41, "text": "Договорились созвониться",
                       "author_type": "agent"}
    assert added["in_bitrix"] is True
