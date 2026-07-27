from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask
from werkzeug.security import generate_password_hash

import funnel_workspace as workspace


ORIGIN = "http://localhost"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_ENABLED", "1")
    monkeypatch.setenv(
        "FUNNEL_WORKSPACE_PASSWORD_HASH",
        generate_password_hash("correct horse battery staple"),
    )
    workspace._LOGIN_ATTEMPTS.clear()
    app = Flask(__name__)
    app.secret_key = "test-secret-" * 8
    app.config.update(TESTING=True)
    workspace.register_funnel_workspace(app)
    return app.test_client()


def login(client, *, operator_name="Александр"):
    response = client.post(
        "/api/funnel-workspace/session",
        json={
            "password": "correct horse battery staple",
            "operator_name": operator_name,
        },
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200
    return response.get_json()


def test_standalone_session_does_not_grant_admin_access(client):
    anonymous = client.get("/api/funnel-workspace/session")
    assert anonymous.status_code == 200
    assert anonymous.get_json()["authenticated"] is False

    payload = login(client)
    assert payload["authenticated"] is True
    assert payload["configured"] is True
    assert payload["operator_name"] == "Александр"
    assert payload["admin_session"] is False
    assert payload["csrf_token"]

    with client.session_transaction() as browser_session:
        assert browser_session["funnel_workspace_authenticated"] is True
        assert not browser_session.get("admin_authenticated")


def test_password_rotation_invalidates_existing_workspace_session(client, monkeypatch):
    first_hash = generate_password_hash("correct horse battery staple")
    second_hash = generate_password_hash("a completely different workspace password")
    current = {"hash": first_hash}
    monkeypatch.delenv("FUNNEL_WORKSPACE_PASSWORD_HASH")
    monkeypatch.setattr(
        workspace.store,
        "get_workspace_password_hash",
        lambda: current["hash"],
    )

    payload = login(client)
    assert payload["authenticated"] is True

    current["hash"] = second_hash
    response = client.get("/api/funnel-workspace/session")

    assert response.status_code == 200
    assert response.get_json()["authenticated"] is False


def test_enabled_workspace_fails_closed_with_insecure_session_secret(client):
    client.application.secret_key = "change-this-secret"

    response = client.get("/api/funnel-workspace/session")

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "session_secret_not_configured"


def test_workspace_api_requires_dedicated_or_admin_session(client):
    response = client.get("/api/funnel-workspace/conversations")

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "authentication_required"


def test_workspace_logout_remains_available_while_feature_is_disabled(
    client,
    monkeypatch,
):
    payload = login(client)
    monkeypatch.setenv("FUNNEL_WORKSPACE_ENABLED", "0")

    response = client.delete(
        "/api/funnel-workspace/session",
        json={"csrf_token": payload["csrf_token"]},
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": payload["csrf_token"],
        },
    )

    assert response.status_code == 200
    assert response.get_json()["authenticated"] is False
    with client.session_transaction() as browser_session:
        assert not browser_session.get("funnel_workspace_authenticated")


def test_mark_read_uses_visible_message_watermark(client, monkeypatch):
    payload = login(client)
    captured = {}

    def mark_read(conversation_id, *, through_message_id):
        captured.update(
            conversation_id=conversation_id,
            through_message_id=through_message_id,
        )
        return {"id": conversation_id, "unread_count": 2}

    monkeypatch.setattr(workspace.store, "mark_read", mark_read)
    response = client.post(
        "/api/funnel-workspace/conversations/41/read",
        json={
            "through_message_id": 500,
            "csrf_token": payload["csrf_token"],
        },
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": payload["csrf_token"],
        },
    )

    assert response.status_code == 200
    assert captured == {"conversation_id": 41, "through_message_id": 500}
    assert response.get_json()["conversation"]["unread_count"] == 2


def test_admin_session_is_accepted(client, monkeypatch):
    monkeypatch.setattr(
        workspace.store,
        "list_conversations",
        lambda **_kwargs: {"items": [], "total": 0, "limit": 100, "offset": 0},
    )
    with client.session_transaction() as browser_session:
        browser_session["admin_authenticated"] = True

    response = client.get("/api/funnel-workspace/conversations")

    assert response.status_code == 200
    assert response.get_json()["conversations"] == []


def test_api_maps_durable_names_to_frontend_contract(client, monkeypatch):
    login(client)
    monkeypatch.setattr(
        workspace.store,
        "list_conversations",
        lambda **_kwargs: {
            "items": [
                {
                    "id": 41,
                    "source_key": "telegram",
                    "external_chat_id": "9001",
                    "username": "client",
                    "display_name": None,
                    "last_message_text": "Вопрос",
                    "control_mode": "paused",
                }
            ],
            "total": 1,
            "limit": 100,
            "offset": 0,
        },
    )
    monkeypatch.setattr(
        workspace.store,
        "list_messages",
        lambda *_args, **_kwargs: [
            {
                "id": 10,
                "author_type": "agent",
                "direction": "outbound",
                "occurred_at": datetime(2026, 7, 26, tzinfo=timezone.utc),
                "text": "Ответ",
                "error_detail": None,
                "metadata": {
                    "telegram_media_type": "document",
                    "telegram_media": {
                        "file_id": "provider-file-id",
                        "file_name": "условия.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            }
        ],
    )

    conversations = client.get("/api/funnel-workspace/conversations").get_json()
    messages = client.get(
        "/api/funnel-workspace/conversations/41/messages"
    ).get_json()

    item = conversations["conversations"][0]
    assert item["source"] == "telegram"
    assert item["last_message"] == "Вопрос"
    assert item["display_name"] == "@client"
    assert item["control_mode"] == "paused"
    assert item["control_mode_internal"] == "paused"
    assert messages["messages"][0]["author_type"] == "ai"
    assert messages["messages"][0]["direction"] == "outgoing"
    assert messages["messages"][0]["attachment"]["file_name"] == "условия.pdf"
    assert "metadata" not in messages["messages"][0]
    assert "file_id" not in messages["messages"][0]["attachment"]


def test_mutation_requires_csrf_and_origin(client, monkeypatch):
    payload = login(client)
    called = {}

    def transition(conversation_id, **kwargs):
        called.update({"conversation_id": conversation_id, **kwargs})
        return {
            "id": conversation_id,
            "control_mode": kwargs["mode"],
            "state_version": 8,
        }

    monkeypatch.setattr(workspace.store, "transition_control", transition)
    body = {"mode": "human", "expected_version": 7}

    missing = client.post(
        "/api/funnel-workspace/conversations/41/control",
        json=body,
        headers={"Origin": ORIGIN},
    )
    assert missing.status_code == 403
    assert missing.get_json()["error"]["code"] == "csrf_failed"

    accepted = client.post(
        "/api/funnel-workspace/conversations/41/control",
        json=body,
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": payload["csrf_token"],
        },
    )
    assert accepted.status_code == 200
    assert called["conversation_id"] == 41
    assert called["actor_name"] == "Александр"


def test_operator_send_contract_uses_version_and_idempotency(client, monkeypatch):
    payload = login(client)
    captured = {}

    def enqueue(conversation_id, **kwargs):
        captured.update({"conversation_id": conversation_id, **kwargs})
        return {
            "conversation": {"id": conversation_id, "state_version": 6},
            "message": {"id": 90, "text": kwargs["text"]},
            "outbox": {"id": 91, "delivery_status": "pending"},
            "duplicate": False,
        }

    monkeypatch.setattr(workspace.store, "enqueue_outgoing_operator", enqueue)
    response = client.post(
        "/api/funnel-workspace/conversations/41/messages",
        json={"text": "Добрый день", "expected_version": 5},
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": payload["csrf_token"],
            "Idempotency-Key": "browser-request-1",
        },
    )

    assert response.status_code == 201
    assert captured["expected_version"] == 5
    assert captured["idempotency_key"] == "browser-request-1"
    assert captured["operator_name"] == "Александр"


def test_store_conflict_is_structured_json(client, monkeypatch):
    payload = login(client)
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "1")
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ALLOW_IDS", "9001")
    monkeypatch.setattr(
        workspace.store,
        "get_conversation",
        lambda _conversation_id: {"external_user_id": 9001},
    )

    def conflict(*_args, **_kwargs):
        raise workspace.store.WorkspaceConflictError(
            "Диалог уже изменился.",
            details={"expected_version": 4, "current_version": 5},
        )

    monkeypatch.setattr(workspace.store, "transition_control", conflict)
    response = client.post(
        "/api/funnel-workspace/conversations/41/control",
        json={"mode": "ai", "expected_version": 4},
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": payload["csrf_token"],
        },
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": {
            "code": "state_conflict",
            "message": "Диалог уже изменился.",
            "details": {"expected_version": 4, "current_version": 5},
        }
    }


def test_ui_cannot_enable_ai_outside_test_rollout(client, monkeypatch):
    payload = login(client)
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "1")
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ALLOW_IDS", "9002")
    monkeypatch.setattr(
        workspace.store,
        "get_conversation",
        lambda _conversation_id: {"external_user_id": 9001},
    )
    called = False

    def transition(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(workspace.store, "transition_control", transition)
    response = client.post(
        "/api/funnel-workspace/conversations/41/control",
        json={"mode": "ai", "expected_version": 4},
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": payload["csrf_token"],
        },
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "ai_rollout_disabled"
    assert called is False


def test_csv_export_blocks_spreadsheet_formula_injection(client, monkeypatch):
    login(client)
    monkeypatch.setattr(
        workspace.store,
        "message_export_rows",
        lambda **_kwargs: [
            {
                "message_id": 1,
                "occurred_at": datetime(2026, 7, 26, tzinfo=timezone.utc),
                "author_type": "client",
                "text": "=HYPERLINK(\"https://invalid\")",
                "conversation_id": 2,
            }
        ],
    )

    response = client.get("/api/funnel-workspace/export.csv")

    assert response.status_code == 200
    assert response.data.startswith(b"\xef\xbb\xbf")
    assert "'=HYPERLINK" in response.get_data(as_text=True)


def test_failed_login_is_independently_rate_limited(client, monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_AUTH_RATE_LIMIT_ATTEMPTS", "2")
    for _ in range(2):
        response = client.post(
            "/api/funnel-workspace/session",
            json={"password": "wrong"},
            headers={"Origin": ORIGIN, "X-Real-IP": "192.0.2.10"},
        )
        assert response.status_code == 401

    blocked = client.post(
        "/api/funnel-workspace/session",
        json={"password": "correct horse battery staple"},
        headers={"Origin": ORIGIN, "X-Real-IP": "192.0.2.10"},
    )
    assert blocked.status_code == 429
    assert blocked.get_json()["error"]["code"] == "rate_limited"


def test_operator_name_is_bound_to_the_password_not_typed_at_login(client, monkeypatch):
    # Под одним входом работает один названный сотрудник: имя берётся из настройки,
    # а не из поля формы, иначе в переписке будут «Юля», «юлия» и пустая подпись.
    monkeypatch.setattr(workspace.store, "get_workspace_operator_name", lambda: "Юлия")

    payload = client.post(
        "/api/funnel-workspace/session",
        json={"password": "correct horse battery staple", "operator_name": "кто угодно"},
        headers={"Origin": ORIGIN},
    ).get_json()

    assert payload["operator_name"] == "Юлия"


def test_session_reports_the_configured_operator_before_login(client, monkeypatch):
    monkeypatch.setattr(workspace.store, "get_workspace_operator_name", lambda: "Юлия")

    payload = client.get("/api/funnel-workspace/session").get_json()

    assert payload["authenticated"] is False
    assert payload["configured_operator_name"] == "Юлия"


def test_meta_publishes_the_iu_funnel_stages_in_owner_order(client, monkeypatch):
    monkeypatch.setattr(workspace.store, "list_sources", lambda: [])
    login(client)

    stages = client.get("/api/funnel-workspace/meta").get_json()["funnel_stages"]

    import iu_funnel

    assert [stage["value"] for stage in stages] == [item.id for item in iu_funnel.CHAIN]
    assert [stage["label"] for stage in stages] == [item.title for item in iu_funnel.CHAIN]
    assert stages[0]["label"] == "Новый клиент"


def test_conversation_page_opens_by_its_own_link(client, monkeypatch):
    """Ссылку /agent-funnels/12 присылает напоминание — она обязана открывать страницу,
    а не отдавать 404."""
    client.application.add_url_rule("/", "index", lambda: "ok")
    with client.session_transaction() as browser_session:
        browser_session["admin_authenticated"] = True

    assert client.get("/agent-funnels/12").status_code == 200
    assert client.get("/agent-funnels").status_code == 200


def test_conversation_payload_carries_its_link_and_urgency(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("FUNNEL_WORKSPACE_PUBLIC_BASE", "https://www.m4s.ru")
    waiting = datetime.now(timezone.utc) - timedelta(minutes=40)
    monkeypatch.setattr(
        workspace.store,
        "list_conversations",
        lambda **kwargs: {
            "items": [
                {
                    "id": 12,
                    "source_key": "telegram",
                    "external_chat_id": "9001",
                    "display_name": "Иван",
                    "username": "ivan",
                    "status": "open",
                    "control_mode": "ai",
                    "state_version": 3,
                    "unread_count": 1,
                    "reply_deadline_at": None,
                    "awaiting_reply_since": waiting,
                }
            ],
            "total": 1,
            "limit": 100,
            "offset": 0,
        },
    )
    login(client)

    payload = client.get("/api/funnel-workspace/conversations").get_json()
    conversation = payload["conversations"][0]

    assert conversation["url"] == "https://www.m4s.ru/agent-funnels/12"
    assert conversation["urgency"] == "urgent"
    assert conversation["waiting_minutes"] >= 40


def test_session_always_carries_a_csrf_token_for_the_bootstrap_form(client):
    # Страницу открывают до входа: форма первичной установки пароля обязана получить токен,
    # иначе отправка упирается в «CSRF-токен отсутствует или устарел».
    payload = client.get("/api/funnel-workspace/session").get_json()

    assert payload["authenticated"] is False
    assert payload["csrf_token"]


def test_password_bootstrap_works_when_the_page_was_opened_before_admin_login(
    client,
    monkeypatch,
):
    """Живой случай 26.07.2026: вкладка с /agent-funnels открыта заранее, вход в кабинет
    произошёл позже, и отправка формы падала с csrf_failed."""
    monkeypatch.delenv("FUNNEL_WORKSPACE_PASSWORD_HASH")
    monkeypatch.setenv(
        "ADMIN_PASSWORD_HASH",
        generate_password_hash("current admin password"),
    )
    monkeypatch.setattr(workspace.store, "get_workspace_password_hash", lambda: "")
    monkeypatch.setattr(workspace.store, "set_workspace_password_hash", lambda value: None)

    opened_before_login = client.get("/api/funnel-workspace/session").get_json()
    csrf_token = opened_before_login["csrf_token"]

    with client.session_transaction() as browser_session:
        browser_session["admin_authenticated"] = True

    response = client.post(
        "/api/funnel-workspace/configure-password",
        json={
            "admin_password": "current admin password",
            "new_password": "separate workspace password",
            "csrf_token": csrf_token,
        },
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200


def test_admin_can_bootstrap_only_scrypt_hash_without_secret_in_response(
    client,
    monkeypatch,
):
    monkeypatch.delenv("FUNNEL_WORKSPACE_PASSWORD_HASH")
    monkeypatch.setenv(
        "ADMIN_PASSWORD_HASH",
        generate_password_hash("current admin password"),
    )
    monkeypatch.setattr(workspace.store, "get_workspace_password_hash", lambda: "")
    captured = {}

    def save_hash(value):
        captured["hash"] = value

    monkeypatch.setattr(workspace.store, "set_workspace_password_hash", save_hash)
    with client.session_transaction() as browser_session:
        browser_session["admin_authenticated"] = True

    session_response = client.get("/api/funnel-workspace/session")
    session_payload = session_response.get_json()
    assert session_payload["configured"] is False
    assert session_payload["can_configure"] is True

    response = client.post(
        "/api/funnel-workspace/configure-password",
        json={
            "admin_password": "current admin password",
            "new_password": "separate workspace password",
            "csrf_token": session_payload["csrf_token"],
        },
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": session_payload["csrf_token"],
        },
    )

    assert response.status_code == 200
    assert captured["hash"].startswith("scrypt:")
    assert "separate workspace password" not in captured["hash"]
    assert "hash" not in response.get_json()
    assert "password" not in response.get_data(as_text=True).lower()


def test_deleting_a_failed_message_purges_it_without_touching_telegram(
    client, monkeypatch
):
    """Живой случай 27.07.2026 (диалог 69, Evgenii Pal): ответ лёг с `PEER_ID_INVALID`.
    Дёргать Telegram нечем и незачем — запись просто уходит из журнала."""
    payload = login(client)
    import funnel_telegram_gateway

    calls: list[str] = []

    def refuse(_payload):
        calls.append("telegram")
        raise AssertionError("Telegram не должен трогаться для недоставленного ответа.")

    monkeypatch.setattr(funnel_telegram_gateway, "delete_delivered_message", refuse)
    monkeypatch.setattr(
        workspace.store,
        "message_delivery_target",
        lambda _message_id: {
            "message_id": 98,
            "conversation_id": 69,
            "author_type": "operator",
            "delivery_status": "failed",
            "provider_message_id": None,
        },
    )
    purged = {}

    def purge(message_id, **kwargs):
        purged.update({"message_id": message_id, **kwargs})
        return {
            "deleted": True,
            "purged": True,
            "message_id": message_id,
            "conversation_id": 69,
            "conversation": {"id": 69, "state_version": 3},
            "delivery_status": "failed",
        }

    monkeypatch.setattr(workspace.store, "purge_undelivered_message", purge)
    response = client.delete(
        "/api/funnel-workspace/messages/98",
        headers={"Origin": ORIGIN, "X-CSRF-Token": payload["csrf_token"]},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["purged"] is True
    assert body["applied_by"] == "never_delivered"
    assert purged["actor_name"] == "Александр"
    assert calls == []


def test_deleting_a_delivered_message_still_goes_through_telegram(client, monkeypatch):
    """Доставленное удаляем у обеих сторон — здесь надгробие остаётся правдой."""
    payload = login(client)
    import funnel_telegram_gateway

    monkeypatch.setattr(
        workspace.store,
        "message_delivery_target",
        lambda _message_id: {
            "message_id": 55,
            "conversation_id": 41,
            "author_type": "operator",
            "delivery_status": "sent",
            "provider_message_id": "712",
        },
    )
    monkeypatch.setattr(
        funnel_telegram_gateway, "delete_delivered_message", lambda _payload: "bot"
    )
    monkeypatch.setattr(
        workspace.store,
        "delete_message_for_everyone",
        lambda message_id, **_kwargs: {
            "message": {"id": message_id, "text": "[Сообщение удалено]"}
        },
    )

    def refuse(*_args, **_kwargs):
        raise AssertionError("Доставленное сообщение нельзя вычищать из журнала.")

    monkeypatch.setattr(workspace.store, "purge_undelivered_message", refuse)
    response = client.delete(
        "/api/funnel-workspace/messages/55",
        headers={"Origin": ORIGIN, "X-CSRF-Token": payload["csrf_token"]},
    )

    assert response.status_code == 200
    assert response.get_json()["applied_by"] == "bot"


def _payload(**overrides):
    row = {
        "id": 41,
        "source_key": "telegram",
        "external_chat_id": "9001",
        "external_user_id": 9001,
        "status": "open",
        "control_mode": "ai",
        "resume_at": None,
        "state_version": 3,
        "reply_deadline_at": None,
        "awaiting_reply_since": None,
        "has_answer": True,
    }
    row.update(overrides)
    return workspace._conversation_payload(row)


def test_control_badge_says_who_runs_the_conversation():
    """Третий бейдж — про исполнителя: ИИ, человек или никто."""
    assert _payload(control_mode="ai")["control_label"] == "ИИ управляет"
    assert _payload(control_mode="human")["control_label"] == "Человек управляет"
    assert _payload(control_mode="paused")["control_label"] == "Ответы приостановлены"


def test_full_takeover_is_visible_as_a_separate_flag_not_a_fourth_badge():
    """Полный перехват остаётся «Человек управляет»: оператору важно, что отвечает
    человек, а бессрочность — отдельная пометка, а не ещё один статус."""
    lease = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    temporary = _payload(control_mode="human", resume_at=lease)
    permanent = _payload(control_mode="human", resume_at=None)

    assert temporary["control_label"] == permanent["control_label"] == "Человек управляет"
    assert temporary["control_permanent"] is False
    assert permanent["control_permanent"] is True


def test_queue_priority_matches_the_owner_order():
    """Очень срочно → новый клиент → клиент ждёт ответа → ждём ответа от клиента."""
    long_ago = datetime.now(timezone.utc) - timedelta(hours=5)
    just_now = datetime.now(timezone.utc)

    urgent = _payload(has_answer=True, awaiting_reply_since=long_ago)
    new_client = _payload(has_answer=False, awaiting_reply_since=just_now)
    client_waiting = _payload(has_answer=True, awaiting_reply_since=just_now)
    waiting_client = _payload(has_answer=True, awaiting_reply_since=None)

    assert urgent["urgent"] is True
    assert [
        urgent["priority"],
        new_client["priority"],
        client_waiting["priority"],
        waiting_client["priority"],
    ] == [1, 2, 3, 4]
    assert new_client["work_state_label"] == "Новый клиент"
    assert client_waiting["work_state_label"] == "Клиент ждёт ответа"
    assert waiting_client["work_state_label"] == "Ждём ответа от клиента"


def test_a_new_client_left_waiting_becomes_urgent_too():
    """Незнакомец, которому не ответили полчаса, — самая горячая строка списка, а не
    просто «новый»."""
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=45)
    row = _payload(has_answer=False, awaiting_reply_since=long_ago)

    assert row["work_state"] == workspace.store.WORK_STATE_NEW
    assert row["urgent"] is True
    assert row["priority"] == 1


def test_full_takeover_is_passed_through_the_control_endpoint(client, monkeypatch):
    """Кнопка «Веду сам» обязана доехать до хранилища именно как полный перехват."""
    session_payload = login(client)
    captured = {}

    def transition(conversation_id, **kwargs):
        captured.update({"conversation_id": conversation_id, **kwargs})
        return {
            "id": conversation_id,
            "control_mode": "human",
            "resume_at": None,
            "state_version": 4,
            "status": "open",
            "source_key": "telegram",
            "external_chat_id": "9001",
            "external_user_id": 9001,
        }

    monkeypatch.setattr(workspace.store, "transition_control", transition)
    response = client.post(
        "/api/funnel-workspace/conversations/41/control",
        json={"mode": "human", "permanent": True, "expected_version": 3},
        headers={"Origin": ORIGIN, "X-CSRF-Token": session_payload["csrf_token"]},
    )

    assert response.status_code == 200
    assert captured["permanent"] is True
    assert captured["mode"] == "human"
    body = response.get_json()["conversation"]
    assert body["control_permanent"] is True
    assert body["control_label"] == "Человек управляет"


def test_an_ordinary_takeover_is_not_permanent(client, monkeypatch):
    captured = {}

    def transition(conversation_id, **kwargs):
        captured.update(kwargs)
        return {
            "id": conversation_id,
            "control_mode": "human",
            "resume_at": datetime(2026, 7, 27, 12, 2, tzinfo=timezone.utc),
            "state_version": 4,
            "status": "open",
            "source_key": "telegram",
            "external_chat_id": "9001",
            "external_user_id": 9001,
        }

    session_payload = login(client)
    monkeypatch.setattr(workspace.store, "transition_control", transition)
    response = client.post(
        "/api/funnel-workspace/conversations/41/control",
        json={"mode": "human", "expected_version": 3},
        headers={"Origin": ORIGIN, "X-CSRF-Token": session_payload["csrf_token"]},
    )

    assert response.status_code == 200
    assert captured["permanent"] is False
    assert response.get_json()["conversation"]["control_permanent"] is False
