#!/usr/bin/env python3
"""Post a message AS THE BOT into the Bitrix24 notifications chat ("Albery Уведомления") from a
standalone process (the weekly cron digests, which have no live Bitrix event).

Mirrors app.py's token logic without importing that 23k-line module: it reads the shared bot state
file (access_token / refresh_token / expires / client_endpoint, written by app.py on every event),
reuses the cached token while valid, otherwise refreshes via oauth.bitrix.info using the stored
refresh_token and persists the rotated pair. Because each weekly run rotates the refresh_token, the
chain stays alive indefinitely — nothing expires.

Env (loaded from /var/www/albery/.env): B24_TESTBOT_CLIENT_ID, B24_TESTBOT_CLIENT_SECRET,
ALBERY_BITRIX_NOTIFY_CHAT (default chat728). State path: B24_TESTBOT_STATE (default
/var/www/albery/.b24_testbot_state.json).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_PATH = "/var/www/albery/.env"
STATE_PATH = os.getenv("B24_TESTBOT_STATE", "/var/www/albery/.b24_testbot_state.json")


def _load_state() -> dict:
    try:
        return json.loads(Path(STATE_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    try:
        Path(STATE_PATH).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def _access_token() -> tuple[str, str]:
    """Return (client_endpoint, access_token); refresh + persist if the cached token is stale.
    Returns ('', '') if not bootstrapped (the bot must receive one event first)."""
    load_dotenv(ENV_PATH)  # ensure client_id/secret are present even if called directly
    state = _load_state()
    tok = state.get("app_tokens") or {}
    endpoint = (tok.get("client_endpoint") or state.get("client_endpoint") or "").strip()
    access_token = (tok.get("access_token") or "").strip()
    try:
        expires = int(tok.get("expires") or 0)
    except (ValueError, TypeError):
        expires = 0
    if access_token and expires - 120 > int(time.time()):
        return endpoint, access_token
    refresh_token = (tok.get("refresh_token") or "").strip()
    client_id = os.getenv("B24_TESTBOT_CLIENT_ID", "").strip()
    client_secret = os.getenv("B24_TESTBOT_CLIENT_SECRET", "").strip()
    if not (refresh_token and client_id and client_secret):
        return endpoint, access_token
    resp = requests.get(
        "https://oauth.bitrix.info/oauth/token/",
        params={"grant_type": "refresh_token", "client_id": client_id,
                "client_secret": client_secret, "refresh_token": refresh_token},
        timeout=30,
    )
    data = resp.json() if resp.content else {}
    new_access = (data.get("access_token") or "").strip() if isinstance(data, dict) else ""
    if not new_access:
        # The sibling weekly digest may have just rotated the token; reuse a now-fresh cached one.
        fresh = _load_state().get("app_tokens") or {}
        try:
            fresh_exp = int(fresh.get("expires") or 0)
        except (ValueError, TypeError):
            fresh_exp = 0
        if fresh.get("access_token") and fresh_exp - 120 > int(time.time()):
            return (fresh.get("client_endpoint") or endpoint).strip(), fresh["access_token"].strip()
        raise RuntimeError(f"b24 token refresh failed: {str(data)[:300]}")
    state = _load_state()  # re-read to merge a possibly newer event-stored pair
    tok = dict(state.get("app_tokens") or {})
    tok["access_token"] = new_access
    tok["refresh_token"] = (data.get("refresh_token") or refresh_token).strip()
    try:
        tok["expires"] = int(data.get("expires") or (int(time.time()) + int(data.get("expires_in") or 3600)))
    except (ValueError, TypeError):
        tok["expires"] = int(time.time()) + 3600
    new_endpoint = (data.get("client_endpoint") or data.get("server_endpoint") or endpoint).strip()
    if new_endpoint:
        tok["client_endpoint"] = new_endpoint
    state["app_tokens"] = tok
    _save_state(state)
    return tok.get("client_endpoint") or endpoint, new_access


def notify(text: str, dialog_id: str | None = None) -> tuple[bool, str | None]:
    """Post `text` as the bot into the notifications chat. Best-effort; returns (ok, error)."""
    load_dotenv(ENV_PATH)
    dialog_id = (dialog_id or os.getenv("ALBERY_BITRIX_NOTIFY_CHAT", "chat728")).strip()
    state = _load_state()
    bot_id = state.get("bot_id")
    try:
        endpoint, access_token = _access_token()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:300]
    if not (endpoint and access_token and bot_id and dialog_id):
        return False, "bitrix bot token/chat not bootstrapped yet"
    base = endpoint.rstrip("/")
    ok_all, err = True, None
    for i in range(0, len(text), 3900):
        try:
            resp = requests.post(
                f"{base}/imbot.message.add.json?auth={access_token}",
                json={"BOT_ID": bot_id, "DIALOG_ID": dialog_id, "MESSAGE": text[i:i + 3900]},
                timeout=30,
            )
            data = resp.json() if resp.content else {}
            if isinstance(data, dict) and data.get("error"):
                ok_all, err = False, str(data.get("error_description") or data.get("error"))[:300]
        except Exception as exc:  # noqa: BLE001
            ok_all, err = False, str(exc)[:300]
    return ok_all, err


if __name__ == "__main__":
    import sys
    msg = sys.argv[1] if len(sys.argv) > 1 else "Тест уведомления Albery (от бота)."
    print(notify(msg))
