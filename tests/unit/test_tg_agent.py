"""The standalone Telegram agent (tg_agent.py + tg_digest.py): pure logic only —
channel normalization, the owner gate and the t.me/s/ preview parser."""
from __future__ import annotations

from datetime import datetime, timezone

import tg_agent
import tg_digest


def test_normalize_channel_variants():
    for raw in ("@wbhub", "wbhub", "https://t.me/wbhub", "t.me/s/wbhub", "T.ME/wbhub/",
                "https://t.me/wbhub?start=1"):
        assert tg_agent.normalize_channel(raw) == "wbhub", raw


def test_normalize_channel_rejects_junk():
    for raw in ("", "@", "https://t.me/+AbCdEf", "t.me/joinchat/xyz", "имя-канала", "a b"):
        assert tg_agent.normalize_channel(raw) is None, raw


def test_owner_gate(monkeypatch):
    monkeypatch.setenv("TG_AGENT_OWNER_IDS", "111, 222")
    assert tg_agent.is_owner(111) and tg_agent.is_owner("222")
    assert not tg_agent.is_owner(333) and not tg_agent.is_owner(None)


_FIXTURE = """
<div class="tgme_widget_message_wrap js-widget_message_wrap">
 <div class="tgme_widget_message_text js-message_text" dir="auto">
   Новые <b>комиссии</b> WB<br/>с 15 июля &amp; далее</div>
 <a class="tgme_widget_message_date"><time datetime="2026-07-08T10:00:00+03:00"></time></a>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
 <div class="tgme_widget_message_text js-message_text" dir="auto">Старый пост</div>
 <a class="tgme_widget_message_date"><time datetime="2026-06-01T09:00:00+03:00"></time></a>
</div>
"""


def test_parse_channel_preview_extracts_time_and_text():
    posts = tg_digest.parse_channel_preview(_FIXTURE)
    assert len(posts) == 2
    at, text = posts[0]
    assert at == datetime(2026, 7, 8, 7, 0, tzinfo=timezone.utc)  # +03:00 -> UTC
    assert text == "Новые комиссии WB\nс 15 июля & далее"  # tags stripped, entities unescaped


def test_hermes_error_sentinel():
    assert tg_agent._HERMES_ERROR_RE.match("API call failed after 3 retries: Broken pipe")
    assert tg_agent._HERMES_ERROR_RE.match("Ошибка LLM: HTTP 500")
    assert not tg_agent._HERMES_ERROR_RE.match("Обычный нормальный ответ")
