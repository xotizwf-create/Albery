"""Durable Telegram Business transport for the funnel operator workspace.

There is exactly one Telegram update consumer: ``albery-tg.service``.  This module is loaded by
that process and adds a bounded set of durable workers around it:

* raw Bot API updates are committed before the polling offset advances and split into
  strictly ordered Business traffic plus an independent owner-bot lane;
* customer turns are debounced in PostgreSQL and answered by the existing Albery ИУ runtime;
* operator/agent replies leave through one transactional outbox.
* confirmed deliveries enqueue separately leased, bounded CRM actions.

The web process never calls Telegram.  That single-owner rule avoids competing ``getUpdates``
consumers, preserves message order and makes every visible reply auditable in the same journal.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

log = logging.getLogger("funnel-telegram-gateway")

SOURCE_KEY = "telegram"
AGENT_NAME = "Агент по работе с ИУ"
_TEXT_LIMIT = 3500

_threads_lock = threading.Lock()
_threads: list[threading.Thread] = []
_stop_event = threading.Event()
_wake_event = threading.Event()
_worker_prefix = (
    f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
)


def _store():
    import funnel_workspace_store

    return funnel_workspace_store


def enabled() -> bool:
    """Whether Telegram traffic is routed into the custom workspace."""

    return _store().enabled()


def ai_enabled() -> bool:
    return str(os.getenv("FUNNEL_WORKSPACE_AI_ENABLED", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ai_allow_ids() -> set[int] | None:
    """Test rollout allowlist; empty means nobody, explicit ``*`` means everyone."""

    raw = str(os.getenv("FUNNEL_WORKSPACE_AI_ALLOW_IDS", "")).strip()
    if raw == "*":
        return None
    if not raw:
        return set()
    result: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        value = part.strip()
        if value.isdigit() and int(value) > 0:
            result.add(int(value))
    # A malformed allowlist must fail closed, never become "all".
    return result


def ai_allowed(telegram_user_id: Any) -> bool:
    if not ai_enabled():
        return False
    try:
        user_id = int(telegram_user_id)
    except (TypeError, ValueError):
        return False
    allowed = ai_allow_ids()
    return allowed is None or user_id in allowed


def telegram_connected() -> bool:
    """Current Telegram Business right, read from the sole transport's durable state."""

    try:
        import tg_agent

        connections = (tg_agent.load_state().get("business") or {}).values()
    except Exception:  # noqa: BLE001
        return False
    return any(
        info
        and info.get("enabled") is not False
        and info.get("can_reply") is not False
        for info in connections
    )


def capture_poll_update(update: Mapping[str, Any]) -> dict[str, Any]:
    """Commit one Bot API update before ``tg_agent`` persists the next offset."""

    update_id = update.get("update_id")
    if update_id is None:
        raise ValueError("Telegram update has no update_id")
    captured = _store().capture_update(
        external_update_id=str(update_id),
        payload=dict(update),
        source_key=SOURCE_KEY,
    )
    _wake_event.set()
    return captured


def start_workers() -> list[threading.Thread]:
    """Start idempotent daemon workers inside the sole Telegram service."""

    if not enabled():
        return []
    with _threads_lock:
        if any(thread.is_alive() for thread in _threads):
            return list(_threads)
        _stop_event.clear()
        _wake_event.clear()
        store = _store()
        for recover in (
            store.recover_updates,
            store.recover_ai_jobs,
            store.recover_outbox,
            store.recover_crm_actions,
        ):
            try:
                recover()
            except Exception:  # noqa: BLE001 - loops will retry after migrations/connectivity recover
                log.warning("workspace recovery failed", exc_info=True)
        specs = (
            ("business-updates", _update_loop),
            ("bot-updates", _bot_update_loop),
            ("ai", _ai_loop),
            ("outbox", _outbox_loop),
            ("crm-actions", _crm_action_loop),
            ("maintenance", _maintenance_loop),
        )
        _threads[:] = []
        for suffix, target in specs:
            thread = threading.Thread(
                target=target,
                daemon=True,
                name=f"funnel-{suffix}",
            )
            thread.start()
            _threads.append(thread)
        return list(_threads)


def stop_workers(timeout: float = 3.0) -> None:
    """Test/shutdown helper; systemd normally terminates the whole process."""

    _stop_event.set()
    _wake_event.set()
    deadline = time.monotonic() + max(0.0, timeout)
    for thread in list(_threads):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(remaining)


def _loop_wait(seconds: float) -> None:
    _wake_event.wait(max(0.05, seconds))
    _wake_event.clear()


def _poll_seconds(name: str, default: float) -> float:
    try:
        return max(0.1, min(60.0, float(os.getenv(name, str(default)) or default)))
    except (TypeError, ValueError):
        return default


def _update_loop() -> None:
    worker_id = f"{_worker_prefix}-business-updates"
    while not _stop_event.is_set():
        try:
            count = process_updates_once(worker_id=worker_id)
        except Exception:  # noqa: BLE001 - keep the transport alive during transient DB failures
            log.exception("workspace update worker failed")
            count = 0
        if not count:
            _loop_wait(0.5)


def _bot_update_loop() -> None:
    worker_id = f"{_worker_prefix}-bot-updates"
    while not _stop_event.is_set():
        try:
            count = process_bot_updates_once(worker_id=worker_id)
        except Exception:  # noqa: BLE001 - keep Business ingestion independent
            log.exception("workspace bot update worker failed")
            count = 0
        if not count:
            _loop_wait(0.5)


def _ai_loop() -> None:
    worker_id = f"{_worker_prefix}-ai"
    while not _stop_event.is_set():
        count = 0
        if ai_enabled():
            try:
                count = process_ai_jobs_once(worker_id=worker_id)
            except Exception:  # noqa: BLE001
                log.exception("workspace AI worker failed")
        if not count:
            _loop_wait(_poll_seconds("FUNNEL_WORKSPACE_JOB_POLL_SECONDS", 1.0))


def _outbox_loop() -> None:
    worker_id = f"{_worker_prefix}-outbox"
    while not _stop_event.is_set():
        try:
            count = process_outbox_once(worker_id=worker_id)
        except Exception:  # noqa: BLE001
            log.exception("workspace outbox worker failed")
            count = 0
        if not count:
            _loop_wait(_poll_seconds("FUNNEL_WORKSPACE_OUTBOX_POLL_SECONDS", 1.0))


def _crm_action_loop() -> None:
    worker_id = f"{_worker_prefix}-crm-actions"
    while not _stop_event.is_set():
        try:
            count = process_crm_actions_once(worker_id=worker_id)
        except Exception:  # noqa: BLE001
            log.exception("workspace CRM action worker failed")
            count = 0
        if not count:
            _loop_wait(_poll_seconds("FUNNEL_WORKSPACE_CRM_POLL_SECONDS", 2.0))


def _maintenance_loop() -> None:
    last_crm = 0.0
    last_stage_sync = 0.0
    last_retention = 0.0
    while not _stop_event.is_set():
        now = time.monotonic()
        try:
            store = _store()
            released = store.release_expired_human_leases(limit=100)
            for conversation in released:
                if (
                    conversation.get("control_mode") == "ai"
                    and not ai_allowed(conversation.get("external_user_id"))
                ):
                    try:
                        store.transition_control(
                            conversation["id"],
                            mode="paused",
                            expected_version=conversation["state_version"],
                            actor_type="system",
                            actor_name="Система",
                            reason="ИИ не включён для этого Telegram ID в тестовом контуре.",
                        )
                    except store.WorkspaceConflictError:
                        pass
            store.expire_reply_windows(limit=500)
            if now - last_crm >= 30:
                sync_missing_crm_deals_once(limit=50)
                last_crm = now
            if now - last_stage_sync >= 60:
                sync_conversation_stages_once(limit=50)
                last_stage_sync = now
            if now - last_retention >= 86_400:
                store.retention_cleanup()
                last_retention = now
        except Exception:  # noqa: BLE001
            log.exception("workspace maintenance failed")
        _loop_wait(1.0)


def _process_update_lane_once(*, worker_id: str, lane: str) -> int:
    store = _store()
    rows = store.claim_updates(
        worker_id=worker_id,
        lane=lane,
        source_key=SOURCE_KEY,
        limit=1,
        lease_seconds=90,
    )
    for row in rows:
        try:
            conversation_id, message_id = route_captured_update(
                row.get("payload") or {},
                provider_update_id=_as_int(row.get("external_update_id")),
            )
            store.complete_update(
                row["id"],
                worker_id=worker_id,
                conversation_id=conversation_id,
                message_id=message_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "workspace %s update %s failed: %s",
                lane,
                row.get("id"),
                _safe_error(exc),
            )
            store.retry_update(
                row["id"],
                worker_id=worker_id,
                error=_safe_error(exc),
                delay_seconds=min(300, 2 ** min(int(row.get("attempts") or 1), 8)),
                max_attempts=10,
            )
    return len(rows)


def process_updates_once(*, worker_id: str, limit: int = 25) -> int:
    # A later edit/delete/business-connection update must never overtake an
    # earlier Business update, including a future retry. AI work and owner bot
    # commands are handled outside this ordered lane.
    del limit
    return _process_update_lane_once(worker_id=worker_id, lane="business")


def process_bot_updates_once(*, worker_id: str, limit: int = 25) -> int:
    del limit
    return _process_update_lane_once(worker_id=worker_id, lane="bot")


def route_captured_update(
    update: Mapping[str, Any],
    *,
    provider_update_id: int | None = None,
) -> tuple[int | None, int | None]:
    """Handle one already-durable Telegram update."""

    import tg_agent

    if update.get("message"):
        # Direct bot messages are outside the lead inbox. Only the owner may use
        # this internal channel in workspace mode; strangers are silently
        # ignored. The existing bounded tg_agent pool keeps even a long owner
        # Hermes turn from blocking ordered Telegram Business ingestion.
        message = dict(update["message"])
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if chat.get("type") == "private" and tg_agent.is_owner(sender):
            tg_agent._workers.submit(
                tg_agent._handle_update_safely,
                {"message": message},
            )
        return None, None
    if update.get("business_connection"):
        tg_agent.handle_business_connection(dict(update["business_connection"]))
        return None, None
    if update.get("business_message"):
        return ingest_business_message(
            dict(update["business_message"]),
            provider_update_id=provider_update_id,
        )
    if update.get("edited_business_message"):
        return ingest_business_message(
            dict(update["edited_business_message"]),
            provider_update_id=provider_update_id,
            is_edit=True,
        )
    if update.get("deleted_business_messages"):
        deleted = dict(update["deleted_business_messages"])
        chat = dict(deleted.get("chat") or {})
        chat_id = chat.get("id")
        connection_id = str(deleted.get("business_connection_id") or "").strip()
        if chat_id is None or not connection_id:
            raise ValueError("Deleted Business messages lack chat/connection identity")
        result = _store().tombstone_business_messages(
            external_chat_id=str(chat_id),
            external_message_ids=list(deleted.get("message_ids") or []),
            business_connection_id=connection_id,
            source_key=SOURCE_KEY,
            provider_update_id=provider_update_id,
        )
        conversation = result.get("conversation") or {}
        return _as_int(conversation.get("id")), _as_int(result.get("message_id"))
    return None, None


def ingest_business_message(
    message: Mapping[str, Any],
    *,
    provider_update_id: int | None = None,
    is_edit: bool = False,
) -> tuple[int | None, int | None]:
    """Put every Telegram Business private message into the shared conversation stream."""

    import tg_agent

    chat = dict(message.get("chat") or {})
    if str(chat.get("type") or "private") != "private":
        return None, None
    chat_id = chat.get("id")
    external_message_id = message.get("message_id")
    connection_id = str(message.get("business_connection_id") or "").strip()
    if chat_id is None or external_message_id is None or not connection_id:
        raise ValueError("Business message lacks chat/message/connection identity")

    sender = dict(message.get("from") or {})
    sent_via_business_bot = bool(message.get("sender_business_bot"))
    owner_id = tg_agent._business_owner_id(connection_id)
    sender_id = _as_int(sender.get("id"))
    if not sent_via_business_bot and owner_id is None:
        # Without the owner bound to this exact connection we cannot safely tell
        # an operator reply from a customer message.  Keep the raw update leased
        # for retry instead of accidentally prompting the AI on our own message.
        raise RuntimeError("Telegram Business owner is unknown for this connection")
    outgoing = sent_via_business_bot or bool(
        owner_id is not None and sender_id == int(owner_id)
    )
    customer = chat if outgoing else sender
    customer_id = _as_int(customer.get("id")) or _as_int(chat_id)
    schedule_ai = not outgoing and ai_allowed(customer_id)
    display_name = _display_name(customer) or _display_name(chat) or f"Telegram {chat_id}"
    if sent_via_business_bot:
        author_type = "agent"
        author_name = AGENT_NAME
    elif outgoing:
        author_type = "operator"
        author_name = _display_name(sender) or "Оператор Telegram"
    else:
        author_type = "client"
        author_name = display_name
    result = _store().ingest_business_message(
        external_chat_id=str(chat_id),
        external_message_id=str(external_message_id),
        text=telegram_message_text(message),
        author_type=author_type,
        source_key=SOURCE_KEY,
        business_connection_id=connection_id,
        external_user_id=customer_id,
        username=customer.get("username") or chat.get("username"),
        display_name=display_name,
        author_name=author_name,
        provider_update_id=provider_update_id,
        occurred_at=_message_datetime(message),
        metadata={
            "telegram_chat_type": str(chat.get("type") or "private"),
            "telegram_media_type": telegram_media_type(message),
            **telegram_media_metadata(message),
            "sent_via_business_bot": sent_via_business_bot,
            "telegram_edited": bool(is_edit),
            "telegram_edit_date": _as_int(message.get("edit_date")),
        },
        schedule_ai=schedule_ai,
        is_edit=is_edit,
    )
    conversation = dict(result["conversation"])
    stored_message = dict(result["message"])

    # Feature-on/AI-off and test allowlists must never leave a misleading active AI mode.
    if (
        not outgoing
        and not schedule_ai
        and conversation.get("control_mode") == "ai"
    ):
        conversation = _pause_disallowed_conversation(_store(), conversation)

    return int(conversation["id"]), int(stored_message["id"])


def _pause_disallowed_conversation(store: Any, conversation: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed even when a duplicate update races another state transition."""

    current = dict(conversation)
    for _attempt in range(3):
        if current.get("control_mode") != "ai":
            return current
        try:
            return store.transition_control(
                current["id"],
                mode="paused",
                expected_version=current["state_version"],
                actor_type="system",
                actor_name="Система",
                reason="ИИ не включён для этого Telegram ID в тестовом контуре.",
            )
        except store.WorkspaceConflictError:
            current = dict(store.get_conversation(current["id"]))
    if current.get("control_mode") == "ai":
        raise RuntimeError("Could not pause a non-allowlisted conversation")
    return current


def telegram_media_type(message: Mapping[str, Any]) -> str:
    for key in (
        "photo",
        "video",
        "video_note",
        "voice",
        "audio",
        "document",
        "sticker",
        "animation",
        "contact",
        "location",
        "venue",
        "poll",
    ):
        if message.get(key):
            return key
    return "text"


def telegram_media_metadata(message: Mapping[str, Any]) -> dict[str, Any]:
    """Keep provider identifiers needed for later download/UI after raw retention."""

    media_type = telegram_media_type(message)
    if media_type == "text":
        return {}
    payload: Mapping[str, Any]
    if media_type == "photo":
        photos = [
            item
            for item in list(message.get("photo") or [])
            if isinstance(item, Mapping)
        ]
        if not photos:
            return {}
        payload = max(
            photos,
            key=lambda item: (
                int(item.get("width") or 0) * int(item.get("height") or 0),
                int(item.get("file_size") or 0),
            ),
        )
    else:
        raw_payload = message.get(media_type)
        if not isinstance(raw_payload, Mapping):
            return {}
        payload = raw_payload
    media = {
        key: payload.get(key)
        for key in (
            "file_id",
            "file_unique_id",
            "file_name",
            "mime_type",
            "file_size",
            "width",
            "height",
            "duration",
        )
        if payload.get(key) is not None
    }
    return {"telegram_media": media} if media else {}


def telegram_message_text(message: Mapping[str, Any]) -> str:
    text = str(message.get("text") or message.get("caption") or "").strip()
    if text:
        return text[:100_000]
    media = telegram_media_type(message)
    labels = {
        "photo": "Фото",
        "video": "Видео",
        "video_note": "Видеосообщение",
        "voice": "Голосовое сообщение",
        "audio": "Аудио",
        "document": "Документ",
        "sticker": "Стикер",
        "animation": "Анимация",
        "contact": "Контакт",
        "location": "Геолокация",
        "venue": "Место",
        "poll": "Опрос",
    }
    label = labels.get(media, "Сообщение без текста")
    if media == "document":
        filename = str((message.get("document") or {}).get("file_name") or "").strip()
        if filename:
            label = f"{label}: {filename}"
    return f"[{label}]"


def _display_name(entity: Mapping[str, Any]) -> str:
    return " ".join(
        str(entity.get(key) or "").strip()
        for key in ("first_name", "last_name", "title")
        if str(entity.get(key) or "").strip()
    )[:300]


def _message_datetime(message: Mapping[str, Any]) -> datetime:
    try:
        return datetime.fromtimestamp(int(message.get("date")), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def sync_missing_crm_deals_once(*, limit: int = 50) -> int:
    """Queue bounded CRM-link repairs without doing network I/O in update routing."""

    return _store().backfill_missing_deal_actions(
        limit=min(250, max(1, limit)),
    )


#: Ответы Telegram, означающие «бот не знает этого собеседника». Для бизнес-подключения
#: это норма: доступ к человеку появляется только после его сообщения в аккаунт.
_PEER_UNKNOWN_MARKERS = (
    "peer_id_invalid",
    "chat not found",
    "user not found",
    "bot can't initiate conversation",
    "bot was blocked",
)


def _peer_unknown_to_bot(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _PEER_UNKNOWN_MARKERS)


def _send_as_manager_account(outbox: Mapping[str, Any], bot_error: Exception) -> str | None:
    """Отправить сообщение от аккаунта менеджера, когда бот не может.

    Сессия аккаунта — не запасной канал «на всякий случай», а единственный способ
    написать человеку, который ещё не обращался в бизнес-аккаунт. Если сессии нет,
    поднимаем понятную ошибку: оператор должен видеть причину, а не «Bad Request».
    """
    import tg_userbot

    if not tg_userbot.session_ready():
        raise RuntimeError(_no_peer_access_message(bot_error))
    message_id = tg_userbot.send_message(
        int(outbox["external_chat_id"]),
        str(outbox.get("text") or "")[:4096],
    )
    log.info(
        "outbox %s delivered through the manager account (bot has no access to the peer)",
        outbox.get("id"),
    )
    return str(message_id)


def edit_delivered_message(payload: Mapping[str, Any]) -> str:
    """Заменить текст уже доставленного сообщения в Telegram.

    Возвращает, чем именно правка была сделана: ботом или аккаунтом менеджера. Порядок
    важен — сначала Telegram, потом наша база: иначе оператор увидит новый текст там,
    где у клиента остался старый.
    """
    import tg_agent

    provider_message_id = payload.get("provider_message_id")
    if not provider_message_id:
        raise RuntimeError(
            "У сообщения нет идентификатора в Telegram — менять нечего. "
            "Так бывает у перенесённой истории: она не отправлялась через эту систему."
        )
    try:
        tg_agent.api(
            "editMessageText",
            http_timeout=30,
            business_connection_id=payload["business_connection_id"],
            chat_id=int(payload["external_chat_id"]),
            message_id=int(provider_message_id),
            text=str(payload["text"])[:4096],
        )
        return "bot"
    except RuntimeError as exc:
        if not _peer_unknown_to_bot(exc):
            raise
        import tg_userbot

        if not tg_userbot.session_ready():
            raise RuntimeError(_no_peer_access_message(exc)) from exc
        tg_userbot.edit_message(
            int(payload["external_chat_id"]),
            int(provider_message_id),
            str(payload["text"])[:4096],
        )
        return "manager_account"


def delete_delivered_message(payload: Mapping[str, Any]) -> str:
    """Удалить сообщение у обеих сторон.

    Бизнес-метод Telegram умеет удалять и наши сообщения, и сообщения клиента; обычный
    deleteMessage — запасной путь для чатов вне бизнес-подключения.
    """
    import tg_agent

    provider_message_id = payload.get("provider_message_id")
    if not provider_message_id:
        # Локальная запись всё равно помечена удалённой — но честно скажем, что в
        # Telegram сообщения не тронули.
        return "local_only"
    try:
        tg_agent.api(
            "deleteBusinessMessages",
            http_timeout=30,
            business_connection_id=payload["business_connection_id"],
            message_ids=[int(provider_message_id)],
        )
        return "bot"
    except RuntimeError as exc:
        if _peer_unknown_to_bot(exc):
            import tg_userbot

            if not tg_userbot.session_ready():
                raise RuntimeError(_no_peer_access_message(exc)) from exc
            tg_userbot.delete_message(
                int(payload["external_chat_id"]),
                int(provider_message_id),
            )
            return "manager_account"
        tg_agent.api(
            "deleteMessage",
            http_timeout=30,
            chat_id=int(payload["external_chat_id"]),
            message_id=int(provider_message_id),
        )
        return "bot"


def _no_peer_access_message(error: Exception) -> str:
    return (
        "Telegram не даёт боту доступ к этому собеседнику: он ещё не писал в "
        "бизнес-аккаунт после подключения бота. Действие от аккаунта менеджера "
        "недоступно — не настроена MTProto-сессия (TG_API_ID/TG_API_HASH и вход по "
        f"коду). Исходная ошибка Telegram: {error}"
    )


def _deal_is_gone(error: Exception) -> bool:
    """Отличить «сделки больше нет» от временной недоступности Битрикса.

    Снимать связь можно только по первому: на сетевой ошибке или отказе доступа связь
    обязана остаться, иначе один сбой портала отвяжет все сделки разом.
    """
    text = str(error).lower()
    return "not found" in text and "crm.deal.get" in text


def sync_conversation_stages_once(*, limit: int = 50) -> int:
    """Догнать этап сделки, который подвинули на стороне Битрикса.

    Оператор видит этап воронки ИУ как статус обращения, а двигать сделку могут и
    люди в CRM, и наш собственный конвейер. Читаем только чтение сделки: ни одного
    записывающего вызова, поэтому ошибка Битрикса не может испортить состояние.
    """

    store = _store()
    import funnel_workspace_crm as crm

    updated = 0
    for row in store.conversations_for_stage_sync(limit=limit):
        deal_id = row.get("deal_id")
        if not deal_id:
            continue
        try:
            stage = crm.read_deal_stage(deal_id)
        except Exception as exc:  # noqa: BLE001
            if _deal_is_gone(exc):
                # Сделку удалили в Битриксе. Мёртвую ссылку снимаем: иначе оператор
                # видит этап несуществующей сделки, а эта ошибка сыплется каждую минуту.
                log.warning(
                    "deal %s is gone in Bitrix; unlinking conversation %s",
                    deal_id,
                    row.get("id"),
                )
                store.unlink_conversation_deal(row["id"])
                updated += 1
                continue
            log.exception("stage sync failed for conversation %s", row.get("id"))
            continue
        if not stage or stage == str(row.get("stage_id") or ""):
            continue
        store.update_crm_link(row["id"], stage_id=stage)
        updated += 1
    return updated


@dataclass(frozen=True)
class DialogTurn:
    texts: tuple[str, ...]
    history: str


def dialog_turn(
    messages: list[Mapping[str, Any]],
    *,
    trigger_message_id: int | None,
    history_limit: int = 16,
) -> DialogTurn:
    """Split the latest contiguous client batch from the preceding visible history."""

    usable = [
        dict(item)
        for item in messages
        if (
            item.get("author_type") == "client"
            or str(item.get("delivery_status") or "sent") == "sent"
        )
        and str(item.get("text") or "").strip()
        and (trigger_message_id is None or int(item.get("id") or 0) <= trigger_message_id)
        and not (
            isinstance(item.get("metadata"), Mapping)
            and bool(item["metadata"].get("telegram_deleted"))
        )
    ]
    batch_start = len(usable)
    while batch_start > 0 and usable[batch_start - 1].get("author_type") == "client":
        batch_start -= 1
    batch = usable[batch_start:]
    if not batch and trigger_message_id is not None:
        batch = [
            item
            for item in usable
            if int(item.get("id") or 0) == int(trigger_message_id)
            and item.get("author_type") == "client"
        ]
        if batch:
            batch_start = usable.index(batch[0])
    texts = tuple(str(item.get("text") or "").strip() for item in batch)

    labels = {
        "client": "Клиент",
        "agent": "Агент",
        "operator": "Менеджер",
    }
    history_lines: list[str] = []
    for item in usable[:batch_start][-max(1, history_limit):]:
        label = labels.get(str(item.get("author_type") or ""))
        if not label:
            continue
        history_lines.append(f"{label}: {str(item.get('text') or '').strip()}")
    return DialogTurn(texts=texts, history="\n".join(history_lines)[-4000:])


@dataclass(frozen=True)
class PreparedReply:
    text: str
    metadata: dict[str, Any]
    escalate: bool
    escalation_reason: str


def prepare_reply(
    outcome: Any,
    *,
    telegram_user_id: int,
    facts: Any = None,
) -> PreparedReply:
    """Turn an ИУ decision into one atomic Telegram text for the durable outbox."""

    import iu_contract
    import tg_agent

    body = tg_agent._strip_markup(str(outcome.reply or "").strip())
    action = str(outcome.action or iu_contract.REPLY_ONLY)
    escalate = bool(outcome.escalate)
    reason = str(outcome.reason or "").strip()
    asset = ""

    if action == iu_contract.SEND_TERMS:
        try:
            terms = tg_agent._strip_markup(tg_agent.terms_text())
            combined = f"{body}\n\n{terms}".strip()
            if len(combined) > _TEXT_LIMIT:
                raise ValueError("документ условий не помещается в одно безопасное сообщение")
            body = combined
            asset = "terms"
        except Exception as exc:  # noqa: BLE001
            body = "Уточню это у команды и вернусь с ответом."
            escalate = True
            reason = f"Не удалось безопасно подготовить условия: {_safe_error(exc)}"
    elif action == iu_contract.SEND_FORM:
        resend = bool((outcome.trace or {}).get("resend"))
        if (
            tg_agent.LEAD_FORM_URL
            and (resend or not tg_agent._invite_already_sent(telegram_user_id))
        ):
            tail = tg_agent.FORM_TAIL_PLAIN.format(url=tg_agent.LEAD_FORM_URL)
            combined = f"{body}{tail}".strip()
            if len(combined) <= _TEXT_LIMIT:
                body = combined
                asset = "form"
            else:
                body = tg_agent.LEAD_FORM_URL[:_TEXT_LIMIT]
                asset = "form"
    elif action == iu_contract.SEND_CONTRACT:
        # Contract generation is not yet an idempotent outbox operation.  Direct sending here
        # would recreate the exact bypass this workspace removes.
        body = "Передам запрос менеджеру — он подготовит и отправит договор."
        escalate = True
        reason = "Договор требует подтверждения и отправки оператором из workspace."

    if not body:
        body = "Уточню это у команды и вернусь с ответом."
        escalate = True
        reason = reason or "ИИ не сформировал безопасный ответ."
    if len(body) > _TEXT_LIMIT:
        body = body[:_TEXT_LIMIT].rstrip()

    stage_move = str(outcome.stage_move or "").strip()
    if asset == "terms" and facts is not None:
        import iu_funnel

        after_delivery = iu_funnel.DealFacts(
            stage=str(getattr(facts, "stage", "") or ""),
            terms_delivered=True,
            form_filled=bool(getattr(facts, "form_filled", False)),
            contract_sent=bool(getattr(facts, "contract_sent", False)),
            contract_signed=bool(getattr(facts, "contract_signed", False)),
        )
        # Match the legacy runtime's fact transition, but persist the desired
        # stage before Telegram send so a later Business echo can repair it.
        stage_move = iu_funnel.next_stage(after_delivery) or stage_move

    metadata = {
        "action": action,
        "asset": asset,
        "escalate_after_delivery": escalate,
        "escalation_reason": reason[:1000],
        "stage_move": stage_move[:200],
        "answered_client": bool(outcome.answered_client),
        "sources": list(outcome.sources or ()),
        "trace": dict(outcome.trace or {}),
    }
    return PreparedReply(
        text=body,
        metadata=metadata,
        escalate=escalate,
        escalation_reason=reason,
    )


def process_ai_jobs_once(*, worker_id: str, limit: int = 3) -> int:
    store = _store()
    jobs = store.claim_ai_jobs(
        worker_id=worker_id,
        limit=limit,
        lease_seconds=600,
    )
    for job in jobs:
        _process_ai_job(job, worker_id=worker_id)
    return len(jobs)


def _process_ai_job(job: Mapping[str, Any], *, worker_id: str) -> None:
    store = _store()
    job_id = int(job["id"])
    guard = store.ai_job_guard(job_id, worker_id=worker_id)
    if not guard.get("allowed"):
        _cancel_ai_job_safely(
            job_id,
            worker_id=worker_id,
            reason=str(guard.get("reason") or "AI guard rejected the job"),
        )
        return
    conversation = store.get_conversation(job["conversation_id"])
    if not ai_allowed(conversation.get("external_user_id")):
        _cancel_ai_job_safely(
            job_id,
            worker_id=worker_id,
            reason="AI rollout does not allow this Telegram ID.",
        )
        return

    try:
        import funnel_workspace_crm

        crm_result = funnel_workspace_crm.ensure_conversation_deal(conversation["id"])
        conversation = dict(crm_result["conversation"])
    except Exception as exc:  # noqa: BLE001 - a helpful answer may proceed while CRM retries
        log.warning(
            "AI turn %s proceeds without CRM link: %s",
            job_id,
            _safe_error(exc),
        )

    trigger_message_id = _as_int(job.get("trigger_message_id"))
    list_kwargs: dict[str, Any] = {"limit": 500}
    if trigger_message_id is not None:
        list_kwargs["before_id"] = trigger_message_id + 1
    messages = store.list_messages(conversation["id"], **list_kwargs)
    turn = dialog_turn(
        messages,
        trigger_message_id=trigger_message_id,
    )
    if not turn.texts:
        _cancel_ai_job_safely(
            job_id,
            worker_id=worker_id,
            reason="No unanswered client text remained.",
        )
        return

    author = {
        "id": _as_int(conversation.get("external_user_id"))
        or _as_int(conversation.get("external_chat_id")),
        "username": conversation.get("username") or "",
        "first_name": conversation.get("display_name") or "",
    }
    try:
        import iu_runtime

        facts, outcome = iu_runtime.decide_turn(
            author,
            list(turn.texts),
            deal_id=_as_int(conversation.get("deal_id")),
            history=turn.history,
        )
        if outcome is None:
            raise RuntimeError("ИУ runtime returned no outcome")
        prepared = prepare_reply(
            outcome,
            telegram_user_id=int(author["id"]),
            facts=facts,
        )

        # The model can take minutes.  Re-check ownership/version immediately before enqueue.
        guard = store.ai_job_guard(job_id, worker_id=worker_id)
        if not guard.get("allowed"):
            _cancel_ai_job_safely(
                job_id,
                worker_id=worker_id,
                reason=str(guard.get("reason") or "Conversation changed during AI turn."),
            )
            return
        queued = store.enqueue_outgoing_agent(
            conversation["id"],
            text=prepared.text,
            expected_version=job["expected_version"],
            idempotency_key=f"ai-job:{job_id}:reply",
            agent_name=AGENT_NAME,
            metadata=prepared.metadata,
        )
        store.complete_ai_job(
            job_id,
            worker_id=worker_id,
            outbox_id=queued["outbox"]["id"],
        )
        _wake_event.set()
    except (
        store.WorkspaceConflictError,
        store.WorkspaceControlError,
        store.WorkspaceReplyWindowExpired,
    ) as exc:
        _cancel_ai_job_safely(
            job_id,
            worker_id=worker_id,
            reason=_safe_error(exc),
        )
    except Exception as exc:  # noqa: BLE001
        retry = store.retry_ai_job(
            job_id,
            worker_id=worker_id,
            error=_safe_error(exc),
            delay_seconds=min(120, 5 * max(1, int(job.get("attempts") or 1))),
            max_attempts=4,
        )
        if retry.get("processing_status") == "failed":
            _mark_waiting_if_current(
                conversation["id"],
                expected_version=int(job["expected_version"]),
                reason=f"ИИ не смог обработать диалог: {_safe_error(exc)}",
            )


def _cancel_ai_job_safely(job_id: int, *, worker_id: str, reason: str) -> None:
    try:
        _store().cancel_ai_job(
            job_id,
            worker_id=worker_id,
            reason=reason[:4000],
        )
    except _store().WorkspaceConflictError:
        pass


def process_outbox_once(*, worker_id: str, limit: int = 25) -> int:
    store = _store()
    rows = store.claim_outbox(worker_id=worker_id, limit=limit, lease_seconds=90)
    for row in rows:
        _process_outbox_item(row, worker_id=worker_id)
    return len(rows)


def _process_outbox_item(item: Mapping[str, Any], *, worker_id: str) -> None:
    import tg_agent

    store = _store()
    outbox_id = int(item["id"])
    guard = store.outbox_send_guard(outbox_id, worker_id=worker_id)
    if not guard.get("allowed"):
        store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="cancelled",
            error=str(guard.get("reason") or "Delivery guard rejected the message."),
        )
        return
    current = dict(guard.get("outbox") or item)
    if current.get("author_type") == "agent" and not ai_allowed(
        current.get("external_chat_id")
    ):
        store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="cancelled",
            error="AI rollout was disabled before delivery.",
        )
        return

    connection_id, connection_error = tg_agent._business_connection_id(
        str(current.get("business_connection_id") or "")
    )
    if not connection_id:
        finished = store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="failed",
            error=connection_error,
        )
        _after_delivery(
            current,
            result=str((finished.get("outbox") or {}).get("delivery_status") or "failed"),
            finished=finished,
        )
        return

    boundary = store.begin_outbox_send(
        outbox_id,
        worker_id=worker_id,
        lease_seconds=90,
    )
    if not boundary.get("allowed"):
        observed = dict(boundary.get("outbox") or {})
        observed_status = str(observed.get("delivery_status") or "")
        if observed_status in {"sent", "failed", "unknown", "cancelled"}:
            return
        store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="cancelled",
            error=str(boundary.get("reason") or "Delivery boundary rejected the message."),
        )
        return
    current = dict(boundary.get("outbox") or current)

    try:
        try:
            sent = tg_agent.api(
                "sendMessage",
                http_timeout=45,
                business_connection_id=connection_id,
                chat_id=int(current["external_chat_id"]),
                text=str(current.get("text") or "")[:4096],
                link_preview_options={"is_disabled": True},
            )
            provider_message_id = (
                str(sent.get("message_id"))
                if isinstance(sent, Mapping) and sent.get("message_id") is not None
                else None
            )
        except RuntimeError as exc:
            if not _peer_unknown_to_bot(exc):
                raise
            # Telegram отдаёт боту доступ только к тем собеседникам, кто написал в
            # бизнес-аккаунт после подключения бота. Остальным бот написать не может
            # вовсе — но у аккаунта менеджера диалог есть, и он может.
            provider_message_id = _send_as_manager_account(current, exc)
        finished = store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="sent",
            provider_message_id=provider_message_id,
        )
        _after_delivery(
            current,
            result=str((finished.get("outbox") or {}).get("delivery_status") or "sent"),
            finished=finished,
        )
    except requests.RequestException as exc:
        # The request may have reached Telegram. Retrying could show the client two messages.
        finished = store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="unknown",
            error=_safe_error(exc),
        )
        _after_delivery(
            current,
            result=str((finished.get("outbox") or {}).get("delivery_status") or "unknown"),
            finished=finished,
        )
    except RuntimeError as exc:
        # tg_agent.api raises RuntimeError only after an explicit Bot API `ok=false`: not sent.
        finished = store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="failed",
            error=_safe_error(exc),
        )
        _after_delivery(
            current,
            result=str((finished.get("outbox") or {}).get("delivery_status") or "failed"),
            finished=finished,
        )
    except Exception as exc:  # noqa: BLE001 - unknown network/client state, never auto-retry
        finished = store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="unknown",
            error=_safe_error(exc),
        )
        _after_delivery(
            current,
            result=str((finished.get("outbox") or {}).get("delivery_status") or "unknown"),
            finished=finished,
        )


def _after_delivery(
    outbox: Mapping[str, Any],
    *,
    result: str,
    finished: Mapping[str, Any],
) -> None:
    """Apply only facts that became true after the provider call."""

    import tg_agent

    payload = outbox.get("payload") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    conversation_id = int(outbox["conversation_id"])

    if result == "sent":
        asset = str(payload.get("asset") or "")
        telegram_id = _as_int(outbox.get("external_chat_id"))
        if asset == "terms" and telegram_id:
            tg_agent._mark_terms_sent(telegram_id)
        elif asset == "form" and telegram_id:
            tg_agent._mark_invited(telegram_id)

    if outbox.get("author_type") != "agent":
        return
    if payload.get("escalate_after_delivery") or result in {"failed", "unknown"}:
        reason = str(payload.get("escalation_reason") or "").strip()
        if not reason:
            reason = (
                "Не удалось подтвердить доставку ответа ИИ."
                if result == "unknown"
                else "Ответ ИИ не доставлен."
            )
        _mark_waiting_if_current(
            conversation_id,
            expected_version=int(outbox["conversation_version"]),
            reason=reason,
        )


def _mark_waiting_if_current(
    conversation_id: int,
    *,
    expected_version: int,
    reason: str,
) -> None:
    store = _store()
    try:
        store.mark_waiting_human(
            conversation_id,
            expected_version=expected_version,
            reason=(reason or "Нужен ответ человека.")[:1000],
        )
    except (
        store.WorkspaceConflictError,
        store.WorkspaceControlError,
        store.WorkspaceReplyWindowExpired,
    ):
        # A new client message or operator action is newer than this delivery result.
        return


def _crm_action_retry_delay(attempts: Any) -> int:
    """Bounded exponential backoff: 5s, 15s, 45s ... capped at one hour."""

    try:
        attempt = max(1, int(attempts))
    except (TypeError, ValueError):
        attempt = 1
    return min(3600, 5 * (3 ** min(6, attempt - 1)))


def process_crm_actions_once(*, worker_id: str, limit: int = 5) -> int:
    store = _store()
    actions = store.claim_crm_actions(
        worker_id=worker_id,
        limit=limit,
        lease_seconds=600,
    )
    actions.sort(
        key=lambda item: (
            0 if item.get("action_type") == "delivery_effects" else 1,
            int(item.get("id") or 0),
        )
    )
    for action in actions:
        _process_crm_action(action, worker_id=worker_id)
    return len(actions)


def _process_crm_action(
    action: Mapping[str, Any],
    *,
    worker_id: str,
) -> None:
    store = _store()
    action_id = int(action["id"])
    try:
        action_type = str(action.get("action_type") or "")
        if action_type == "delivery_effects":
            result = _apply_delivery_effects(action)
        elif action_type == "ensure_deal":
            import funnel_workspace_crm

            ensured = funnel_workspace_crm.ensure_conversation_deal(
                action["conversation_id"],
            )
            result = {
                "conversation_id": int(action["conversation_id"]),
                "deal_id": int(ensured["deal_id"]),
                "status": str(ensured.get("status") or "linked"),
                "created": bool(ensured.get("created")),
                "recovered": bool(ensured.get("recovered")),
                "already_linked": bool(ensured.get("already_linked")),
                "orphan_deal_id": _as_int(ensured.get("orphan_deal_id")),
            }
        elif action_type == "move_stage":
            import funnel_workspace_crm

            result = funnel_workspace_crm.apply_conversation_stage_action(
                action["conversation_id"],
                action["target_stage"],
            )
        else:
            raise RuntimeError(
                f"Unsupported durable workspace action {action_type!r}."
            )
        store.complete_crm_action(
            action_id,
            worker_id=worker_id,
            result=result,
        )
    except Exception as exc:  # noqa: BLE001 - every external failure is bounded and inspectable
        try:
            retried = store.retry_crm_action(
                action_id,
                worker_id=worker_id,
                error=_safe_error(exc),
                delay_seconds=_crm_action_retry_delay(action.get("attempts")),
            )
        except store.WorkspaceConflictError:
            # Another worker recovered/completed an expired lease while this
            # external call was in flight.  Its durable state is authoritative.
            log.warning(
                "workspace CRM action %s lease was lost before result commit",
                action_id,
            )
            return
        if retried.get("processing_status") == "dead_letter":
            log.error(
                "workspace CRM action %s exhausted %s attempts: %s",
                action_id,
                retried.get("attempts"),
                _safe_error(exc),
            )


def _apply_delivery_effects(action: Mapping[str, Any]) -> dict[str, Any]:
    """Replay safe local facts and escalation after a confirmed Telegram send."""

    import tg_agent

    payload = action.get("payload") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    asset = str(payload.get("asset") or "")
    telegram_id = _as_int(payload.get("telegram_id"))
    applied_asset = ""
    if asset == "terms":
        if telegram_id is None:
            raise RuntimeError("Delivered terms action has no Telegram id.")
        if not tg_agent._terms_already_sent(telegram_id):
            tg_agent._mark_terms_sent(telegram_id)
        applied_asset = asset
    elif asset == "form":
        if telegram_id is None:
            raise RuntimeError("Delivered form action has no Telegram id.")
        if not tg_agent._invite_already_sent(telegram_id):
            tg_agent._mark_invited(telegram_id)
        applied_asset = asset

    escalated = bool(
        payload.get("author_type") == "agent"
        and payload.get("escalate_after_delivery")
    )
    if escalated:
        _mark_waiting_if_current(
            int(action["conversation_id"]),
            expected_version=int(payload.get("conversation_version") or 0),
            reason=str(
                payload.get("escalation_reason")
                or "После доставленного ответа ИИ нужен ответ человека."
            ),
        )
    return {
        "conversation_id": int(action["conversation_id"]),
        "status": "applied",
        "asset": applied_asset,
        "escalated": escalated,
    }


def _safe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return (text or exc.__class__.__name__)[:4000]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
