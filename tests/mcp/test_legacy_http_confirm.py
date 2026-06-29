"""Legacy HTTP external actions must keep a server-side confirmation gate."""
from __future__ import annotations

import pytest


@pytest.fixture()
def authenticated_legacy_client(client, monkeypatch):
    monkeypatch.setenv("ALLOW_LEGACY_HTTP_API", "1")
    with client.session_transaction() as session:
        session["admin_authenticated"] = True
    return client


def _post_json(client, path: str, payload: dict):
    return client.post(
        path,
        json=payload,
        headers={"Origin": "http://localhost"},
        base_url="http://localhost",
    )


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/zoom-calls/call-1/dispatch-operational-tasks", {"preview": {"items": []}}),
        ("/api/owner/daily-reports/report-1/send", {"recipient_ids": [1]}),
        ("/api/owner/daily-reports/report-1/send-full", {"recipient_ids": [1]}),
        ("/api/owner/weekly-reports/report-1/send", {"recipient_ids": [1]}),
        ("/api/owner/weekly-reports/report-1/send-full", {"recipient_ids": [1]}),
    ],
)
def test_legacy_external_actions_require_server_side_confirm(authenticated_legacy_client, path, payload):
    resp = _post_json(authenticated_legacy_client, path, payload)

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"]["code"] == "confirm_required"


def test_legacy_external_action_confirm_helper_accepts_only_literal_true(app_module):
    assert app_module.legacy_external_action_confirmed({"confirm": True}) is True
    assert app_module.legacy_external_action_confirmed({"confirm": "true"}) is False
    assert app_module.legacy_external_action_confirmed({}) is False
