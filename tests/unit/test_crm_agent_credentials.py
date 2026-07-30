from __future__ import annotations

import pytest

import b24bot
from mcp import context_server as ctx


def test_ordinary_bitrix_event_cannot_replace_persistent_oauth(monkeypatch):
    state = {
        "app_tokens": {
            "access_token": "installer-access",
            "refresh_token": "installer-refresh",
            "expires": 123,
            "client_endpoint": "https://portal/rest/",
        }
    }
    saves: list[dict] = []
    monkeypatch.setattr(b24bot, "_b24_save_state", lambda value: saves.append(value.copy()))

    b24bot._b24_capture_tokens(
        {
            "auth[access_token]": "employee-access",
            "auth[refresh_token]": "employee-refresh",
            "auth[client_endpoint]": "https://portal/rest/",
        },
        state,
        event_name="ONIMBOTMESSAGEADD",
    )

    assert state["app_tokens"]["access_token"] == "installer-access"
    assert saves == []


def test_install_event_establishes_persistent_oauth(monkeypatch):
    state: dict = {}
    saves: list[dict] = []
    monkeypatch.setattr(b24bot, "_b24_save_state", lambda value: saves.append(value.copy()))

    b24bot._b24_capture_tokens(
        {
            "auth[access_token]": "installer-access",
            "auth[refresh_token]": "installer-refresh",
            "auth[expires]": "9999999999",
            "auth[client_endpoint]": "https://portal/rest/",
        },
        state,
        event_name="ONAPPINSTALL",
    )

    assert state["app_tokens"]["access_token"] == "installer-access"
    assert state["app_tokens"]["refresh_token"] == "installer-refresh"
    assert len(saves) == 1


def test_app_mutation_checks_and_reuses_one_token(monkeypatch):
    monkeypatch.setattr(
        b24bot, "_b24_app_access_token", lambda: ("https://portal/rest/", "agent-token")
    )
    calls: list[tuple[str, str]] = []

    def fake_call(endpoint, token, method, payload, **_kwargs):
        calls.append((token, method))
        if method == "user.current":
            return {"result": {"ID": "22"}}
        return {"result": True}

    monkeypatch.setattr(b24bot, "_b24_app_call", fake_call)

    result = b24bot.b24_app_method_call_as_user(
        22, "crm.deal.update", {"id": 10, "fields": {"STAGE_ID": "C16:NEW"}}
    )

    assert result == {"result": True}
    assert calls == [
        ("agent-token", "user.current"),
        ("agent-token", "crm.deal.update"),
    ]


def test_app_mutation_refuses_employee_token(monkeypatch):
    monkeypatch.setattr(
        b24bot, "_b24_app_access_token", lambda: ("https://portal/rest/", "employee-token")
    )
    called_methods: list[str] = []

    def fake_call(_endpoint, _token, method, _payload, **_kwargs):
        called_methods.append(method)
        return {"result": {"ID": "36"}}

    monkeypatch.setattr(b24bot, "_b24_app_call", fake_call)

    with pytest.raises(RuntimeError, match="expected AI agent user 22"):
        b24bot.b24_app_method_call_as_user(22, "crm.deal.update", {"id": 10})

    assert called_methods == ["user.current"]


def test_crm_mutation_uses_verified_agent_workflow(monkeypatch):
    monkeypatch.setenv("CRM_AGENT_USER_ID", "22")
    monkeypatch.setattr(ctx, "_crm_webhook_usable", lambda: False)
    calls: list[tuple] = []

    def guarded_call(expected_user_id, method, payload):
        calls.append((expected_user_id, method, payload))
        return {"result": True}

    monkeypatch.setattr(
        ctx,
        "app_workflow_function",
        lambda name: guarded_call
        if name == "b24_app_method_call_as_user"
        else pytest.fail(f"unexpected workflow {name}"),
    )

    result = ctx._crm_call("crm.deal.update", {"id": 10, "fields": {"STAGE_ID": "C16:NEW"}})

    assert result == {"result": True}
    assert calls == [
        (22, "crm.deal.update", {"id": 10, "fields": {"STAGE_ID": "C16:NEW"}})
    ]


def test_crm_mutation_prefers_verified_agent_webhook(monkeypatch):
    monkeypatch.setenv("CRM_AGENT_USER_ID", "22")
    monkeypatch.setattr(ctx, "_crm_webhook_usable", lambda: True)
    calls: list[tuple[str, dict]] = []

    def webhook_call(method, payload):
        calls.append((method, payload))
        if method == "user.current":
            return {"result": {"ID": "22"}}
        return {"result": True}

    monkeypatch.setattr(ctx, "_webhook_raw", webhook_call)
    monkeypatch.setattr(
        ctx,
        "app_workflow_function",
        lambda name: pytest.fail(f"unexpected workflow {name}"),
    )

    result = ctx._crm_call("crm.deal.update", {"id": 10, "fields": {"STAGE_ID": "C16:NEW"}})

    assert result == {"result": True}
    assert calls == [
        ("user.current", {}),
        ("crm.deal.update", {"id": 10, "fields": {"STAGE_ID": "C16:NEW"}}),
    ]


def test_verified_webhook_validation_error_is_not_masked_by_oauth(monkeypatch):
    monkeypatch.setenv("CRM_AGENT_USER_ID", "22")
    monkeypatch.setattr(ctx, "_crm_webhook_usable", lambda: True)

    def webhook_call(method, _payload):
        if method == "user.current":
            return {"result": {"ID": "22"}}
        raise ctx.McpError(-32010, "crm.deal.update: invalid stage")

    monkeypatch.setattr(ctx, "_webhook_raw", webhook_call)
    monkeypatch.setattr(
        ctx,
        "app_workflow_function",
        lambda name: pytest.fail(f"unexpected workflow {name}"),
    )

    with pytest.raises(ctx.McpError, match="invalid stage"):
        ctx._crm_call("crm.deal.update", {"id": 10, "fields": {"STAGE_ID": "bad"}})


def test_crm_mutation_never_falls_back_to_unverified_oauth(monkeypatch):
    monkeypatch.setenv("CRM_AGENT_USER_ID", "22")
    monkeypatch.setattr(ctx, "_crm_webhook_usable", lambda: False)
    unguarded_called = False

    def workflow(name):
        nonlocal unguarded_called
        if name == "b24_app_method_call":
            unguarded_called = True
            return lambda *_args: {"result": True}
        if name == "b24_app_method_call_as_user":
            return lambda *_args: (_ for _ in ()).throw(
                RuntimeError("OAuth belongs to user 36")
            )
        pytest.fail(f"unexpected workflow {name}")

    monkeypatch.setattr(ctx, "app_workflow_function", workflow)

    with pytest.raises(ctx.McpError, match="mutation refused"):
        ctx._crm_call("crm.item.update", {"entityTypeId": 2, "id": 10, "fields": {}})

    assert unguarded_called is False


def test_crm_read_keeps_existing_oauth_fallback(monkeypatch):
    monkeypatch.setattr(ctx, "_crm_webhook_usable", lambda: False)
    calls: list[tuple[str, dict]] = []

    def ordinary_call(method, payload):
        calls.append((method, payload))
        return {"result": {"ID": "10"}}

    monkeypatch.setattr(
        ctx,
        "app_workflow_function",
        lambda name: ordinary_call
        if name == "b24_app_method_call"
        else pytest.fail(f"unexpected workflow {name}"),
    )

    result = ctx._crm_call("crm.deal.get", {"id": 10})

    assert result["result"]["ID"] == "10"
    assert calls == [("crm.deal.get", {"id": 10})]
