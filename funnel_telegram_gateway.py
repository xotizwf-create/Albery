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
import re
import socket
import threading
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import requests

log = logging.getLogger("funnel-telegram-gateway")

SOURCE_KEY = "telegram"
#: Диалог, который клиент завёл сам, написав боту. Отличается от бизнес-переписки
#: менеджера тем, что бизнес-подключения у него нет: ответ уходит обычным сообщением.
BOT_SOURCE_KEY = "telegram_bot"
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
        register_client_bot_commands()
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
            ("iu-reminders", _reminder_loop),
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


def register_client_bot_commands() -> bool:
    """Показать команды в системном меню Telegram."""

    if not client_bot_enabled():
        return False
    try:
        import tg_agent

        tg_agent.api(
            "setMyCommands",
            commands=[
                {"command": "start", "description": "Начать или перезапустить сценарий"},
                {"command": "menu", "description": "Вернуться в главное меню"},
                {"command": "stop", "description": "Остановить поддержку и напоминания"},
            ],
        )
        return True
    except Exception as exc:  # noqa: BLE001 - регистрация меню не останавливает транспорт
        log.warning("iu client bot commands were not registered: %s", _safe_error(exc))
        return False


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


def ai_worker_needed() -> bool:
    """Нужен ли обработчик заданий ИИ хоть какому-то каналу.

    Общий рубильник относится к переписке бизнес-аккаунта и намеренно выключен. Если
    сверяться только с ним, задания клиентского бота копились бы в очереди нетронутыми:
    клиент видел бы молчание, а в базе — вечное «ожидает». Кому именно отвечать, решает
    ai_allowed_in_channel уже по каналу диалога.
    """

    import iu_client_bot

    return ai_enabled() or (iu_client_bot.enabled() and iu_client_bot.ai_answers_enabled())


def _ai_loop() -> None:
    worker_id = f"{_worker_prefix}-ai"
    while not _stop_event.is_set():
        count = 0
        if ai_worker_needed():
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


def _reminder_loop() -> None:
    worker_id = f"{_worker_prefix}-iu-reminders"
    while not _stop_event.is_set():
        try:
            count = process_iu_reminders_once(worker_id=worker_id)
        except Exception:  # noqa: BLE001
            log.exception("IU reminder worker failed")
            count = 0
        if not count:
            _loop_wait(_poll_seconds("IU_BOT_REMINDER_POLL_SECONDS", 5.0))


def process_iu_reminders_once(*, worker_id: str, limit: int = 20) -> int:
    import iu_bot_reminders
    import iu_bot_state
    import iu_client_bot

    rows = iu_bot_reminders.claim_due(worker_id=worker_id, limit=limit)
    for row in rows:
        try:
            decision = iu_bot_reminders.delivery_decision(
                datetime.now(timezone.utc), row["due_at"]
            )
            if decision.action == "wait":
                iu_bot_reminders.finish(
                    row,
                    worker_id=worker_id,
                    status="pending",
                    retry_at=decision.retry_at,
                )
                continue
            if decision.action == "cancel":
                iu_bot_reminders.finish(
                    row, worker_id=worker_id, status="cancelled"
                )
                continue
            conversation = _store().get_conversation(row["conversation_id"]) or {}
            messages = _bot_messages(int(row["conversation_id"]))
            state = iu_bot_state.support_state(messages)
            newer_client_message = any(
                message.get("author_type") == "client"
                and int(message.get("id") or 0) > int(row["anchor_message_id"] or 0)
                for message in messages
            )
            if (
                str(conversation.get("source_key") or "") != BOT_SOURCE_KEY
                or str(conversation.get("control_mode") or "") != "ai"
                or state.mode != "active"
                or newer_client_message
            ):
                iu_bot_reminders.finish(
                    row, worker_id=worker_id, status="cancelled"
                )
                continue
            text = (
                iu_client_bot.REMINDER_WAITING_QUESTION
                if row["kind"] == "waiting_question"
                else iu_client_bot.REMINDER_AFTER_ANSWER
            )
            _reply_to_client(
                int(row["conversation_id"]),
                text,
                idempotency_key=(
                    f"iu-reminder:{row['conversation_id']}:{row['kind']}:"
                    f"{row['anchor_message_id']}"
                ),
                reply_markup=iu_client_bot.support_menu(
                    offer_operator=iu_client_bot.should_offer_operator(
                        iu_bot_state.support_agent_replies(messages)
                    )
                ),
                metadata={"iu_event": "support_quiet_close"},
            )
            iu_bot_reminders.finish(row, worker_id=worker_id, status="sent")
        except Exception as exc:  # noqa: BLE001
            iu_bot_reminders.finish(
                row,
                worker_id=worker_id,
                status="pending",
                retry_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                error=_safe_error(exc),
            )
    return len(rows)


def _maintenance_loop() -> None:
    last_crm = 0.0
    last_stage_sync = 0.0
    last_retention = 0.0
    while not _stop_event.is_set():
        now = time.monotonic()
        try:
            store = _store()
            released = store.release_expired_human_leases(limit=100)
            if released:
                # release_expired_human_leases already restores the latest unanswered
                # client turn. Wake the AI worker immediately instead of making the
                # customer wait for another polling interval.
                _wake_event.set()
            for conversation in released:
                if (
                    conversation.get("control_mode") == "ai"
                    and not ai_allowed_in_channel(
                        conversation, conversation.get("external_user_id")
                    )
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
        message = dict(update["message"])
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if chat.get("type") == "private" and client_bot_enabled():
            # Один и тот же бот-менеджер для всех, включая владельца (его решение
            # 28.07.2026). Раньше свои попадали в личного ИИ-ассистента, и один бот жил
            # двумя жизнями: клиент видел воронку, сотрудник — ассистента.
            return ingest_bot_message(message, provider_update_id=provider_update_id)
        if chat.get("type") == "private" and tg_agent.is_owner(sender):
            # Клиентский вход выключен — бот остаётся внутренним каналом владельца.
            tg_agent._workers.submit(
                tg_agent._handle_update_safely,
                {"message": message},
            )
        return None, None
    if update.get("callback_query"):
        if not client_bot_enabled():
            return None, None
        return handle_bot_callback(dict(update["callback_query"]))
    if update.get("business_connection"):
        tg_agent.handle_business_connection(dict(update["business_connection"]))
        return None, None
    if update.get("business_message"):
        message = dict(update["business_message"])
        # Эхо собственной отправки — не новое обращение, а подтверждение того, что наш
        # ответ ушёл. Его обрабатываем даже с выключенным приёмом, иначе сообщение,
        # отправленное до отключения, навсегда осталось бы «в пути».
        if not business_intake_enabled() and not message.get("sender_business_bot"):
            return None, None
        return ingest_business_message(message, provider_update_id=provider_update_id)
    if update.get("edited_business_message"):
        if not business_intake_enabled():
            return None, None
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


def business_intake_enabled() -> bool:
    """Заводит ли личка аккаунта менеджера обращения в рабочем окне.

    Владелец 28.07.2026: единственный источник обращений — бот, переписка в личке менеджера
    в рабочее окно больше не идёт. Выключается только явным флагом: потеря настройки не
    должна молча обрубать канал, через который сейчас живут открытые диалоги.
    """

    return os.getenv("FUNNEL_WORKSPACE_BUSINESS_INTAKE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def client_bot_enabled() -> bool:
    """Открыт ли боту клиентский вход. Выключено — чужие сообщения игнорируются, как раньше."""

    import iu_client_bot

    return iu_client_bot.enabled()


def _reply_to_client(
    conversation_id: int,
    text: str,
    *,
    idempotency_key: str,
    reply_markup: Mapping[str, Any] | None = None,
    attachment: Mapping[str, Any] | None = None,
    attachments: list[Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
    service: bool = True,
) -> Mapping[str, Any] | None:
    """Служебный ответ бота — через ту же очередь, что и ответы оператора.

    Прямая отправка в Telegram оставила бы команду без половины разговора: в ленте
    обращения не было бы ни приветствия, ни условий, ни того, что клиент нажимал.
    """

    store = _store()
    if not hasattr(store, "enqueue_outgoing_agent"):
        return None
    conversation = (
        store.get_conversation(conversation_id) or {}
        if hasattr(store, "get_conversation")
        else {}
    )
    message_metadata: dict[str, Any] = {
        "channel": "iu_client_bot",
        **dict(metadata or {}),
    }
    if reply_markup:
        message_metadata["reply_markup"] = dict(reply_markup)
    for attempt in range(2):
        try:
            queued = store.enqueue_outgoing_agent(
                conversation_id,
                text=text,
                expected_version=conversation.get("state_version"),
                idempotency_key=idempotency_key,
                agent_name=AGENT_NAME,
                metadata=message_metadata,
                attachment=attachment,
                attachments=attachments,
                service=service,
            )
            break
        except store.WorkspaceConflictError as exc:
            # Параллельное входящее сообщение или служебный ответ могли успеть
            # изменить версию диалога. Один раз перечитываем состояние:
            # идемпотентный ключ всё равно не позволит создать дубль.
            if attempt == 0 and "current_version" in getattr(exc, "details", {}):
                conversation = store.get_conversation(conversation_id) or {}
                continue
            log.warning(
                "iu client bot: reply %s was not queued (%s)",
                idempotency_key,
                _safe_error(exc),
            )
            return None
    else:  # pragma: no cover - цикл либо поставил сообщение, либо вернулся из except
        return None
    _wake_event.set()
    return queued


def _conversation_for_bot_chat(chat_id: Any) -> int | None:
    """Обращение, заведённое для этого чата с ботом."""

    store = _store()
    if not hasattr(store, "find_conversation"):
        return None
    conversation = store.find_conversation(
        source_key=BOT_SOURCE_KEY,
        business_connection_id="",
        external_chat_id=str(chat_id),
    )
    return _as_int((conversation or {}).get("id"))


def _bot_messages(conversation_id: int) -> list[dict[str, Any]]:
    store = _store()
    if not hasattr(store, "list_messages"):
        return []
    return list(store.list_messages(conversation_id, limit=500) or [])


def _cancel_bot_reminders(conversation_id: int) -> None:
    try:
        import iu_bot_reminders

        iu_bot_reminders.cancel_all(conversation_id)
    except Exception as exc:  # noqa: BLE001 - сценарий продолжает работать без напоминаний
        log.warning("iu client bot: reminders were not cancelled: %s", _safe_error(exc))


def _schedule_existing_question(conversation_id: int, messages: list[dict[str, Any]]) -> None:
    import iu_bot_state

    trigger_message_id = iu_bot_state.latest_pending_question(messages)
    if not trigger_message_id:
        return
    store = _store()
    conversation = store.get_conversation(conversation_id) or {}
    try:
        store.schedule_ai_job(
            conversation_id,
            trigger_message_id=trigger_message_id,
            expected_version=conversation.get("state_version"),
        )
        _wake_event.set()
    except Exception as exc:  # noqa: BLE001 - новый вопрос клиент всё равно сможет написать
        log.warning("iu client bot: pending question was not scheduled: %s", _safe_error(exc))


def _manager_request_metadata(
    event: str,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    """Delivery metadata for every customer-facing handover to the IU manager."""

    conversation = (
        _store().get_conversation(conversation_id) or {}
        if conversation_id is not None
        else {}
    )
    client_name = str(
        conversation.get("display_name")
        or (
            f"@{str(conversation.get('username')).lstrip('@')}"
            if conversation.get("username")
            else ""
        )
        or "Клиент"
    ).strip()
    # Имя попадает в BBCode-сообщение Битрикса: убираем управляющие скобки и переносы,
    # оставляя само имя читаемым.
    client_name = " ".join(client_name.replace("[", "").replace("]", "").split())[:300]
    return {
        "iu_event": event,
        "notify_manager_after_delivery": True,
        "manager_notification_recipient": os.getenv(
            "IU_MANAGER_NOTIFY_BITRIX_USER_ID", "16"
        ),
        "manager_notification_bot_id": (
            _as_int(os.getenv("IU_AGENT_BOT_ID", "86")) or 86
        ),
        "manager_notification_client_name": client_name or "Клиент",
    }


def _reply_and_hand_over(
    conversation_id: int,
    text: str,
    *,
    idempotency_key: str,
    event: str,
    reason: str,
    metadata: Mapping[str, Any] | None = None,
    **reply_options: Any,
) -> dict[str, Any] | None:
    """Tell the client, persist the manager badge and enqueue a durable Bitrix alert."""

    payload = dict(metadata or {})
    payload.update(_manager_request_metadata(event, conversation_id))
    queued = _reply_to_client(
        conversation_id,
        text,
        idempotency_key=idempotency_key,
        metadata=payload,
        **reply_options,
    )
    _cancel_bot_reminders(conversation_id)
    _hand_over_to_human(
        conversation_id,
        reason,
        manager_requested=True,
    )
    return queued


def hide_client_menu_for_manager(
    conversation_id: int,
    *,
    state_version: int,
) -> Mapping[str, Any] | None:
    """Remove the scenario reply keyboard as soon as an operator takes the chat."""

    import iu_client_bot

    return _reply_to_client(
        conversation_id,
        "Менеджер подключился к диалогу.",
        idempotency_key=f"iu-bot:manager-takeover:{conversation_id}:{state_version}",
        reply_markup=iu_client_bot.remove_keyboard(),
        metadata={"iu_event": "manager_takeover"},
        service=True,
    )


def _send_terms_documents(conversation_id: int, *, idempotency_key: str) -> None:
    import iu_bot_documents
    import iu_client_bot

    try:
        terms = iu_bot_documents.attachment("terms")
        contract = iu_bot_documents.attachment("contract")
        _reply_to_client(
            conversation_id,
            iu_client_bot.TERMS_REPLY,
            idempotency_key=f"{idempotency_key}:documents",
            attachments=[terms, contract],
            metadata={"iu_event": "terms_sent", "asset": "terms"},
        )
    except Exception as exc:  # noqa: BLE001 - юридический документ нельзя подменять догадкой
        log.warning("iu client bot: PDF documents unavailable: %s", _safe_error(exc))
        _reply_and_hand_over(
            conversation_id,
            iu_client_bot.TERMS_FALLBACK,
            idempotency_key=f"{idempotency_key}:unavailable",
            event="terms_unavailable",
            reason=(
                "Не удалось отправить утверждённые PDF условий/договора: "
                f"{_safe_error(exc)}"
            ),
            reply_markup=iu_client_bot.main_menu(),
        )


def _enter_support(
    conversation_id: int,
    *,
    idempotency_key: str,
    messages: list[dict[str, Any]],
) -> None:
    import iu_bot_documents
    import iu_bot_reminders
    import iu_client_bot

    try:
        faq = iu_bot_documents.attachment("faq")
        queued = _reply_to_client(
            conversation_id,
            iu_client_bot.ASK_PROMPT,
            idempotency_key=f"{idempotency_key}:faq",
            attachment=faq,
            reply_markup=iu_client_bot.support_menu(),
            metadata={"iu_event": "support_enter"},
        )
    except Exception as exc:  # noqa: BLE001 - текстовый вход в поддержку остаётся доступен
        log.warning("iu client bot: FAQ PDF unavailable: %s", _safe_error(exc))
        queued = _reply_to_client(
            conversation_id,
            iu_client_bot.ASK_PROMPT,
            idempotency_key=f"{idempotency_key}:prompt",
            reply_markup=iu_client_bot.support_menu(),
            metadata={"iu_event": "support_enter", "faq_unavailable": True},
        )
    anchor = _as_int(((queued or {}).get("message") or {}).get("id"))
    try:
        iu_bot_reminders.schedule_waiting_question(
            conversation_id,
            anchor_message_id=anchor or 0,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("iu client bot: support reminder was not scheduled: %s", _safe_error(exc))
    _schedule_existing_question(conversation_id, messages)


def handle_bot_callback(callback: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Нажатие кнопки в клиентском боте."""

    import iu_client_bot
    import tg_agent

    data = str(callback.get("data") or "")
    label = iu_client_bot.button_label(data)
    message = dict(callback.get("message") or {})
    chat = dict(message.get("chat") or {})
    sender = dict(callback.get("from") or {})
    chat_id = chat.get("id")
    if chat_id is None or not label:
        return None, None

    # Telegram ждёт подтверждения нажатия, иначе кнопка «крутится» у клиента.
    try:
        tg_agent.api("answerCallbackQuery", callback_query_id=callback.get("id"))
    except Exception as exc:  # noqa: BLE001 - косметика не важнее самого ответа
        log.info("answerCallbackQuery failed: %s", _safe_error(exc))

    # Нажатие — такая же реплика клиента, как текст: без неё лента станет односторонней.
    conversation_id, _ = ingest_bot_message(
        {
            "message_id": f"cb-{callback.get('id')}",
            # Момент нажатия, а не дата сообщения с кнопками: иначе в ленте рабочего окна
            # все нажатия слипаются во время приветствия и встают раньше ответов на них.
            "date": int(time.time()),
            "chat": chat,
            "from": sender,
            "text": label,
        },
        schedule_ai=False,
        handle_scenario=False,
    )
    if conversation_id is None:
        conversation_id = _conversation_for_bot_chat(chat_id)
    if conversation_id is None:
        return None, None

    key = f"iu-bot:{chat_id}:{callback.get('id')}"
    run_menu_action(
        data,
        conversation_id=conversation_id,
        idempotency_key=key,
    )
    return conversation_id, None


def _hand_over_to_human(
    conversation_id: int,
    reason: str,
    *,
    manager_requested: bool = True,
) -> None:
    """Передать обращение человеку: дальше отвечает оператор из рабочего окна."""

    store = _store()
    conversation = store.get_conversation(conversation_id) or {}
    try:
        store.mark_waiting_human(
            conversation_id,
            expected_version=conversation.get("state_version"),
            reason=reason,
            manager_requested=manager_requested,
        )
    except Exception as exc:  # noqa: BLE001 - клиент уже получил ответ, сбой не должен его терять
        log.warning("iu client bot: handover failed for %s: %s", conversation_id, _safe_error(exc))


def ingest_bot_message(
    message: Mapping[str, Any],
    *,
    provider_update_id: int | None = None,
    schedule_ai: bool | None = None,
    handle_scenario: bool = True,
) -> tuple[int | None, int | None]:
    """Сообщение, которое клиент написал боту напрямую, — в общий поток обращений.

    Отличие от бизнес-переписки одно: здесь нет посредника-аккаунта, поэтому автор всегда
    клиент, а бизнес-подключения нет. Дальше всё общее — тот же журнал, то же рабочее окно,
    та же сделка в CRM.
    """

    chat = dict(message.get("chat") or {})
    if str(chat.get("type") or "private") != "private":
        return None, None
    chat_id = chat.get("id")
    external_message_id = message.get("message_id")
    if chat_id is None or external_message_id is None:
        raise ValueError("Bot message lacks chat/message identity")

    import iu_client_bot
    import iu_bot_state

    sender = dict(message.get("from") or {})
    customer_id = _as_int(sender.get("id")) or _as_int(chat_id)
    display_name = _display_name(sender) or _display_name(chat) or f"Telegram {chat_id}"
    text = telegram_message_text(message)
    existing_id = _conversation_for_bot_chat(chat_id)
    previous_conversation = (
        dict(_store().get_conversation(existing_id) or {}) if existing_id else {}
    )
    previous_messages = _bot_messages(existing_id) if existing_id else []
    previous_support = iu_bot_state.support_state(previous_messages)
    is_command = text.strip().startswith("/")
    # Пункт меню приходит обычным текстом. Это выбор действия, а не вопрос: отвечает
    # сценарий, модель не вызывается вовсе.
    action = iu_client_bot.menu_action(text)
    calculator_discussion = iu_client_bot.is_calculator_discussion(text)
    if action == iu_client_bot.CB_OPERATOR and previous_support.mode not in {
        "active",
        "confirming",
    }:
        action = ""
    if action in {iu_client_bot.CB_CONFIRM_YES, iu_client_bot.CB_CONFIRM_NO} and (
        previous_support.mode != "confirming"
    ):
        action = ""
    media_type = telegram_media_type(message)
    has_media_caption = bool(str(message.get("text") or message.get("caption") or "").strip())
    awaiting_file_context = iu_bot_state.awaiting_file_context(previous_messages)
    if schedule_ai is None:
        schedule_ai = (
            iu_client_bot.ai_answers_enabled()
            and not is_command
            and not action
            and not calculator_discussion
            and media_type == "text"
            and not awaiting_file_context
            and previous_support.mode in {"active", "quiet"}
        )
    command = text.strip().split(maxsplit=1)[0].split("@", 1)[0].lower() if text.strip() else ""
    start_payload = ""
    if command == "/start":
        parts = text.strip().split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", parts[1]):
            start_payload = parts[1]
    result = _store().ingest_business_message(
        external_chat_id=str(chat_id),
        external_message_id=str(external_message_id),
        text=text,
        author_type="client",
        source_key=BOT_SOURCE_KEY,
        business_connection_id="",
        external_user_id=customer_id,
        username=sender.get("username") or chat.get("username"),
        display_name=display_name,
        author_name=display_name,
        provider_update_id=provider_update_id,
        occurred_at=_message_datetime(message),
        metadata={
            "telegram_chat_type": str(chat.get("type") or "private"),
            "telegram_media_type": media_type,
            "telegram_has_caption": has_media_caption,
            **telegram_media_metadata(message),
            **({"iu_start_source": start_payload} if start_payload else {}),
        },
        schedule_ai=bool(schedule_ai),
    )
    conversation = dict(result.get("conversation") or {})
    journaled = dict(result.get("message") or {})
    conversation_id = _as_int(conversation.get("id"))
    if conversation_id is None:
        return None, _as_int(journaled.get("id"))
    _cancel_bot_reminders(conversation_id)
    all_messages = _bot_messages(conversation_id)
    if not handle_scenario:
        return conversation_id, _as_int(journaled.get("id"))
    if command == "/start":
        _reply_to_client(
            conversation_id,
            iu_client_bot.WELCOME if not previous_messages else iu_client_bot.WELCOME_BACK,
            idempotency_key=f"iu-bot:start:{chat_id}:{external_message_id}",
            reply_markup=iu_client_bot.main_menu(),
            metadata={"iu_event": "start"},
        )
    elif command == "/menu":
        _reply_to_client(
            conversation_id,
            iu_client_bot.MENU_PROMPT,
            idempotency_key=f"iu-bot:menu-command:{chat_id}:{external_message_id}",
            reply_markup=iu_client_bot.main_menu(),
            metadata={"iu_event": "menu"},
        )
    elif command == "/stop":
        _reply_to_client(
            conversation_id,
            iu_client_bot.STOPPED,
            idempotency_key=f"iu-bot:stop:{chat_id}:{external_message_id}",
            reply_markup=iu_client_bot.remove_keyboard(),
            metadata={"iu_event": "stop"},
        )
    elif (
        calculator_discussion
        and previous_conversation.get("control_mode") != "human"
    ):
        _handle_calculator_discussion(
            conversation_id,
            idempotency_key=f"iu-bot:calculator-discussion:{chat_id}:{external_message_id}",
        )
    elif action:
        run_menu_action(
            action,
            conversation_id=conversation_id,
            idempotency_key=f"iu-bot:menu:{chat_id}:{external_message_id}",
        )
    elif (
        media_type != "text"
        and not has_media_caption
        and previous_conversation.get("control_mode") != "human"
        and not iu_bot_state.bot_stopped(previous_messages)
    ):
        current_support = iu_bot_state.support_state(all_messages)
        queued = _reply_to_client(
            conversation_id,
            iu_client_bot.FILE_NEEDS_CONTEXT,
            idempotency_key=f"iu-bot:file-context:{chat_id}:{external_message_id}",
            reply_markup=(
                iu_client_bot.support_menu(
                    offer_operator=iu_client_bot.should_offer_operator(
                        iu_bot_state.support_agent_replies(all_messages)
                    )
                )
                if current_support.mode in {"active", "quiet"}
                else iu_client_bot.main_menu()
            ),
            metadata={"iu_event": "file_needs_context"},
        )
        if current_support.mode in {"active", "quiet"}:
            try:
                import iu_bot_reminders

                iu_bot_reminders.schedule_waiting_question(
                    conversation_id,
                    anchor_message_id=_as_int(
                        ((queued or {}).get("message") or {}).get("id")
                    ) or 0,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "iu client bot: file clarification reminder was not scheduled: %s",
                    _safe_error(exc),
                )
    elif (
        (media_type != "text" or awaiting_file_context)
        and previous_conversation.get("control_mode") != "human"
        and not iu_bot_state.bot_stopped(previous_messages)
    ):
        _reply_and_hand_over(
            conversation_id,
            iu_client_bot.FILE_SENT_TO_MANAGER,
            idempotency_key=f"iu-bot:file-handover:{chat_id}:{external_message_id}",
            event="file_handover",
            reason=(
                "Клиент прислал файл с пояснением — требуется ручной разбор содержимого."
            ),
            reply_markup=iu_client_bot.remove_keyboard(),
        )
    elif (
        not schedule_ai
        and previous_conversation.get("control_mode") != "human"
        and not iu_bot_state.bot_stopped(previous_messages)
    ):
        if iu_bot_state.last_join_result(previous_messages) == "filled":
            _reply_and_hand_over(
                conversation_id,
                iu_client_bot.FORM_EDIT_SENT,
                idempotency_key=f"iu-bot:form-edit:{chat_id}:{external_message_id}",
                event="form_edit_handover",
                reason="Клиент прислал изменения к уже заполненной анкете.",
                reply_markup=iu_client_bot.main_menu(),
            )
        else:
            # Вне режима поддержки ИИ не отвечает по существу и не переводит клиента
            # к человеку самовольно. На каждое свободное сообщение напоминаем, какой
            # пункт меню открывает консультацию.
            _reply_to_client(
                conversation_id,
                iu_client_bot.STRICT_QUESTION_HINT,
                idempotency_key=f"iu-bot:strict-hint:{chat_id}:{external_message_id}",
                reply_markup=iu_client_bot.main_menu(),
                metadata={"iu_event": "strict_question_hint"},
            )
    return conversation_id, _as_int(journaled.get("id"))


def _anketa_in_crm(deal_id) -> str:
    """Сверка анкеты ЖИВЬЁМ из сделки. Пусто — анкеты нет.

    Это единственный источник правды о том, заполнена ли анкета. Наша отметка о выдаче ссылки
    правдой не является: владелец 29.07.2026 удалил анкету в Битриксе, а бот продолжал
    говорить «вы уже заполнили». Сверку собирает `tg_agent.anketa_block` — тот же код, что
    показывает анкету в обычном ходе, с живыми названиями полей из воронки.

    Сделку могли удалить целиком, CRM могла не ответить — в обоих случаях считаем, что анкеты
    нет: предложить заполнить её лишний раз безопаснее, чем отказать человеку, который её не
    заполнял."""

    if not deal_id:
        return ""
    import tg_agent

    try:
        return tg_agent.anketa_block(tg_agent._deal_for_watch(int(deal_id)) or {})
    except Exception as exc:  # noqa: BLE001
        log.warning("анкета сделки %s не сверена: %s", deal_id, _safe_error(exc))
        return ""


def _join_body(conversation_id: int, *, repeated: bool = False) -> tuple[str, bool]:
    """Текст ответа на «Присоединиться к ИУ» и признак «анкета уже заполнена».

    Ссылка персональная: по метке в ней заявка приклеивается к этому же человеку, а не заводит
    вторую карточку в воронке. Повторное нажатие отдаёт ТУ ЖЕ ссылку, пока анкета не заполнена,
    и сверку данных из сделки — после.

    Сбой базы не оставляет клиента ни с чем: он получает прежний ответ с обещанием менеджера.
    """

    import iu_client_bot

    try:
        import iu_form_link
        from app import pg_connect

        conversation = dict(_store().get_conversation(conversation_id) or {})
        telegram_id = _as_int(conversation.get("external_user_id")) or _as_int(
            conversation.get("external_chat_id"))
        if not telegram_id:
            raise ValueError("у обращения нет Telegram id")
        deal_id = _as_int(conversation.get("deal_id"))
        anketa = _anketa_in_crm(deal_id)
        if anketa:
            # Ссылку не выдаём вовсе: анкета есть, и второй раз её заполнять незачем.
            return iu_client_bot.join_answer(anketa, "", repeated=repeated)
        with pg_connect() as conn:
            live = iu_form_link.issue(
                conn, telegram_id,
                conversation_id=conversation_id,
                deal_id=deal_id,
            )
        return iu_client_bot.join_answer("", live["url"], repeated=repeated)
    except Exception as exc:  # noqa: BLE001 — без ссылки клиент всё равно получает ответ
        log.warning("персональная ссылка на анкету не выдана: %s", _safe_error(exc))
        return iu_client_bot.JOIN_STUB, False


def _handle_calculator_discussion(
    conversation_id: int,
    *,
    idempotency_key: str,
) -> None:
    """Continue calculator conversion in the bot, gated by the live CRM form state."""

    import iu_bot_state
    import iu_client_bot

    messages = _bot_messages(conversation_id)
    repeated = (
        iu_bot_state.action_count(messages, iu_client_bot.BUTTON_JOIN) > 0
        or iu_bot_state.calculator_discussion_pending(messages)
    )
    body, form_filled = _join_body(conversation_id, repeated=repeated)
    if form_filled:
        _reply_and_hand_over(
            conversation_id,
            iu_client_bot.CALCULATOR_MANAGER_READY,
            idempotency_key=idempotency_key,
            event="calculator_discussion_filled",
            reason=(
                "Клиент вернулся из калькулятора, хочет обсудить условия; анкета заполнена."
            ),
            reply_markup=iu_client_bot.remove_keyboard(),
            metadata={"calculator_origin": True},
        )
        return
    if body == iu_client_bot.JOIN_STUB:
        _reply_and_hand_over(
            conversation_id,
            body,
            idempotency_key=idempotency_key,
            event="calculator_discussion_unavailable",
            reason=(
                "Клиент вернулся из калькулятора, но персональную ссылку на анкету "
                "не удалось подготовить."
            ),
            reply_markup=iu_client_bot.remove_keyboard(),
            metadata={"calculator_origin": True},
        )
        return
    _reply_to_client(
        conversation_id,
        body,
        idempotency_key=idempotency_key,
        reply_markup=iu_client_bot.main_menu(),
        metadata={
            "iu_event": "calculator_discussion_unfilled",
            "calculator_origin": True,
        },
    )


def run_menu_action(action: str, *, conversation_id: int, idempotency_key: str) -> None:
    """Выполнить выбранный в меню пункт."""

    import iu_client_bot
    import iu_bot_state

    messages = _bot_messages(conversation_id)
    if action == iu_client_bot.CB_TERMS:
        _send_terms_documents(conversation_id, idempotency_key=idempotency_key)
    elif action == iu_client_bot.CB_JOIN:
        repeated = iu_bot_state.action_count(messages, iu_client_bot.BUTTON_JOIN) > 1
        body, already = _join_body(conversation_id, repeated=repeated)
        if body == iu_client_bot.JOIN_STUB:
            _reply_and_hand_over(
                conversation_id,
                body,
                idempotency_key=idempotency_key,
                event="join_unavailable",
                reason="Персональную ссылку на анкету не удалось подготовить.",
                reply_markup=iu_client_bot.main_menu(),
            )
        else:
            _reply_to_client(
                conversation_id,
                body,
                idempotency_key=idempotency_key,
                reply_markup=iu_client_bot.main_menu(),
                metadata={"iu_event": "join_filled" if already else "join_unfilled"},
            )
    elif action == iu_client_bot.CB_CALCULATOR:
        _reply_to_client(
            conversation_id,
            iu_client_bot.CALCULATOR_REPLY,
            idempotency_key=idempotency_key,
            reply_markup=iu_client_bot.main_menu(),
            metadata={"iu_event": "calculator"},
        )
    elif action == iu_client_bot.CB_ASK:
        _enter_support(
            conversation_id,
            idempotency_key=idempotency_key,
            messages=messages,
        )
    elif action == iu_client_bot.CB_OPERATOR:
        replies = iu_bot_state.support_agent_replies(messages)
        if not messages and hasattr(_store(), "count_agent_replies"):
            replies = int(_store().count_agent_replies(conversation_id) or 0)
        if not iu_client_bot.should_offer_operator(replies):
            _reply_to_client(
                conversation_id,
                "Пока постараюсь помочь сам. Напишите вопрос — если понадобится, "
                "подключим менеджера.",
                idempotency_key=idempotency_key,
                reply_markup=iu_client_bot.support_menu(),
                metadata={"iu_event": "operator_too_early"},
            )
        else:
            _reply_and_hand_over(
                conversation_id,
                iu_client_bot.OPERATOR_CALLED,
                idempotency_key=idempotency_key,
                event="operator_called",
                reason="Клиент выбрал «Позвать оператора».",
                reply_markup=iu_client_bot.remove_keyboard(),
            )
    elif action == iu_client_bot.CB_EXIT_SUPPORT:
        _reply_to_client(
            conversation_id,
            iu_client_bot.EXIT_CONFIRM,
            idempotency_key=idempotency_key,
            reply_markup=iu_client_bot.exit_confirmation_menu(),
            metadata={"iu_event": "support_exit_confirm"},
        )
    elif action == iu_client_bot.CB_CONFIRM_YES:
        _cancel_bot_reminders(conversation_id)
        _reply_to_client(
            conversation_id,
            iu_client_bot.EXITED_SUPPORT,
            idempotency_key=idempotency_key,
            reply_markup=iu_client_bot.main_menu(),
            metadata={"iu_event": "support_exit"},
        )
    elif action == iu_client_bot.CB_CONFIRM_NO:
        _reply_to_client(
            conversation_id,
            iu_client_bot.CONTINUE_SUPPORT,
            idempotency_key=idempotency_key,
            reply_markup=iu_client_bot.support_menu(
                offer_operator=iu_client_bot.should_offer_operator(
                    iu_bot_state.support_agent_replies(messages)
                )
            ),
            metadata={"iu_event": "support_continue"},
        )
        # Нажатие «Выйти» является новым клиентским событием и отменяет незавершённую
        # генерацию. После «Нет» возвращаем в очередь исходный вопрос, а не ждём,
        # пока клиент догадается повторить его.
        _schedule_existing_question(conversation_id, messages)


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


def _telegram_ids(payload: Mapping[str, Any]) -> tuple[int, int] | None:
    """Числовые идентификаторы чата и сообщения в Telegram.

    У записей, не проходивших через Telegram (перенесённая история, служебные записи),
    идентификаторы нечисловые или их нет вовсе. Такое должно приводить к понятному
    отказу, а не к падению на приведении типа.
    """
    try:
        return (
            int(payload["external_chat_id"]),
            int(payload["provider_message_id"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def edit_delivered_message(payload: Mapping[str, Any]) -> str:
    """Заменить текст уже доставленного сообщения в Telegram.

    Возвращает, чем именно правка была сделана: ботом или аккаунтом менеджера. Порядок
    важен — сначала Telegram, потом наша база: иначе оператор увидит новый текст там,
    где у клиента остался старый.
    """
    import tg_agent

    ids = _telegram_ids(payload)
    if ids is None:
        raise RuntimeError(
            "У сообщения нет идентификатора в Telegram — менять нечего. "
            "Так бывает у перенесённой истории: она не отправлялась через эту систему."
        )
    chat_id, provider_message_id = ids
    try:
        tg_agent.api(
            "editMessageText",
            http_timeout=30,
            business_connection_id=payload["business_connection_id"],
            chat_id=chat_id,
            message_id=provider_message_id,
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
            chat_id,
            provider_message_id,
            str(payload["text"])[:4096],
        )
        return "manager_account"


def delete_delivered_message(payload: Mapping[str, Any]) -> str:
    """Удалить сообщение у обеих сторон.

    Бизнес-метод Telegram умеет удалять и наши сообщения, и сообщения клиента; обычный
    deleteMessage — запасной путь для чатов вне бизнес-подключения.
    """
    import tg_agent

    ids = _telegram_ids(payload)
    if ids is None:
        # Локальная запись всё равно помечена удалённой — но честно скажем, что в
        # Telegram сообщения не тронули.
        return "local_only"
    chat_id, provider_message_id = ids
    try:
        tg_agent.api(
            "deleteBusinessMessages",
            http_timeout=30,
            business_connection_id=payload["business_connection_id"],
            message_ids=[provider_message_id],
        )
        return "bot"
    except RuntimeError as exc:
        if _peer_unknown_to_bot(exc):
            import tg_userbot

            if not tg_userbot.session_ready():
                raise RuntimeError(_no_peer_access_message(exc)) from exc
            tg_userbot.delete_message(chat_id, provider_message_id)
            return "manager_account"
        tg_agent.api(
            "deleteMessage",
            http_timeout=30,
            chat_id=chat_id,
            message_id=provider_message_id,
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
    conversation: Mapping[str, Any] | None = None,
) -> PreparedReply:
    """Turn an ИУ decision into one atomic Telegram text for the durable outbox."""

    import iu_contract
    import tg_agent

    # В боте жирный доезжает до клиента (собирается в разметку при отправке), в переписке
    # бизнес-аккаунта сообщение уходит обычным текстом от лица менеджера — там вычищаем всё.
    keep_bold = bool(conversation) and str(
        (conversation or {}).get("source_key") or "") == BOT_SOURCE_KEY
    body = tg_agent._strip_markup(str(outcome.reply or "").strip(), keep_bold=keep_bold)
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
    if escalate:
        metadata.update(
            _manager_request_metadata(
                "ai_escalation",
                _as_int((conversation or {}).get("id")),
            )
        )
    # Клиенту, который уже несколько раз спросил у ИИ, показываем прямой путь к человеку.
    # В переписке менеджера кнопки не нужны: там и так отвечает человек.
    if conversation and str(conversation.get("source_key") or "") == BOT_SOURCE_KEY:
        import iu_client_bot
        import iu_bot_state

        messages = _bot_messages(int(conversation["id"]))
        state = iu_bot_state.support_state(messages)
        if state.mode in {"active", "quiet"}:
            replies = iu_bot_state.support_agent_replies(messages)
            metadata["reply_markup"] = iu_client_bot.support_menu(
                offer_operator=iu_client_bot.should_offer_operator(
                    replies,
                    control_mode=str(conversation.get("control_mode") or "ai"),
                )
            )
            metadata["iu_event"] = "support_answer"
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
    if not ai_allowed_in_channel(conversation, conversation.get("external_user_id")):
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
            conversation=conversation,
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


def ai_allowed_in_channel(row: Mapping[str, Any], telegram_id: Any) -> bool:
    """Разрешены ли ответы ИИ — по каналу, из которого пришёл диалог.

    У бизнес-переписки это точечный список тестовых Telegram-ID, у клиентского бота —
    собственный рубильник. Один общий список сделал бы бота заложником настройки, которая
    задумана для другого канала: пустой rollout молча отменял бы каждый ответ.
    """

    if str(row.get("source_key") or "") == BOT_SOURCE_KEY:
        import iu_client_bot

        return iu_client_bot.enabled() and iu_client_bot.ai_answers_enabled()
    return ai_allowed(telegram_id)


def _agent_replies_allowed(outbox: Mapping[str, Any]) -> bool:
    return ai_allowed_in_channel(outbox, outbox.get("external_chat_id"))


def _outgoing_file(outbox: Mapping[str, Any]) -> dict[str, Any] | None:
    """Файл, который оператор приложил к этому сообщению, или None."""

    payload = outbox.get("payload") or {}
    if not isinstance(payload, Mapping):
        return None
    attached = payload.get("outgoing_file")
    return dict(attached) if isinstance(attached, Mapping) and attached else None


def _outgoing_files(outbox: Mapping[str, Any]) -> list[dict[str, Any]]:
    """All files for one Telegram delivery; legacy single-file payloads still work."""

    payload = outbox.get("payload") or {}
    if not isinstance(payload, Mapping):
        return []
    grouped = payload.get("outgoing_files")
    if isinstance(grouped, list):
        files = [dict(item) for item in grouped if isinstance(item, Mapping) and item]
        if len(files) >= 2:
            return files[:10]
    single = _outgoing_file(outbox)
    return [single] if single is not None else []


def _send_document_group(
    outbox: Mapping[str, Any],
    outgoing_files: list[Mapping[str, Any]],
    *,
    connection_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Send 2-10 documents as one native Telegram album with one shared caption."""

    import funnel_workspace_uploads as uploads
    import tg_agent

    stored_files = [uploads.resolve_upload(item.get("token")) for item in outgoing_files]
    rendered_caption = tg_agent.telegram_html(str(outbox.get("text") or "")[:1024])
    media: list[dict[str, Any]] = []
    multipart: dict[str, Any] = {}
    with ExitStack() as stack:
        for index, stored in enumerate(stored_files):
            field = f"document{index}"
            handle = stack.enter_context(open(stored["path"], "rb"))
            multipart[field] = (stored["file_name"], handle, stored["mime_type"])
            item: dict[str, Any] = {
                "type": "document",
                "media": f"attach://{field}",
            }
            if index == 0 and rendered_caption:
                item["caption"] = rendered_caption
                item["parse_mode"] = "HTML"
            media.append(item)
        sent = tg_agent.api_multipart(
            "sendMediaGroup",
            files=multipart,
            chat_id=int(outbox["external_chat_id"]),
            media=media,
            **({"business_connection_id": connection_id} if connection_id else {}),
        )

    messages = list(sent) if isinstance(sent, list) else []
    provider_ids = [
        str(item.get("message_id"))
        for item in messages
        if isinstance(item, Mapping) and item.get("message_id") is not None
    ]
    delivered: list[dict[str, Any]] = []
    for index, item in enumerate(messages):
        document = item.get("document") if isinstance(item, Mapping) else None
        if not isinstance(document, Mapping) or not document.get("file_id"):
            continue
        stored = stored_files[min(index, len(stored_files) - 1)]
        delivered.append(
            {
                "file_id": str(document["file_id"]),
                "file_unique_id": str(document.get("file_unique_id") or "") or None,
                "file_name": stored["file_name"],
                "mime_type": stored["mime_type"],
                "file_size": document.get("file_size") or stored["file_size"],
                "media_type": "document",
            }
        )
    if len(delivered) != len(outgoing_files):
        log.warning(
            "outbox %s delivered a media group without all provider file ids",
            outbox.get("id"),
        )
    provider_media = (
        {**delivered[0], "media_group": delivered, "provider_message_ids": provider_ids}
        if delivered
        else None
    )
    return (provider_ids[0] if provider_ids else None), provider_media


def _send_document(
    outbox: Mapping[str, Any],
    outgoing_file: Mapping[str, Any],
    *,
    connection_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Отправить приложенный файл в тот же Telegram-диалог, что и текст.

    Текст становится подписью к документу: клиент получает одно сообщение, а не
    файл и отдельную реплику. Пропавший на диске файл — отказ, а не пустая отправка.
    """

    import funnel_workspace_uploads as uploads
    import tg_agent

    stored = uploads.resolve_upload(outgoing_file.get("token"))
    caption = str(outbox.get("text") or "")[:1024]
    with open(stored["path"], "rb") as handle:
        sent = tg_agent.api_multipart(
            "sendDocument",
            files={"document": (stored["file_name"], handle, stored["mime_type"])},
            chat_id=int(outbox["external_chat_id"]),
            caption=caption or None,
            **({"business_connection_id": connection_id} if connection_id else {}),
        )

    provider_message_id = (
        str(sent.get("message_id"))
        if isinstance(sent, Mapping) and sent.get("message_id") is not None
        else None
    )
    document = sent.get("document") if isinstance(sent, Mapping) else None
    file_id = str(document.get("file_id") or "").strip() if isinstance(document, Mapping) else ""
    if not file_id:
        # Доставка состоялась, но показать вложение в ленте нечем — врать об этом нельзя.
        log.warning(
            "outbox %s delivered a document without a provider file id", outbox.get("id")
        )
        return provider_message_id, None
    return provider_message_id, {
        "file_id": file_id,
        "file_unique_id": str(document.get("file_unique_id") or "") or None,
        "file_name": stored["file_name"],
        "mime_type": stored["mime_type"],
        "file_size": document.get("file_size") or stored["file_size"],
        "media_type": "document",
    }


def _send_text(
    outbox: Mapping[str, Any],
    *,
    connection_id: str,
    keyboard: Any,
    formatted: bool,
) -> Mapping[str, Any]:
    """Отправить текст клиенту: с жирными заголовками там, где это бот.

    Разметка — украшение, доставка — обязательство. Если Telegram не примет разметку (модель
    написала что-то, что он разберёт как сломанный тег), то же сообщение уходит обычным
    текстом, а не теряется. В переписке бизнес-аккаунта разметки нет вовсе: там сообщение
    отправляется от лица менеджера обычным текстом."""
    import tg_agent

    text = str(outbox.get("text") or "")[:4096]
    common = {
        "http_timeout": 45,
        "chat_id": int(outbox["external_chat_id"]),
        "link_preview_options": {"is_disabled": True},
        **({"business_connection_id": connection_id} if connection_id else {}),
        **({"reply_markup": keyboard} if keyboard else {}),
    }
    if formatted:
        rendered = tg_agent.telegram_html(text)
        # Ссылка — такой же повод включить разметку, как и жирный: без HTML-режима клиент
        # получил бы markdown-скобки, а не кликабельную подпись.
        if "<b>" in rendered or "<a href=" in rendered:
            try:
                return tg_agent.api("sendMessage", text=rendered[:4096],
                                    parse_mode="HTML", **common)
            except RuntimeError as exc:
                if _peer_unknown_to_bot(exc):
                    raise
                log.warning("Telegram не принял разметку, шлём обычным текстом: %s",
                            _safe_error(exc))
    return tg_agent.api("sendMessage", text=text, **common)


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
    # У каждого канала свой рубильник: список тестовых ID бизнес-контура не должен
    # отменять ответы клиентского бота, и наоборот.
    if current.get("author_type") == "agent" and not _agent_replies_allowed(current):
        store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="cancelled",
            error="AI rollout was disabled before delivery.",
        )
        return

    # Диалог, пришедший в бота напрямую, отвечается обычным сообщением: бизнес-подключения
    # у такого чата нет и быть не может. У диалога бизнес-аккаунта наоборот — без подключения
    # отправлять нельзя, иначе ответ придёт клиенту «от бота», а не от менеджера.
    direct_bot_chat = str(current.get("source_key") or "") == BOT_SOURCE_KEY
    connection_id = ""
    if not direct_bot_chat:
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

    outgoing_files = _outgoing_files(current)
    outgoing_file = outgoing_files[0] if outgoing_files else None
    provider_media: dict[str, Any] | None = None

    try:
        try:
            if len(outgoing_files) > 1:
                provider_message_id, provider_media = _send_document_group(
                    current,
                    outgoing_files,
                    connection_id=connection_id,
                )
            elif outgoing_file is not None:
                provider_message_id, provider_media = _send_document(
                    current,
                    outgoing_file,
                    connection_id=connection_id,
                )
            else:
                payload = current.get("payload")
                keyboard = (payload or {}).get("reply_markup") if isinstance(payload, Mapping) else None
                sent = _send_text(
                    current,
                    connection_id=connection_id,
                    keyboard=keyboard,
                    formatted=direct_bot_chat,
                )
                provider_message_id = (
                    str(sent.get("message_id"))
                    if isinstance(sent, Mapping) and sent.get("message_id") is not None
                    else None
                )
        except RuntimeError as exc:
            if not _peer_unknown_to_bot(exc):
                raise
            if outgoing_file is not None:
                # Аккаунт менеджера умеет только текст, и подменять файл текстом нельзя:
                # оператор должен увидеть отказ, а не решить, что документ ушёл.
                raise RuntimeError(
                    "Файл не отправлен: бот не может писать этому собеседнику, "
                    "а через аккаунт менеджера файлы не отправляются. "
                    "Дождитесь сообщения клиента или отправьте файл вручную."
                ) from exc
            # Telegram отдаёт боту доступ только к тем собеседникам, кто написал в
            # бизнес-аккаунт после подключения бота. Остальным бот написать не может
            # вовсе — но у аккаунта менеджера диалог есть, и он может.
            provider_message_id = _send_as_manager_account(current, exc)
        finished = store.finish_outbox(
            outbox_id,
            worker_id=worker_id,
            result="sent",
            provider_message_id=provider_message_id,
            provider_media=provider_media,
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
        if outbox.get("author_type") == "operator":
            try:
                handled_at = outbox.get("created_at")
                _store().mark_manager_request_handled(
                    conversation_id,
                    now=handled_at if isinstance(handled_at, datetime) else None,
                )
            except Exception as exc:  # noqa: BLE001 - reply was delivered; badge cleanup is retry-safe
                log.warning(
                    "workspace manager-request badge was not cleared for %s: %s",
                    conversation_id,
                    _safe_error(exc),
                )
        asset = str(payload.get("asset") or "")
        telegram_id = _as_int(outbox.get("external_chat_id"))
        if asset == "terms" and telegram_id:
            tg_agent._mark_terms_sent(telegram_id)
        elif asset == "form" and telegram_id:
            tg_agent._mark_invited(telegram_id)
        if (
            str(outbox.get("source_key") or "") == BOT_SOURCE_KEY
            and str(payload.get("iu_event") or "") == "support_answer"
        ):
            try:
                import iu_bot_reminders

                iu_bot_reminders.cancel_all(conversation_id)
                iu_bot_reminders.schedule_after_answer(
                    conversation_id,
                    anchor_message_id=int(outbox.get("message_id") or 0),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "iu client bot: after-answer reminder was not scheduled: %s",
                    _safe_error(exc),
                )

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
        # Клиент получил ответ по существу, а без ответа осталась только часть вопроса —
        # разговор у ИИ не забираем: обращение поднимается в очередь оператора, но клиент
        # может спрашивать дальше и получать ответы. Полная передача остаётся там, где
        # агент не ответил вовсе или ответ не доставлен.
        if result == "sent" and payload.get("answered_client"):
            try:
                _store().flag_needs_human(conversation_id, reason=reason)
            except Exception as exc:  # noqa: BLE001 — пометка не важнее уже доставленного ответа
                log.warning("не удалось пометить обращение %s: %s",
                            conversation_id, _safe_error(exc))
            return
        if str(outbox.get("source_key") or "") == BOT_SOURCE_KEY:
            _cancel_bot_reminders(conversation_id)
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
            manager_requested=True,
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

    manager_notified = bool(payload.get("notify_manager_after_delivery"))
    if manager_notified:
        import funnel_workspace

        conversation_id = int(action["conversation_id"])
        dialog_url = funnel_workspace.conversation_url(conversation_id)
        if dialog_url.startswith("/"):
            dialog_url = f"https://www.m4s.ru{dialog_url}"
        recipient = str(
            payload.get("manager_notification_recipient")
            or os.getenv("IU_MANAGER_NOTIFY_BITRIX_USER_ID", "16")
        ).strip()
        bot_id = (
            _as_int(payload.get("manager_notification_bot_id"))
            or _as_int(os.getenv("IU_AGENT_BOT_ID", "86"))
            or 86
        )
        client_name = str(
            payload.get("manager_notification_client_name") or "Клиент"
        ).strip()
        client_label = (
            f"Клиент {client_name}"
            if client_name and client_name.casefold() != "клиент"
            else "Клиент"
        )
        tg_agent.mcp_call(
            "notify_iu_group",
            {
                "text": (
                    f"{client_label} позвал менеджера в "
                    f"[URL={dialog_url}]диалоге[/URL]"
                ),
                "dialog_id": recipient,
                "bot_id": bot_id,
            },
        )

    escalated = bool(
        payload.get("author_type") == "agent"
        and payload.get("escalate_after_delivery")
    )
    if escalated:
        reason = str(
            payload.get("escalation_reason")
            or "После доставленного ответа ИИ нужен ответ человека."
        )
        # Ответ по существу клиент получил, без ответа осталась часть вопроса — обращение
        # поднимается в очередь оператора, но разговор остаётся у ИИ (владелец, 28.07.2026).
        if payload.get("answered_client"):
            try:
                _store().flag_needs_human(int(action["conversation_id"]), reason=reason)
            except Exception as exc:  # noqa: BLE001 — пометка не важнее доставленного ответа
                log.warning("не удалось пометить обращение %s: %s",
                            action.get("conversation_id"), _safe_error(exc))
        else:
            _mark_waiting_if_current(
                int(action["conversation_id"]),
                expected_version=int(payload.get("conversation_version") or 0),
                reason=reason,
            )
    return {
        "conversation_id": int(action["conversation_id"]),
        "status": "applied",
        "asset": applied_asset,
        "escalated": escalated,
        "manager_notified": manager_notified,
    }


def _safe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return (text or exc.__class__.__name__)[:4000]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
