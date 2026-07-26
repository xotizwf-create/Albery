from __future__ import annotations

# Синхронизация этапа: статус обращения обязан догонять сделку, которую двигают в CRM.

import sys
from types import SimpleNamespace

import requests

import funnel_telegram_gateway as gateway


def test_nonempty_malformed_ai_allowlist_fails_closed(monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "1")
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ALLOW_IDS", "not-an-id")

    assert gateway.ai_allow_ids() == set()
    assert not gateway.ai_allowed(123)


def test_empty_allowlist_blocks_everyone_even_when_ai_enabled(monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ALLOW_IDS", "")
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "0")
    assert not gateway.ai_allowed(123)

    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "1")
    assert gateway.ai_allow_ids() == set()
    assert not gateway.ai_allowed(123)

    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ALLOW_IDS", "*")
    assert gateway.ai_allow_ids() is None
    assert gateway.ai_allowed(123)


def test_media_without_caption_is_visible_to_operator():
    assert gateway.telegram_message_text(
        {"document": {"file_name": "условия.pdf"}}
    ) == "[Документ: условия.pdf]"
    assert gateway.telegram_message_text({"voice": {"file_id": "x"}}) == (
        "[Голосовое сообщение]"
    )


def test_dialog_turn_uses_latest_client_batch_and_prior_history():
    messages = [
        {"id": 1, "author_type": "client", "text": "Здравствуйте", "delivery_status": "sent"},
        {"id": 2, "author_type": "agent", "text": "Добрый день", "delivery_status": "sent"},
        {"id": 3, "author_type": "client", "text": "Сколько стоит?", "delivery_status": "sent"},
        {"id": 4, "author_type": "client", "text": "И какие сроки?", "delivery_status": "sent"},
        # A cancelled draft must not leak into the model's history.
        {"id": 5, "author_type": "agent", "text": "Старый ответ", "delivery_status": "cancelled"},
        {"id": 6, "author_type": "operator", "text": "Черновик", "delivery_status": "pending"},
        {"id": 7, "author_type": "agent", "text": "Неясная доставка", "delivery_status": "unknown"},
        # This message is newer than the trigger and must not enter the turn.
        {"id": 8, "author_type": "client", "text": "Позднее сообщение", "delivery_status": "sent"},
    ]

    turn = gateway.dialog_turn(messages, trigger_message_id=4)

    assert turn.texts == ("Сколько стоит?", "И какие сроки?")
    assert turn.history == "Клиент: Здравствуйте\nАгент: Добрый день"


def test_terms_delivery_persists_fact_derived_stage_before_outbox_send(monkeypatch):
    import iu_contract
    import iu_funnel
    import tg_agent

    monkeypatch.setattr(tg_agent, "_strip_markup", lambda value: str(value))
    monkeypatch.setattr(tg_agent, "terms_text", lambda: "Условия ИУ")
    outcome = SimpleNamespace(
        reply="Отправляю.",
        action=iu_contract.SEND_TERMS,
        escalate=False,
        reason="",
        stage_move="",
        answered_client=True,
        sources=(),
        trace={},
    )

    prepared = gateway.prepare_reply(
        outcome,
        telegram_user_id=123,
        facts=iu_funnel.DealFacts(stage=iu_funnel.STAGE_NEW),
    )

    assert prepared.metadata["asset"] == "terms"
    assert prepared.metadata["stage_move"] == iu_funnel.STAGE_TERMS


class FakeConflict(RuntimeError):
    pass


class FakeStoreForIngest:
    WorkspaceConflictError = FakeConflict

    def __init__(self):
        self.ingest_kwargs = None
        self.transitions = []
        self.duplicate = False

    def ingest_business_message(self, **kwargs):
        self.ingest_kwargs = kwargs
        return {
            "conversation": {
                "id": 7,
                "state_version": 2,
                "control_mode": "ai",
            },
            "message": {"id": 11},
            "duplicate": self.duplicate,
        }

    def transition_control(self, conversation_id, **kwargs):
        self.transitions.append((conversation_id, kwargs))
        return {
            "id": conversation_id,
            "state_version": 3,
            "control_mode": "paused",
        }

    def get_conversation(self, conversation_id):
        return {
            "id": conversation_id,
            "state_version": 2,
            "control_mode": "ai",
        }


def test_incoming_is_journaled_but_paused_when_ai_rollout_is_off(monkeypatch):
    store = FakeStoreForIngest()
    tg = SimpleNamespace(_business_owner_id=lambda _connection_id: 999)
    crm_calls = []
    crm = SimpleNamespace(
        ensure_conversation_deal=lambda conversation_id: crm_calls.append(
            conversation_id
        )
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    monkeypatch.setitem(sys.modules, "funnel_workspace_crm", crm)
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "0")

    conversation_id, message_id = gateway.ingest_business_message(
        {
            "message_id": 55,
            "date": 1_700_000_000,
            "business_connection_id": "connection-A",
            "chat": {"id": 123, "type": "private", "first_name": "Иван"},
            "from": {"id": 123, "first_name": "Иван"},
            "text": "Хочу условия",
        },
        provider_update_id=88,
    )

    assert (conversation_id, message_id) == (7, 11)
    assert store.ingest_kwargs["schedule_ai"] is False
    assert store.ingest_kwargs["external_user_id"] == 123
    assert store.ingest_kwargs["business_connection_id"] == "connection-A"
    assert store.transitions[0][1]["mode"] == "paused"
    assert crm_calls == []


def test_allowlisted_incoming_schedules_ai_without_pausing(monkeypatch):
    store = FakeStoreForIngest()
    tg = SimpleNamespace(_business_owner_id=lambda _connection_id: 999)
    crm = SimpleNamespace(ensure_conversation_deal=lambda _conversation_id: None)
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    monkeypatch.setitem(sys.modules, "funnel_workspace_crm", crm)
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "1")
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ALLOW_IDS", "123")

    gateway.ingest_business_message(
        {
            "message_id": 56,
            "business_connection_id": "connection-A",
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123},
            "text": "Хочу условия",
        }
    )

    assert store.ingest_kwargs["schedule_ai"] is True
    assert store.transitions == []


def test_duplicate_replay_still_pauses_disallowed_ai(monkeypatch):
    store = FakeStoreForIngest()
    store.duplicate = True
    tg = SimpleNamespace(_business_owner_id=lambda _connection_id: 999)
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(
        sys.modules,
        "funnel_workspace_crm",
        SimpleNamespace(ensure_conversation_deal=lambda _conversation_id: None),
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "0")

    gateway.ingest_business_message(
        {
            "message_id": 56,
            "business_connection_id": "connection-A",
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123},
            "text": "Повтор",
        }
    )

    assert store.transitions[0][1]["mode"] == "paused"


def test_owner_is_resolved_for_exact_business_connection(monkeypatch):
    store = FakeStoreForIngest()
    requested_connections = []

    def owner_id(connection_id):
        requested_connections.append(connection_id)
        return 777

    monkeypatch.setitem(
        sys.modules,
        "tg_agent",
        SimpleNamespace(_business_owner_id=owner_id),
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(
        sys.modules,
        "funnel_workspace_crm",
        SimpleNamespace(ensure_conversation_deal=lambda _conversation_id: None),
    )

    gateway.ingest_business_message(
        {
            "message_id": 70,
            "business_connection_id": "connection-B",
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 777, "first_name": "Владелец"},
            "text": "Ответ",
        }
    )

    assert requested_connections == ["connection-B"]
    assert store.ingest_kwargs["author_type"] == "operator"


def test_sender_business_bot_is_never_classified_as_client(monkeypatch):
    store = FakeStoreForIngest()
    monkeypatch.setitem(
        sys.modules,
        "tg_agent",
        SimpleNamespace(_business_owner_id=lambda _connection_id: None),
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(
        sys.modules,
        "funnel_workspace_crm",
        SimpleNamespace(ensure_conversation_deal=lambda _conversation_id: None),
    )

    gateway.ingest_business_message(
        {
            "message_id": 71,
            "business_connection_id": "connection-B",
            "sender_business_bot": {"id": 44},
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 777},
            "text": "Ответ ИИ",
        }
    )

    assert store.ingest_kwargs["author_type"] == "agent"
    assert store.ingest_kwargs["schedule_ai"] is False
    assert store.ingest_kwargs["metadata"]["sent_via_business_bot"] is True


def test_edited_and_deleted_updates_route_to_durable_store(monkeypatch):
    class RouteStore:
        def __init__(self):
            self.deleted = None

        def ingest_business_message(self, **kwargs):
            assert kwargs["is_edit"] is True
            return {
                "conversation": {"id": 9, "state_version": 2, "control_mode": "paused"},
                "message": {"id": 10},
                "duplicate": False,
            }

        def tombstone_business_messages(self, **kwargs):
            self.deleted = kwargs
            return {"conversation": {"id": 9}, "message_id": 10}

    store = RouteStore()
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(
        sys.modules,
        "tg_agent",
        SimpleNamespace(_business_owner_id=lambda _connection_id: 999),
    )
    monkeypatch.setitem(
        sys.modules,
        "funnel_workspace_crm",
        SimpleNamespace(ensure_conversation_deal=lambda _conversation_id: None),
    )

    edited = gateway.route_captured_update(
        {
            "edited_business_message": {
                "message_id": 80,
                "business_connection_id": "connection-A",
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123},
                "text": "Исправлено",
            }
        },
        provider_update_id=100,
    )
    deleted = gateway.route_captured_update(
        {
            "deleted_business_messages": {
                "business_connection_id": "connection-A",
                "chat": {"id": 123, "type": "private"},
                "message_ids": [80],
            }
        },
        provider_update_id=101,
    )

    assert edited == (9, 10)
    assert deleted == (9, 10)
    assert store.deleted["external_message_ids"] == [80]


def test_media_metadata_keeps_download_identifiers():
    metadata = gateway.telegram_media_metadata(
        {
            "photo": [
                {"file_id": "small", "file_unique_id": "u1", "width": 10, "height": 10},
                {
                    "file_id": "large",
                    "file_unique_id": "u2",
                    "file_size": 999,
                    "width": 100,
                    "height": 100,
                },
            ]
        }
    )

    assert metadata["telegram_media"]["file_id"] == "large"
    assert metadata["telegram_media"]["file_unique_id"] == "u2"
    assert metadata["telegram_media"]["file_size"] == 999


def test_capture_failure_backoff_is_bounded():
    assert gateway._as_int("12") == 12
    import tg_agent

    assert tg_agent._capture_failure_delay(1) == 1
    assert tg_agent._capture_failure_delay(3) == 4
    assert tg_agent._capture_failure_delay(99) == 30


def test_business_owner_lookup_is_scoped_to_connection(monkeypatch):
    import tg_agent

    monkeypatch.setattr(
        tg_agent,
        "load_state",
        lambda: {
            "business": {
                "connection-A": {"user_id": 111},
                "connection-B": {"user_id": 222},
            }
        },
    )

    assert tg_agent._business_owner_id("connection-A") == 111
    assert tg_agent._business_owner_id("connection-B") == 222
    assert tg_agent._business_owner_id("missing") is None


def test_raw_updates_are_claimed_one_at_a_time_to_preserve_order(monkeypatch):
    seen = {}

    class UpdateStore:
        def claim_updates(self, **kwargs):
            seen.update(kwargs)
            return []

    monkeypatch.setitem(sys.modules, "funnel_workspace_store", UpdateStore())

    assert gateway.process_updates_once(worker_id="worker", limit=25) == 0
    assert seen["limit"] == 1
    assert seen["lane"] == "business"
    assert seen["source_key"] == "telegram"


def test_bot_updates_use_an_independent_raw_lane(monkeypatch):
    seen = {}

    class UpdateStore:
        def claim_updates(self, **kwargs):
            seen.update(kwargs)
            return []

    monkeypatch.setitem(sys.modules, "funnel_workspace_store", UpdateStore())

    assert gateway.process_bot_updates_once(worker_id="bot-worker", limit=25) == 0
    assert seen["limit"] == 1
    assert seen["lane"] == "bot"
    assert seen["source_key"] == "telegram"


def test_owner_bot_dm_is_dispatched_to_existing_worker_pool(monkeypatch):
    submitted = []
    handled = []

    class WorkerPool:
        def submit(self, function, message):
            submitted.append((function, message))

    def handle_update_safely(update):
        handled.append(update)

    tg = SimpleNamespace(
        is_owner=lambda sender: sender.get("id") == 7,
        _workers=WorkerPool(),
        _handle_update_safely=handle_update_safely,
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    message = {
        "message_id": 12,
        "chat": {"id": 7, "type": "private"},
        "from": {"id": 7},
        "text": "Статус?",
    }

    assert gateway.route_captured_update({"message": message}) == (None, None)
    assert submitted == [(handle_update_safely, {"message": message})]
    assert handled == []


def test_non_owner_bot_dm_is_silently_ignored(monkeypatch):
    class WorkerPool:
        def submit(self, *_args):
            raise AssertionError("non-owner message must not be dispatched")

    tg = SimpleNamespace(
        is_owner=lambda _sender: False,
        _workers=WorkerPool(),
        _handle_update_safely=lambda _update: (_ for _ in ()).throw(
            AssertionError("non-owner message must not be handled")
        ),
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    assert gateway.route_captured_update(
        {
            "message": {
                "message_id": 13,
                "chat": {"id": 99, "type": "private"},
                "from": {"id": 99},
                "text": "Привет",
            }
        }
    ) == (None, None)


class FakeStoreForOutbox:
    def __init__(self):
        self.finishes = []

    def outbox_send_guard(self, _outbox_id, *, worker_id):
        return {"allowed": True, "outbox": self.item}

    def begin_outbox_send(self, _outbox_id, *, worker_id, lease_seconds):
        self.item = {**self.item, "delivery_status": "sending"}
        return {"allowed": True, "outbox": self.item}

    def finish_outbox(self, outbox_id, **kwargs):
        self.finishes.append((outbox_id, kwargs))
        return {
            "outbox": {
                **self.item,
                "delivery_status": kwargs["result"],
            },
            "message": {},
        }


def _operator_outbox():
    return {
        "id": 8,
        "conversation_id": 7,
        "conversation_version": 3,
        "author_type": "operator",
        "external_chat_id": "123",
        "business_connection_id": "connection-A",
        "text": "Ответ менеджера",
        "payload": {},
    }


def test_outbox_uses_exact_connection_and_persists_provider_message_id(monkeypatch):
    store = FakeStoreForOutbox()
    store.item = _operator_outbox()
    calls = []

    def api(method, **kwargs):
        calls.append((method, kwargs))
        return {"message_id": 456}

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: (preferred, ""),
        api=api,
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    gateway._process_outbox_item(store.item, worker_id="worker")

    assert calls[0][1]["business_connection_id"] == "connection-A"
    assert calls[0][1]["chat_id"] == 123
    assert store.finishes[0][1]["result"] == "sent"
    assert store.finishes[0][1]["provider_message_id"] == "456"


def test_ambiguous_network_failure_is_never_automatically_retried(monkeypatch):
    store = FakeStoreForOutbox()
    store.item = _operator_outbox()

    def api(_method, **_kwargs):
        raise requests.Timeout("provider timed out")

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: (preferred, ""),
        api=api,
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    gateway._process_outbox_item(store.item, worker_id="worker")

    assert store.finishes[0][1]["result"] == "unknown"
    assert "retry_at" not in store.finishes[0][1]


def test_post_delivery_stage_is_not_called_synchronously(monkeypatch):
    calls = []
    tg = SimpleNamespace(
        _mark_terms_sent=lambda _telegram_id: None,
        _mark_invited=lambda _telegram_id: None,
        _move_deal_stage=lambda *_args, **_kwargs: calls.append("unsafe-direct-call"),
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    gateway._after_delivery(
        {
            "conversation_id": 7,
            "conversation_version": 3,
            "author_type": "operator",
            "external_chat_id": "123",
            "payload": {"stage_move": "C16:TERMS"},
        },
        result="sent",
        finished={},
    )

    assert calls == []


class FakeStoreForCrmActions:
    class WorkspaceConflictError(RuntimeError):
        pass

    def __init__(self, action):
        self.action = action
        self.completed = []
        self.retried = []

    def claim_crm_actions(self, **_kwargs):
        return [self.action]

    def complete_crm_action(self, action_id, **kwargs):
        self.completed.append((action_id, kwargs))
        return {**self.action, "processing_status": "done"}

    def retry_crm_action(self, action_id, **kwargs):
        self.retried.append((action_id, kwargs))
        return {**self.action, "processing_status": "dead_letter"}


def _crm_action():
    return {
        "id": 71,
        "conversation_id": 7,
        "message_id": 8,
        "outbox_id": 9,
        "action_type": "move_stage",
        "target_stage": "C16:TERMS",
        "attempts": 3,
    }


def test_durable_crm_worker_completes_verified_stage_set(monkeypatch):
    store = FakeStoreForCrmActions(_crm_action())
    applied = {
        "conversation_id": 7,
        "deal_id": 82,
        "target_stage": "C16:TERMS",
        "previous_stage": "C16:NEW",
        "status": "applied",
        "crm_link_status": "already_linked",
    }
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(
        sys.modules,
        "funnel_workspace_crm",
        SimpleNamespace(
            apply_conversation_stage_action=lambda *_args, **_kwargs: applied,
        ),
    )

    assert gateway.process_crm_actions_once(worker_id="crm-worker") == 1
    assert store.completed == [
        (71, {"worker_id": "crm-worker", "result": applied})
    ]
    assert store.retried == []


def test_durable_crm_worker_retries_with_bounded_backoff(monkeypatch):
    store = FakeStoreForCrmActions(_crm_action())
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(
        sys.modules,
        "funnel_workspace_crm",
        SimpleNamespace(
            apply_conversation_stage_action=lambda *_args, **_kwargs: (
                _ for _ in ()
            ).throw(RuntimeError("Bitrix unavailable")),
        ),
    )

    assert gateway.process_crm_actions_once(worker_id="crm-worker") == 1
    assert store.completed == []
    assert store.retried[0][0] == 71
    assert store.retried[0][1]["delay_seconds"] == 45
    assert gateway._crm_action_retry_delay(999) == 3600


def test_durable_worker_runs_ensure_deal_outside_update_router(monkeypatch):
    action = {
        **_crm_action(),
        "action_type": "ensure_deal",
        "target_stage": None,
    }
    store = FakeStoreForCrmActions(action)
    calls = []

    def ensure(conversation_id):
        calls.append(conversation_id)
        return {
            "deal_id": 82,
            "status": "created",
            "created": True,
            "recovered": False,
            "already_linked": False,
            "orphan_deal_id": None,
            "conversation": {"id": conversation_id, "deal_id": 82},
        }

    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(
        sys.modules,
        "funnel_workspace_crm",
        SimpleNamespace(ensure_conversation_deal=ensure),
    )

    assert gateway.process_crm_actions_once(worker_id="crm-worker") == 1
    assert calls == [7]
    assert store.completed[0][1]["result"]["deal_id"] == 82


def test_delivery_effects_replay_terms_and_escalation_idempotently(monkeypatch):
    marked = []
    waiting = []
    state = {"sent": False}
    tg = SimpleNamespace(
        _terms_already_sent=lambda _telegram_id: state["sent"],
        _mark_terms_sent=lambda telegram_id: (
            marked.append(telegram_id),
            state.__setitem__("sent", True),
        ),
        _invite_already_sent=lambda _telegram_id: False,
        _mark_invited=lambda _telegram_id: None,
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    monkeypatch.setattr(
        gateway,
        "_mark_waiting_if_current",
        lambda conversation_id, **kwargs: waiting.append(
            (conversation_id, kwargs)
        ),
    )
    action = {
        "id": 72,
        "conversation_id": 7,
        "action_type": "delivery_effects",
        "payload": {
            "asset": "terms",
            "telegram_id": "123",
            "author_type": "agent",
            "conversation_version": 9,
            "escalate_after_delivery": True,
            "escalation_reason": "Нужен менеджер",
        },
    }

    first = gateway._apply_delivery_effects(action)
    second = gateway._apply_delivery_effects(action)

    assert first["asset"] == "terms"
    assert second["asset"] == "terms"
    assert marked == [123]
    assert waiting == [
        (7, {"expected_version": 9, "reason": "Нужен менеджер"}),
        (7, {"expected_version": 9, "reason": "Нужен менеджер"}),
    ]


def test_stage_sync_pulls_a_stage_moved_by_people_in_crm(monkeypatch):
    """Этап двигают и люди в CRM: список обращений обязан это догонять сам,
    иначе на подписанном договоре висит «Новый клиент»."""
    updates: list[tuple[int, str]] = []

    class FakeStore:
        WorkspaceConflictError = RuntimeError

        def conversations_for_stage_sync(self, *, limit):
            assert limit == 50
            return [
                {"id": 7, "deal_id": 555, "stage_id": "C16:NEW"},
                {"id": 8, "deal_id": 556, "stage_id": "C16:NDA"},
                {"id": 9, "deal_id": None, "stage_id": ""},
            ]

        def update_crm_link(self, conversation_id, *, stage_id):
            updates.append((conversation_id, stage_id))

    monkeypatch.setattr(gateway, "_store", lambda: FakeStore())
    import funnel_workspace_crm as crm

    monkeypatch.setattr(
        crm,
        "read_deal_stage",
        lambda deal_id: "C16:UC_SGZRVS" if int(deal_id) == 555 else "C16:NDA",
    )

    changed = gateway.sync_conversation_stages_once(limit=50)

    # Диалог 8 уже на своём этапе, у диалога 9 нет сделки — трогаем только 7.
    assert updates == [(7, "C16:UC_SGZRVS")]
    assert changed == 1


def test_stage_sync_survives_a_broken_crm_answer(monkeypatch):
    class FakeStore:
        def conversations_for_stage_sync(self, *, limit):
            return [{"id": 7, "deal_id": 555, "stage_id": "C16:NEW"}]

        def update_crm_link(self, conversation_id, *, stage_id):
            raise AssertionError("этап не должен меняться при ошибке CRM")

    monkeypatch.setattr(gateway, "_store", lambda: FakeStore())
    import funnel_workspace_crm as crm

    def explode(deal_id):
        raise RuntimeError("Bitrix недоступен")

    monkeypatch.setattr(crm, "read_deal_stage", explode)

    assert gateway.sync_conversation_stages_once(limit=10) == 0
