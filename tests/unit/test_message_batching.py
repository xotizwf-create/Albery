"""Файлы, присланные подряд, обрабатываются одним ходом.

Битрикс не умеет отправлять несколько файлов одним сообщением — каждая картинка приходит
отдельным событием. 20.07.2026, диалог 30: Наталья прислала запрос «составь доп соглашение
исходя из сокращения выплаты WB» (13:11:33), скриншот (13:11:35) и документ (13:11:41).
Агент завёл ДВА независимых хода: один переспросил данные, которые были на скриншоте, второй
разобрал документ. Задача осталась невыполненной.
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def bot(app_module, monkeypatch):
    import b24bot

    monkeypatch.setattr(b24bot, "_B24_BATCH_TEXT_WINDOW_S", 0.15)
    monkeypatch.setattr(b24bot, "_B24_BATCH_FILE_WINDOW_S", 0.25)
    monkeypatch.setattr(b24bot, "_B24_BATCH_MAX_WAIT_S", 5.0)
    b24bot._B24_BATCHES.clear()
    return b24bot


def _piece(text="", images=(), docs=(), atts=(), voices=(), mid="1"):
    return {"texts": [text] if text else [], "image_texts": list(images),
            "doc_blocks": list(docs), "attachments": list(atts),
            "voice_texts": list(voices), "reply_text": "", "message_ids": [mid]}


def _collect(bot, pieces, gap=0.05):
    done = threading.Event()
    runs = []

    def run(parts):
        runs.append(parts)
        done.set()

    for p in pieces:
        bot._b24_batch_message(("24", "30", "42"), p, run)
        time.sleep(gap)
    assert done.wait(4), "ход так и не запустился"
    return runs


def test_natalia_case_three_messages_become_one_turn(bot):
    """Точный сценарий инцидента: текст, скриншот, документ подряд."""
    runs = _collect(bot, [
        _piece(text="составь мне новое доп соглашение исходя из сокращения выплаты WB", mid="m1"),
        _piece(images=["текст со скриншота: сокращение 214 350 руб."], mid="m2"),
        _piece(docs=["ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 1..."], mid="m3"),
    ])

    assert len(runs) == 1, "должен быть ОДИН ход, а не три"
    parts = runs[0]
    assert "доп соглашение" in " ".join(parts["texts"])
    assert parts["image_texts"] and parts["doc_blocks"], "скрин и документ в том же ходе"
    assert parts["message_ids"] == ["m1", "m2", "m3"]


def test_several_images_are_merged(bot):
    """Владелец: «нужно продумать механизм, как можно загружать несколько картинок разом»."""
    runs = _collect(bot, [
        _piece(text="разбери эти скрины", mid="m1"),
        _piece(images=["скрин 1"], mid="m2"),
        _piece(images=["скрин 2"], mid="m3"),
        _piece(images=["скрин 3"], mid="m4"),
    ])

    assert len(runs) == 1
    assert runs[0]["image_texts"] == ["скрин 1", "скрин 2", "скрин 3"]


def test_single_message_still_works(bot):
    runs = _collect(bot, [_piece(text="привет", mid="m1")])

    assert len(runs) == 1
    assert runs[0]["texts"] == ["привет"]
    assert runs[0]["message_ids"] == ["m1"]


def test_reply_to_the_last_message_of_the_batch(bot):
    """Реакция и ответ вешаются на последнее сообщение пачки, иначе они уедут вверх."""
    runs = _collect(bot, [_piece(text="раз", mid="m1"), _piece(images=["скрин"], mid="m2")])

    assert runs[0]["message_ids"][-1] == "m2"


def test_separate_dialogs_do_not_mix(bot):
    """Сообщения разных людей нельзя склеивать между собой."""
    done = threading.Event()
    runs = []

    def run(parts):
        runs.append(parts)
        if len(runs) == 2:
            done.set()

    bot._b24_batch_message(("24", "30", "42"), _piece(text="от Натальи", mid="a"), run)
    bot._b24_batch_message(("24", "16", "16"), _piece(text="от Александра", mid="b"), run)
    assert done.wait(4)

    assert len(runs) == 2
    texts = sorted(" ".join(r["texts"]) for r in runs)
    assert texts == ["от Александра", "от Натальи"]


def test_long_stream_is_capped_by_max_wait(bot, monkeypatch):
    """Непрерывный поток файлов не должен откладывать ответ бесконечно."""
    monkeypatch.setattr(bot, "_B24_BATCH_MAX_WAIT_S", 0.6)
    done = threading.Event()
    runs = []
    monkeypatch.setattr(bot, "_B24_BATCH_FILE_WINDOW_S", 0.3)

    def run(parts):
        runs.append(parts)
        done.set()

    started = time.monotonic()
    for i in range(12):
        bot._b24_batch_message(("24", "30", "42"), _piece(images=[f"скрин {i}"], mid=str(i)), run)
        time.sleep(0.08)
    assert done.wait(4)

    assert time.monotonic() - started < 3, "ответ не должен откладываться бесконечно"
    assert runs[0]["image_texts"], "накопленное не теряется"


def test_failure_inside_the_turn_does_not_break_the_buffer(bot):
    """Падение обработки не должно оставлять диалог с висящей пачкой."""
    def boom(parts):
        raise RuntimeError("сломалось")

    bot._b24_batch_message(("24", "30", "42"), _piece(text="привет", mid="m1"), boom)
    time.sleep(0.5)

    assert ("24", "30", "42") not in bot._B24_BATCHES, "буфер обязан очиститься"
