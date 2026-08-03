"""Уведомления владельцу переехали из личного Telegram в Bitrix-группу «Уведомления» (chat728).

Эти тесты фиксируют маршрут: дайджесты доставляются через b24_chat_notify.notify (Bitrix),
а НЕ через личный Telegram владельца.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import b24_chat_notify  # noqa: E402


def test_channel_digest_goes_to_bitrix_group_not_owner_telegram(monkeypatch):
    import tg_agent
    import tg_digest
    import tg_userbot

    monkeypatch.setattr(tg_agent, "_load_env_file", lambda: None)
    monkeypatch.setattr(tg_agent, "channels", lambda: [])          # нет каналов -> ветка "no channels"
    monkeypatch.setattr(tg_userbot, "session_ready", lambda: False)

    tg_sends: list = []
    monkeypatch.setattr(tg_agent, "send_text", lambda *a, **k: tg_sends.append((a, k)))

    bitrix_calls: list = []

    def _fake_notify(text, dialog_id=None):
        bitrix_calls.append((text, dialog_id))
        return True, None

    monkeypatch.setattr(b24_chat_notify, "notify", _fake_notify)

    result = tg_digest.run_digest()

    assert result == "no channels"
    assert len(bitrix_calls) == 1                # ушло в Bitrix
    assert bitrix_calls[0][1] is None            # dialog_id=None -> b24_chat_notify подставит chat728
    assert tg_sends == []                        # в личный Telegram владельца НЕ ушло


def test_error_digest_delivers_to_bitrix_only(monkeypatch):
    import error_report_digest as erd

    # TG-путь удалён целиком.
    assert not hasattr(erd, "tg_send")
    assert not hasattr(erd, "tg_token")

    calls: list = []
    monkeypatch.setattr(b24_chat_notify, "notify",
                        lambda text, dialog_id=None: (calls.append(text) or (True, None)))

    ok, err = erd.bitrix_send("тест дайджеста")

    assert ok is True and err is None
    assert calls == ["тест дайджеста"]           # маршрут -> Bitrix-группа
