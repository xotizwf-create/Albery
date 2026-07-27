"""Инструменты агента для рабочего окна «Работа с воронками».

Здесь живёт всё, что агент может делать с обращениями: видеть список, находить
срочные, читать переписку, отвечать, двигать этап и закрывать обращение. Логика
вынесена из ``mcp/context_server.py``: тот файл уже монолит, а эти операции трогают
живую переписку с клиентами и обязаны быть покрыты тестами отдельно.

Границы, которые здесь соблюдаются:

* Отвечать клиенту агент может только когда диалог ведёт он сам. Если оператор забрал
  разговор себе, инструмент отказывает — иначе клиент получит два ответа, и вся защита
  от двойных ответов в очереди отправки теряет смысл.
* Инструменты предназначены операторскому контуру (агент ИУ в Битриксе). Клиентский
  коннектор остаётся с нулевым набором инструментов: недоверенный текст клиента не
  должен получать доступ к чужим диалогам.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

import funnel_workspace
import funnel_workspace_store as store


MAX_LIST_LIMIT = 100
MAX_TRANSCRIPT_MESSAGES = 200

AUTHOR_LABELS = {
    "client": "Клиент",
    "agent": "ИИ-агент",
    "operator": "Оператор",
    "system": "Система",
}

CONTROL_LABELS = {
    "ai": "отвечает ИИ",
    "human": "отвечает человек",
    "paused": "ответы приостановлены",
}


class WorkspaceToolError(RuntimeError):
    """Ошибка, которую агент должен показать человеку как есть."""


def _int_arg(args: Mapping[str, Any], name: str, *, required: bool = True) -> int | None:
    value = args.get(name)
    if value in (None, ""):
        if required:
            raise WorkspaceToolError(f"Нужен параметр {name}.")
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceToolError(f"Параметр {name} должен быть числом.") from exc


def _text_arg(args: Mapping[str, Any], name: str, *, limit: int = 4096) -> str:
    value = str(args.get(name) or "").strip()
    if not value:
        raise WorkspaceToolError(f"Нужен параметр {name}.")
    return value[:limit]


def _waiting_minutes(value: Any) -> int | None:
    """Сколько минут клиент ждёт ответа; None — ответ уже дан."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - value).total_seconds() // 60))


def _conversation_brief(row: Mapping[str, Any]) -> dict[str, Any]:
    """Короткая карточка обращения — то, по чему агент ориентируется в списке."""
    waiting_minutes = _waiting_minutes(row.get("awaiting_reply_since"))
    urgent = (
        waiting_minutes is not None
        and waiting_minutes >= store.urgent_after_minutes()
    )
    if not row.get("has_answer"):
        work_state = store.WORK_STATE_NEW
    elif waiting_minutes is not None:
        work_state = store.WORK_STATE_CLIENT_WAITING
    else:
        work_state = store.WORK_STATE_WAITING_CLIENT
    name = (
        row.get("display_name")
        or (f"@{row['username']}" if row.get("username") else None)
        or f"Telegram {row.get('external_chat_id')}"
    )
    return {
        "conversation_id": int(row["id"]),
        "url": funnel_workspace.conversation_url(row["id"]),
        "client": name,
        "username": row.get("username"),
        "telegram_id": row.get("external_user_id"),
        "deal_id": row.get("deal_id"),
        "stage_id": row.get("stage_id"),
        "status": row.get("status"),
        "control": CONTROL_LABELS.get(str(row.get("control_mode")), row.get("control_mode")),
        "state": work_state,
        "state_label": store.WORK_STATE_LABELS[work_state],
        "urgent": urgent,
        "urgency": "urgent" if urgent else "working",
        "urgency_label": "Очень срочно" if urgent else store.WORK_STATE_LABELS[work_state],
        "waiting_minutes": waiting_minutes,
        "unread_count": row.get("unread_count"),
        "last_message": row.get("last_message_text"),
        "last_message_at": row.get("last_message_at"),
    }


def list_conversations(args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Список обращений с теми же фильтрами, что видит оператор в окне."""
    args = args or {}
    limit = _int_arg(args, "limit", required=False) or 20
    result = store.list_conversations(
        q=str(args.get("query") or "").strip(),
        stage=str(args.get("stage") or "").strip(),
        state=str(args.get("state") or "").strip(),
        urgency=str(args.get("urgency") or "").strip(),
        status=str(args.get("status") or "").strip(),
        limit=max(1, min(MAX_LIST_LIMIT, limit)),
    )
    items = [_conversation_brief(row) for row in result["items"]]
    return {
        "conversations": items,
        "total": result["total"],
        "urgent_after_minutes": store.urgent_after_minutes(),
        "note": (
            "urgency=urgent — клиент ждёт ответа дольше порога. Ссылка в поле url "
            "открывает именно это обращение."
        ),
    }


def list_urgent(args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Только те обращения, где клиент ждёт ответа дольше порога.

    Отдельный инструмент, а не фильтр: напоминание оператору — самый частый сценарий,
    и агент не должен каждый раз вспоминать имя фильтра.
    """
    args = dict(args or {})
    args["urgency"] = "urgent"
    payload = list_conversations(args)
    payload["note"] = (
        "Это обращения, на которые никто не ответил дольше порога. В напоминании "
        "оператору дай ссылку url и имя клиента."
    )
    return payload


def get_conversation(args: Mapping[str, Any]) -> dict[str, Any]:
    """Карточка обращения вместе с перепиской."""
    conversation_id = _int_arg(args, "conversation_id")
    limit = _int_arg(args, "messages_limit", required=False) or 30
    conversation = store.get_conversation(conversation_id)
    messages = store.list_messages(
        conversation_id,
        limit=max(1, min(MAX_TRANSCRIPT_MESSAGES, limit)),
    )
    transcript = [
        {
            "message_id": message.get("id"),
            "author": AUTHOR_LABELS.get(
                str(message.get("author_type")),
                message.get("author_type"),
            ),
            "author_name": message.get("author_name"),
            "text": message.get("text"),
            "delivery_status": message.get("delivery_status"),
            "occurred_at": message.get("occurred_at"),
        }
        for message in messages
    ]
    return {
        "conversation": _conversation_brief(conversation),
        "reply_open": conversation.get("status") in store.ACTIVE_STATUSES,
        "state_version": conversation.get("state_version"),
        "messages": transcript,
    }


def reply(args: Mapping[str, Any]) -> dict[str, Any]:
    """Ответить клиенту в диалоге.

    Отправка возможна, только когда разговор ведёт агент. Если оператор забрал диалог
    себе, инструмент отказывает: два ответа одному клиенту — худшее, что может сделать
    эта система.
    """
    conversation_id = _int_arg(args, "conversation_id")
    text = _text_arg(args, "text", limit=store.MAX_MESSAGE_LENGTH)
    conversation = store.get_conversation(conversation_id)
    control_mode = str(conversation.get("control_mode") or "")
    if control_mode != "ai":
        raise WorkspaceToolError(
            "Диалог сейчас ведёт человек — отправлять ответ от агента нельзя. "
            f"Обращение: {funnel_workspace.conversation_url(conversation_id)}"
        )
    if conversation.get("status") not in store.ACTIVE_STATUSES:
        raise WorkspaceToolError("Обращение закрыто — отвечать в нём нельзя.")
    result = store.enqueue_outgoing_agent(
        conversation_id,
        text=text,
        expected_version=conversation.get("state_version"),
        idempotency_key=f"workspace-tool:{conversation_id}:{uuid4().hex}",
        metadata={"channel": "mcp_tool"},
    )
    return {
        "sent": True,
        "message_id": result["message"]["id"],
        "delivery_status": result["outbox"]["delivery_status"],
        "url": funnel_workspace.conversation_url(conversation_id),
        "note": "Сообщение поставлено в очередь отправки; доставка занимает около секунды.",
    }


def set_stage(args: Mapping[str, Any]) -> dict[str, Any]:
    """Перевести сделку обращения на другой этап воронки."""
    conversation_id = _int_arg(args, "conversation_id")
    stage = _text_arg(args, "stage", limit=200)
    known = {item["value"]: item["label"] for item in funnel_workspace.funnel_stages()}
    if stage not in known:
        raise WorkspaceToolError(
            "Неизвестный этап. Доступны: "
            + ", ".join(f"{value} ({label})" for value, label in known.items())
        )
    conversation = store.get_conversation(conversation_id)
    result = store.enqueue_operator_stage_change(
        conversation_id,
        target_stage=stage,
        expected_version=conversation.get("state_version"),
        operator_name=str(args.get("actor_name") or "ИИ-агент"),
    )
    return {
        "conversation_id": conversation_id,
        "stage_id": result["conversation"]["stage_id"],
        "stage_label": known[stage],
        "url": funnel_workspace.conversation_url(conversation_id),
        "note": "Этап уже виден в окне; в сделку Битрикса он уходит очередью.",
    }


def set_status(args: Mapping[str, Any]) -> dict[str, Any]:
    """Закрыть обращение, вернуть в работу или пометить спамом."""
    conversation_id = _int_arg(args, "conversation_id")
    status = _text_arg(args, "status", limit=50).lower()
    if status not in store.VALID_STATUSES:
        raise WorkspaceToolError(
            "Неизвестный статус. Доступны: " + ", ".join(sorted(store.VALID_STATUSES))
        )
    conversation = store.get_conversation(conversation_id)
    updated = store.update_conversation_status(
        conversation_id,
        status=status,
        expected_version=conversation.get("state_version"),
        actor_name=str(args.get("actor_name") or "ИИ-агент"),
    )
    return {
        "conversation_id": conversation_id,
        "status": updated["status"],
        "url": funnel_workspace.conversation_url(conversation_id),
    }


def set_control(args: Mapping[str, Any]) -> dict[str, Any]:
    """Передать диалог человеку или вернуть его агенту."""
    conversation_id = _int_arg(args, "conversation_id")
    mode = _text_arg(args, "mode", limit=20).lower()
    if mode not in store.VALID_CONTROL_MODES:
        raise WorkspaceToolError(
            "Режим должен быть одним из: " + ", ".join(sorted(store.VALID_CONTROL_MODES))
        )
    conversation = store.get_conversation(conversation_id)
    updated = store.transition_control(
        conversation_id,
        mode=mode,
        expected_version=conversation.get("state_version"),
        actor_type="agent",
        actor_name=str(args.get("actor_name") or "ИИ-агент"),
        reason=str(args.get("reason") or "Переключено агентом."),
    )
    return {
        "conversation_id": conversation_id,
        "control": CONTROL_LABELS.get(
            str(updated.get("control_mode")),
            updated.get("control_mode"),
        ),
        "url": funnel_workspace.conversation_url(conversation_id),
    }
