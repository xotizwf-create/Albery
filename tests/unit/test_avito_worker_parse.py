"""Разбор переписок Авито: из ответа мессенджера, а не из вёрстки.

Форма снята с живой сессии 19.08.2026: список чатов приходит методом `avito.getChats.v5` —
это собственный протокол мессенджера (JSON-RPC), тот же, которым пользуется сам сайт. Раньше
разбиралось встроенное в страницу состояние `ssrState`, но оно жило там лишь пока не грузились
скрипты Авито; как только страница стала загружаться целиком, состояние из неё пропало и
зеркало молча приносило НОЛЬ переписок. Здесь форма закреплена на синтетическом примере:
настоящие переписки владельца в репозиторий не попадают.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

WORKER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "avito_worker.py"

ME_ID = "198797068"


@pytest.fixture(scope="module")
def worker():
    spec = importlib.util.spec_from_file_location("avito_worker", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Числовой id участника Авито кладёт В ПРОФИЛЬ, а не в саму запись участника.
ME = {"id": "hash-me", "name": "Александр", "publicUserProfile": {"originalId": ME_ID}}
THEM = {"id": "hash-them", "name": "Этажи Казань",
        "publicUserProfile": {"itemId": 7707314537}}


def _channel(**over):
    channel = {
        "channelId": "u2i-abc",
        "type": "u2i",
        "users": [ME, THEM],
        "context": {"type": "item", "value": {"id": "7707314537",
                                              "title": "2-к. квартира, 40 м²",
                                              "priceString": "45 000 ₽"}},
        "lastMessage": {"id": "m-1", "fromUid": "hash-them", "created": 17856721011584856,
                        "preview": {"text": "Квартира ещё свободна?"}},
        "updated": 17856721011584856,
    }
    channel.update(over)
    return channel


def _chats(*channels) -> dict:
    return {"hasMore": False, "channels": list(channels)}


def test_talk_is_read_from_the_messenger_answer(worker):
    parsed = worker.parse_channels(_chats(_channel()), own_id=ME_ID)

    assert len(parsed) == 1
    talk = parsed[0]
    assert talk["external_chat_id"] == "u2i-abc"
    assert talk["display_name"] == "Этажи Казань"
    assert talk["listing"]["id"] == "7707314537"
    assert talk["listing"]["title"].startswith("2-к. квартира")
    assert talk["listing"]["price"] == "45 000 ₽"
    assert talk["listing"]["url"] == "https://www.avito.ru/7707314537"


def test_incoming_message_is_marked_as_the_clients(worker):
    parsed = worker.parse_channels(_chats(_channel()), own_id=ME_ID)

    message = parsed[0]["messages"][0]
    assert message["author_type"] == "client"
    assert message["text"] == "Квартира ещё свободна?"
    assert message["external_message_id"] == "m-1"
    # Время Авито — в сотнях наносекунд; без пересчёта переписка уехала бы в 58-й век.
    assert message["occurred_at"].startswith("2026-")


def test_our_own_reply_is_not_mistaken_for_the_clients(worker):
    channel = _channel(lastMessage={"id": "m-2", "fromUid": "hash-me",
                                    "created": 17856721011584856, "preview": "Да, свободна"})

    parsed = worker.parse_channels(_chats(channel), own_id=ME_ID)

    assert parsed[0]["messages"][0]["author_type"] == "operator"


def test_the_other_side_is_not_taken_for_us_when_both_have_a_numeric_id(worker):
    """В ответе сокета числовой id есть у ОБОИХ участников — свой узнаём по id аккаунта."""
    them = dict(THEM, publicUserProfile={"originalId": "555000111"})
    channel = _channel(users=[them, ME],
                       lastMessage={"id": "m-5", "fromUid": "hash-them",
                                    "created": 17856721011584856, "preview": "Ещё актуально?"})

    parsed = worker.parse_channels(_chats(channel), own_id=ME_ID)

    assert parsed[0]["display_name"] == "Этажи Казань"
    assert parsed[0]["messages"][0]["author_type"] == "client"


def test_talk_without_a_listing_still_arrives(worker):
    channel = _channel(context={"type": "system", "value": {"name": "Служебный чат"}})

    parsed = worker.parse_channels(_chats(channel), own_id=ME_ID)

    assert parsed[0]["listing"]["id"] == ""
    assert parsed[0]["listing"]["url"] == ""
    assert parsed[0]["listing"]["title"] == "Служебный чат"


def test_empty_preview_does_not_create_a_blank_message(worker):
    channel = _channel(lastMessage={"id": "m-3", "fromUid": "hash-them", "created": 0,
                                    "preview": "   "})

    parsed = worker.parse_channels(_chats(channel), own_id=ME_ID)

    assert parsed[0]["messages"] == []


def test_update_key_changes_with_the_last_message_so_repeats_are_free(worker):
    first = worker.parse_channels(_chats(_channel()), own_id=ME_ID)[0]
    same = worker.parse_channels(_chats(_channel()), own_id=ME_ID)[0]
    newer = worker.parse_channels(_chats(_channel(
        lastMessage={"id": "m-9", "fromUid": "hash-them", "created": 17856721011584857,
                     "preview": "Ещё вопрос"})), own_id=ME_ID)[0]

    assert first["update_id"] == same["update_id"]
    assert newer["update_id"] != first["update_id"]


def test_preview_object_is_unwrapped_into_plain_text(worker):
    """Авито отдаёт превью объектом. Без разбора оператор видел «{'text': '…'}»."""
    parsed = worker.parse_channels(_chats(_channel()), own_id=ME_ID)

    assert parsed[0]["messages"][0]["text"] == "Квартира ещё свободна?"
    assert "{" not in parsed[0]["messages"][0]["text"]


def test_attachment_without_text_is_named_not_dropped(worker):
    channel = _channel(lastMessage={"id": "m-4", "fromUid": "hash-them", "type": "image",
                                    "created": 17856721011584856, "preview": {}})

    parsed = worker.parse_channels(_chats(channel), own_id=ME_ID)

    assert parsed[0]["messages"][0]["text"] == "[изображение]"


def test_an_empty_answer_gives_nothing_instead_of_crashing(worker):
    assert worker.parse_channels({}) == []
    assert worker.parse_channels(None) == []
    assert worker.parse_channels({"channels": [{"users": []}]}) == []
