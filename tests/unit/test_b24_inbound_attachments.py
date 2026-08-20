"""Вложения в чат боту: право на файл живёт ровно один запрос.

Разобрано 20.08.2026 по жалобе владельца («у Артура файл не открылся и задача не сделалась»).

Файл, присланный в личный чат «сотрудник ↔ бот», лежит на Диске отправителя, и доступ к нему
даёт участие в ЭТОМ чате. Постоянный токен приложения ходит от лица технического пользователя 22,
который в чате не участник, — портал отвечает ACCESS_DENIED. Доступ есть только у токена из самого
события: он принадлежит сотруднику, который написал.

14.08.2026 разбор вложений уехал в отложенный воркер (durable inbound), а payload кладётся в
очередь без токенов — и воркеру достаётся ровно тот токен, которому портал отказывает. С этого
дня НИ ОДНО вложение не прочиталось: скриншот Софьи 18.08, договоры Юлии 19.08 ×2, таблица
Артура 20.08 — четыре из четырёх.

Здесь закреплено: байты забираются на приёме события (пока токен отправителя в руках), а
распознавание остаётся в воркере и читает уже сохранённое.
"""
from __future__ import annotations

import pytest

import attachments
import b24bot


ARTHUR_DIALOG = "28"
ARTHUR_FILE_ID = "6100"
ARTHUR_FILE = "Анализ_поставщиков_без_футболок_НДС_ТОП10_WB_2026.xlsx"
ARTHUR_BYTES = b"PK\x03\x04-real-xlsx-bytes-of-arthurs-supplier-analysis"
MAIN_BOT_ID = 40
DENIED = 'disk.file.get: HTTP 403 {"error":"ACCESS_DENIED","error_description":"Access denied!"}'

SENDER_TOKEN = "token-of-arthur-from-the-event"
APP_TOKEN = "permanent-token-of-user-22"
ENDPOINT = "https://b24-0xrp3s.bitrix24.ru/rest/"


def _event_payload() -> dict:
    """Событие ONIMBOTMESSAGEADD ровно в той плоской раскладке, в какой его шлёт Битрикс."""
    return {
        "event": "ONIMBOTMESSAGEADD",
        "data[PARAMS][MESSAGE_ID]": "1552",
        "data[PARAMS][DIALOG_ID]": ARTHUR_DIALOG,
        "data[PARAMS][FROM_USER_ID]": ARTHUR_DIALOG,
        "data[PARAMS][MESSAGE]": "Создай гугл таблицу из вложения, в первом листе Топ15",
        f"data[PARAMS][FILES][{ARTHUR_FILE_ID}][name]": ARTHUR_FILE,
        f"data[PARAMS][FILES][{ARTHUR_FILE_ID}][type]": "file",
    }


@pytest.fixture()
def portal(monkeypatch):
    """Портал: файл отдаётся только токену отправителя, приложению — 403, как на бою."""
    seen = {"download_tokens": [], "recover_dialogs": []}

    def fake_download_url(endpoint, access_token, fid):
        seen["download_tokens"].append(access_token)
        if access_token != SENDER_TOKEN:
            b24bot.logging.warning("b24 extras: bot disk.file.get failed fid=%s: %s", fid, DENIED)
            return ""
        return f"https://b24-0xrp3s.bitrix24.ru/rest/download/{fid}/"

    def fake_fetch_bytes(url, access_token, **kw):
        return ARTHUR_BYTES if url else b""

    def fake_refresh(webhook_base, fid):
        return ""  # статический вебхук ходит тем же пользователем 22 — тоже не участник

    def fake_file_ids(endpoint, access_token, dialog_id, message_id, limit=15):
        seen["recover_dialogs"].append(str(dialog_id))
        return []

    monkeypatch.setattr(b24bot, "_b24_app_download_url", fake_download_url)
    monkeypatch.setattr(b24bot, "_b24_fetch_bytes", fake_fetch_bytes)
    monkeypatch.setattr(b24bot, "_refresh_bitrix_download_url", fake_refresh)
    monkeypatch.setattr(b24bot, "_b24_message_file_ids", fake_file_ids)
    return seen


@pytest.fixture()
def store(monkeypatch):
    """Хранилище вложений в памяти: строка на файл, поиск по disk file id."""
    rows: dict[str, dict] = {}
    blobs: dict[str, bytes] = {}

    def fake_store(*, data, file_name, kind, extracted_text, agent_slug, dialog_id,
                   bitrix_user_id=None, mime=None, source_disk_file_id=None):
        token = f"att_{len(rows) + 1}"
        rows[token] = {"token": token, "file_name": file_name, "kind": kind,
                       "extracted_text": extracted_text, "char_len": len(extracted_text or ""),
                       "source_disk_file_id": str(source_disk_file_id or ""),
                       "dialog_id": dialog_id}
        blobs[token] = data
        return token

    def fake_find(disk_file_id):
        fid = str(disk_file_id)
        found = [r for r in rows.values() if r["source_disk_file_id"] == fid]
        return found[-1] if found else None

    def fake_bytes(token):
        return (blobs[token], rows[token]["file_name"]) if token in blobs else None

    def fake_finalize(token, *, kind, extracted_text):
        rows[token].update(kind=kind, extracted_text=extracted_text,
                           char_len=len(extracted_text or ""))
        return token

    monkeypatch.setattr(attachments, "store_attachment", fake_store)
    monkeypatch.setattr(attachments, "find_by_disk_file_id", fake_find)
    monkeypatch.setattr(attachments, "attachment_bytes", fake_bytes)
    monkeypatch.setattr(attachments, "finalize_capture", fake_finalize, raising=False)
    return rows


def _capture(payload, **kw):
    return b24bot._b24_capture_event_files(
        payload, ENDPOINT, SENDER_TOKEN, agent_slug=None, dialog_id=ARTHUR_DIALOG,
        from_user_id=28, bot_id=MAIN_BOT_ID, **kw)


def test_intake_captures_bytes_with_the_senders_token(portal, store):
    assert _capture(_event_payload()) == 1

    row = attachments.find_by_disk_file_id(ARTHUR_FILE_ID)
    assert row is not None, "файл Артура обязан быть сохранён на приёме события"
    assert row["file_name"] == ARTHUR_FILE
    assert attachments.attachment_bytes(row["token"])[0] == ARTHUR_BYTES
    # На приёме — только байты: распознавание стоит денег и времени и живёт в воркере.
    assert row["extracted_text"] == ""
    assert portal["download_tokens"] == [SENDER_TOKEN]


def test_intake_asks_the_dialog_the_sender_sees(portal, store):
    """Личный чат ключуется id собеседника: для отправителя это бот, а не он сам."""
    _capture(_event_payload())
    assert portal["recover_dialogs"] == [str(MAIN_BOT_ID)]


def test_intake_is_idempotent_across_bitrix_redelivery(portal, store):
    payload = _event_payload()
    assert _capture(payload) == 1
    assert _capture(payload) == 0, "повторная доставка события не должна плодить копии файла"
    assert len(store) == 1


def test_deferred_worker_reads_arthurs_file_from_the_capture(portal, store, monkeypatch):
    """Жалоба Артура целиком: приняли событие, ответили позже — таблица обязана прочитаться."""
    extracted = []

    def fake_extract(data, name):
        extracted.append((data, name))
        return "Поставщик;Цена\nBINTEX;225"

    monkeypatch.setattr(b24bot, "_b24_extract_document", fake_extract)
    _capture(_event_payload())

    # Воркер: payload уже без токенов, в руках только постоянный токен приложения.
    import bitrix_inbound
    queued = bitrix_inbound.token_free_payload(_event_payload())
    _images, _reply, doc_blocks, attached, _voice = b24bot._b24_message_extras(
        queued, ENDPOINT, APP_TOKEN, agent_slug=None,
        dialog_id=ARTHUR_DIALOG, from_user_id=28)

    assert extracted == [(ARTHUR_BYTES, ARTHUR_FILE)]
    assert len(doc_blocks) == 1
    name, text, token = doc_blocks[0]
    assert name == ARTHUR_FILE
    assert "BINTEX" in text
    assert "не удалось скачать файл" not in text
    assert attached and attached[0]["token"] == token
    # Одна строка на файл: сырой захват дополняется текстом, а не дублируется.
    assert len(store) == 1
    assert store[token]["kind"] == "document"


def test_worker_never_redownloads_what_the_portal_denies_it(portal, store, monkeypatch):
    """Дословный симптом жалобы: файл лежит рядом, а агент отвечает «не смог открыть».

    Байты уже в хранилище; воркеру остаётся их взять. Пока он вместо этого шёл в портал
    токеном пользователя 22, сотрудник получал отказ при полностью читаемом файле."""
    monkeypatch.setattr(b24bot, "_b24_extract_document", lambda data, name: data.decode("latin-1"))
    attachments.store_attachment(
        data=ARTHUR_BYTES, file_name=ARTHUR_FILE, kind="inbound_raw", extracted_text="",
        agent_slug=None, dialog_id=ARTHUR_DIALOG, source_disk_file_id=ARTHUR_FILE_ID)

    _images, _reply, doc_blocks, _attached, _voice = b24bot._b24_message_extras(
        _event_payload(), ENDPOINT, APP_TOKEN, agent_slug=None,
        dialog_id=ARTHUR_DIALOG, from_user_id=28)

    assert "не удалось скачать файл" not in doc_blocks[0][1]
    assert portal["download_tokens"] == [], "лишний поход в портал за уже сохранённым файлом"


def test_uncaptured_file_still_fails_honestly(portal, store):
    """Если захват не сработал, агент обязан сказать правду, а не выдумать содержимое."""
    queued = {k: v for k, v in _event_payload().items()}
    _images, _reply, doc_blocks, _attached, _voice = b24bot._b24_message_extras(
        queued, ENDPOINT, APP_TOKEN, agent_slug=None,
        dialog_id=ARTHUR_DIALOG, from_user_id=28)

    assert len(doc_blocks) == 1
    assert "не удалось скачать файл" in doc_blocks[0][1]


def test_durable_intake_captures_before_the_payload_loses_its_token():
    """Захват обязан стоять в ветке durable-приёма, до постановки в очередь."""
    source = b24bot.__file__.replace(".pyc", ".py")
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    branch = text.split("if bitrix_inbound.enabled():", 1)[1].split("bitrix_inbound.enqueue(", 1)[0]
    assert "_b24_capture_event_files(" in branch
