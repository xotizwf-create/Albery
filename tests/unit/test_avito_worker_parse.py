"""Разбор переписок Авито: из встроенного состояния страницы, а не из вёрстки.

Форма снята с живой сессии 19.08.2026 (`avito_worker.py capture`): мессенджер отдаёт цельный
JSON в script-теге, внутри — state.ssrState.channels с участниками, объявлением и последним
сообщением. Здесь эта форма закреплена на синтетическом примере: настоящие переписки
владельца в репозиторий не попадают.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

WORKER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "avito_worker.py"


@pytest.fixture(scope="module")
def worker():
    spec = importlib.util.spec_from_file_location("avito_worker", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _page(channels: dict) -> str:
    state = {"i18n": {"locale": "ru-RU"},
             "state": {"clientProps": {}, "ssrState": {"channels": channels}}}
    return ("<html><head><script>window.x=1</script></head><body>"
            f"<script>{json.dumps(state, ensure_ascii=False)}</script></body></html>")


ME = {"id": "hash-me", "name": "Александр", "originalId": "198797068"}
THEM = {"id": "hash-them", "name": "Этажи Казань"}


def _channel(**over):
    channel = {
        "channelId": "u2i-abc",
        "type": "u2i",
        "users": [ME, THEM],
        "context": {"type": "item", "value": {"id": "7707314537",
                                              "title": "2-к. квартира, 40 м²",
                                              "priceString": "45 000 ₽"}},
        "lastMessage": {"id": "m-1", "fromUid": "hash-them", "created": 17856721011584856,
                        "preview": "Квартира ещё свободна?"},
        "updated": 17856721011584856,
    }
    channel.update(over)
    return channel


def test_talk_is_read_from_the_embedded_state(worker):
    parsed = worker.parse_channels(_page({"ids": ["u2i-abc"],
                                          "entities": {"u2i-abc": _channel()}}))

    assert len(parsed) == 1
    talk = parsed[0]
    assert talk["external_chat_id"] == "u2i-abc"
    assert talk["display_name"] == "Этажи Казань"
    assert talk["listing"]["title"].startswith("2-к. квартира")
    assert talk["listing"]["price"] == "45 000 ₽"
    assert talk["listing"]["url"] == "https://www.avito.ru/7707314537"


def test_incoming_message_is_marked_as_the_clients(worker):
    parsed = worker.parse_channels(_page({"ids": ["u2i-abc"],
                                          "entities": {"u2i-abc": _channel()}}))

    message = parsed[0]["messages"][0]
    assert message["author_type"] == "client"
    assert message["text"] == "Квартира ещё свободна?"
    assert message["external_message_id"] == "m-1"
    # Время Авито — в сотнях наносекунд; без пересчёта переписка уехала бы в 58-й век.
    assert message["occurred_at"].startswith("2026-")


def test_our_own_reply_is_not_mistaken_for_the_clients(worker):
    channel = _channel(lastMessage={"id": "m-2", "fromUid": "hash-me",
                                    "created": 17856721011584856, "preview": "Да, свободна"})

    parsed = worker.parse_channels(_page({"ids": ["u2i-abc"], "entities": {"u2i-abc": channel}}))

    assert parsed[0]["messages"][0]["author_type"] == "operator"


def test_talk_without_a_listing_still_arrives(worker):
    channel = _channel(context={"type": "system", "value": {"name": "Служебный чат"}})

    parsed = worker.parse_channels(_page({"ids": ["u2i-abc"], "entities": {"u2i-abc": channel}}))

    assert parsed[0]["listing"]["id"] == ""
    assert parsed[0]["listing"]["url"] == ""
    assert parsed[0]["listing"]["title"] == "Служебный чат"


def test_empty_preview_does_not_create_a_blank_message(worker):
    channel = _channel(lastMessage={"id": "m-3", "fromUid": "hash-them", "created": 0,
                                    "preview": "   "})

    parsed = worker.parse_channels(_page({"ids": ["u2i-abc"], "entities": {"u2i-abc": channel}}))

    assert parsed[0]["messages"] == []


def test_update_key_changes_with_the_last_message_so_repeats_are_free(worker):
    first = worker.parse_channels(_page({"ids": ["u2i-abc"],
                                         "entities": {"u2i-abc": _channel()}}))[0]
    same = worker.parse_channels(_page({"ids": ["u2i-abc"],
                                        "entities": {"u2i-abc": _channel()}}))[0]
    newer = worker.parse_channels(_page({"ids": ["u2i-abc"], "entities": {"u2i-abc": _channel(
        lastMessage={"id": "m-9", "fromUid": "hash-them", "created": 17856721011584857,
                     "preview": "Ещё вопрос"})}}))[0]

    assert first["update_id"] == same["update_id"]
    assert newer["update_id"] != first["update_id"]


def test_a_page_without_the_messenger_gives_nothing_instead_of_crashing(worker):
    assert worker.parse_channels("<html><body>Доступ ограничен</body></html>") == []
    assert worker.extract_state("<html></html>") == {}
