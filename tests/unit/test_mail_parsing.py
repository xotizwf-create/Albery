"""Разбор писем поставщиков: в работу идёт только новый текст, а не вся история ветки.

Ящик закупок allberi.otdel.zakupok@gmail.com ведёт переписку с фабриками: уходит запрос,
приходит «Re: Запрос по…» с условиями и прайсом во вложении. Если не отсекать цитату,
в разбор попадает наш собственный запрос, и из него вычитываются «условия поставщика»,
которых тот не давал. Поэтому цитата режется по САМОМУ РАННЕМУ маркеру.

Формы писем взяты с живой почты 17.08.2026 (stuff-textile.ru, konstantaplus@mail.ru,
norsy.ru): текст + HTML-вариант, вложение-прайс, русские и английские маркеры цитирования.
"""
from __future__ import annotations

import base64

import pytest

import mail


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def message(parts=None, headers=None, **kw):
    return {
        "id": kw.get("id", "m1"),
        "threadId": kw.get("thread_id", "t1"),
        "labelIds": kw.get("labels", ["INBOX"]),
        "snippet": kw.get("snippet", ""),
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": k, "value": v} for k, v in (headers or {}).items()],
            "parts": parts or [],
        },
    }


def text_part(body: str) -> dict:
    return {"mimeType": "text/plain", "body": {"data": b64(body)}}


def html_part(body: str) -> dict:
    return {"mimeType": "text/html", "body": {"data": b64(body)}}


def attachment_part(name: str, size: int = 40000) -> dict:
    return {"mimeType": "application/vnd.ms-excel", "filename": name,
            "body": {"attachmentId": "att-1", "size": size}}


# --- отсечение цитат -------------------------------------------------------------------

@pytest.mark.parametrize("quoted_start", [
    "> Добрый день! Просим прислать остатки",
    "17 августа 2026 г. в 10:12 Отдел закупок написал:",
    "On Mon, 17 Aug 2026 at 10:12, Otdel Zakupok wrote:",
    "-----Пересылаемое сообщение-----",
    "--- Исходное сообщение ---",
])
def test_quoted_history_is_cut_off(quoted_start):
    body = f"Добрый день! Прайс во вложении, НДС включён.\n\n{quoted_start}\nнаш прежний запрос"
    assert mail.strip_quoted(body) == "Добрый день! Прайс во вложении, НДС включён."


def test_cut_happens_at_the_earliest_marker():
    """Иначе часть чужого текста доедет до разбора и станет «условиями поставщика»."""
    body = ("Отгрузка от 3 дней.\n"
            "On Mon, 17 Aug 2026, wrote:\n"
            "> прежний запрос\n"
            "17 августа 2026 г. Отдел закупок написал:\n")
    assert mail.strip_quoted(body) == "Отгрузка от 3 дней."


def test_message_without_quotes_survives_intact():
    body = "Наличие: халаты флис 420 шт, цена 890 руб с НДС."
    assert mail.strip_quoted(body) == body


# --- разбор письма ---------------------------------------------------------------------

def test_real_shaped_supplier_reply():
    raw = message(
        headers={"From": "Stuff Textile <info@stuff-textile.ru>",
                 "Subject": "Re: Запрос по нательному белью, костюмам и готовым остаткам",
                 "Date": "Mon, 17 Aug 2026 09:40:11 +0300",
                 "To": "allberi.otdel.zakupok@gmail.com"},
        parts=[text_part("Добрый день! Цены с НДС, продажа от 1 ед.\n\n"
                         "17 августа 2026 г. Отдел закупок написал:\n> наш запрос"),
               attachment_part("ПРАЙС STUFF - 01.08.2026 - НДС.xlsx")],
    )
    parsed = mail.parse_message(raw)

    assert parsed["from"] == "info@stuff-textile.ru"
    assert parsed["from_name"] == "Stuff Textile"
    assert parsed["subject"].startswith("Re: Запрос")
    assert parsed["body"] == "Добрый день! Цены с НДС, продажа от 1 ед."
    assert [a["filename"] for a in parsed["attachments"]] == ["ПРАЙС STUFF - 01.08.2026 - НДС.xlsx"]


def test_price_list_attachment_is_reported():
    """Прайс приходит файлом — потерять его значит потерять смысл письма."""
    raw = message(headers={"From": "a@b.ru"},
                  parts=[text_part("прайс во вложении"),
                         attachment_part("Прайс.xlsx", size=51200)])
    attachments = mail.parse_message(raw)["attachments"]

    assert len(attachments) == 1
    assert attachments[0]["size"] == 51200
    assert attachments[0]["attachment_id"] == "att-1"


def test_html_only_letter_is_readable():
    """Часть фабрик шлёт только HTML; без разбора тело было бы пустым."""
    raw = message(headers={"From": "a@b.ru"}, parts=[
        html_part("<html><style>p{}</style><body><p>Отгрузка <b>3 дня</b></p>"
                  "<p>Цена 890 руб</p></body></html>")])
    body = mail.parse_message(raw)["body"]

    assert "Отгрузка" in body and "3 дня" in body and "890" in body
    assert "<" not in body and "style" not in body


def test_plain_text_wins_over_html():
    raw = message(headers={"From": "a@b.ru"},
                  parts=[text_part("чистый текст"), html_part("<p>разметка</p>")])
    assert mail.parse_message(raw)["body"] == "чистый текст"


def test_nested_parts_are_walked():
    raw = message(headers={"From": "a@b.ru"}, parts=[
        {"mimeType": "multipart/alternative", "body": {},
         "parts": [text_part("вложенный текст"), attachment_part("Прайс.pdf")]}])
    parsed = mail.parse_message(raw)

    assert parsed["body"] == "вложенный текст"
    assert parsed["attachments"][0]["filename"] == "Прайс.pdf"


def test_long_letter_is_bounded_and_says_so():
    """Один ответ не должен заливать модель — но об обрезке надо сообщить."""
    raw = message(headers={"From": "a@b.ru"}, parts=[text_part("я" * 20000)])
    parsed = mail.parse_message(raw)

    assert len(parsed["body"]) <= mail._BODY_CHAR_BUDGET
    assert parsed.get("body_truncated") is True


def test_keep_quoted_is_available_for_manual_review():
    raw = message(headers={"From": "a@b.ru"},
                  parts=[text_part("новое\n> старое")])
    assert "старое" in mail.parse_message(raw, keep_quoted=True)["body"]


def test_missing_token_fails_with_an_actionable_message(monkeypatch, tmp_path):
    """«Почта не подключена» обязано читаться как инструкция, а не как трассировка."""
    monkeypatch.setattr(mail, "MAIL_TOKEN_PATHS", (str(tmp_path / "нет.json"),))
    with pytest.raises(mail.MailNotConnected) as err:
        mail.mail_credentials()
    assert "согласие" in str(err.value).lower() or "токен" in str(err.value).lower()
