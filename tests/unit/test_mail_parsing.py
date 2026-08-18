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


# --- недоставленные письма --------------------------------------------------------------

class _FakeGmail:
    """Минимальный Gmail: список отбойников и их полное содержимое."""

    def __init__(self, messages: list[dict]):
        self._messages = {m["id"]: m for m in messages}
        self._pending = None

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId=None, q=None, maxResults=None):  # noqa: N803
        self._pending = {"messages": [{"id": i} for i in self._messages]}
        return self

    def get(self, userId=None, id=None, format=None, metadataHeaders=None):  # noqa: A002,N803
        self._pending = self._messages[id]
        return self

    def execute(self):
        return self._pending


BOUNCE_BODY = (
    "Адрес не найден\n\nСообщение не доставлено, так как адрес "
    "alexxandrn.nikitenko@gmail.com не найден или не принимает входящие письма.\n"
    "Полученный ответ: 550 5.1.1 The email account that you tried to reach does not exist."
)


def test_bounce_names_the_address_that_failed(monkeypatch):
    """Отправка «прошла», а письмо не дошло — это надо назвать вслух."""
    fake = _FakeGmail([message(
        id="b1",
        headers={"From": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                 "Subject": "Delivery Status Notification (Failure)",
                 "Date": "Mon, 17 Aug 2026 11:10:30 +0300"},
        parts=[text_part(BOUNCE_BODY)],
    )])
    monkeypatch.setattr(mail, "_service", lambda creds=None: fake)

    report = mail.mail_bounces(own_address="allberi.otdel.zakupok@gmail.com")

    assert report["count"] == 1
    assert report["addresses"] == ["alexxandrn.nikitenko@gmail.com"]
    assert "не найден" in report["bounces"][0]["reason"]


def test_own_address_is_not_reported_as_failed(monkeypatch):
    """В теле отбойника есть и наш адрес — обвинять себя незачем."""
    body = ("Сообщение от allberi.otdel.zakupok@gmail.com не доставлено, "
            "так как адрес triktex2@mail.ru не найден.")
    fake = _FakeGmail([message(
        id="b2",
        headers={"From": "mailer-daemon@googlemail.com", "Subject": "Undelivered Mail"},
        parts=[text_part(body)],
    )])
    monkeypatch.setattr(mail, "_service", lambda creds=None: fake)

    report = mail.mail_bounces(own_address="allberi.otdel.zakupok@gmail.com")

    assert report["addresses"] == ["triktex2@mail.ru"]


def test_ordinary_letter_from_a_person_is_not_a_bounce(monkeypatch):
    """Иначе обычный ответ поставщика попал бы в «не доставлено»."""
    fake = _FakeGmail([message(
        id="n1",
        headers={"From": "info@stuff-textile.ru", "Subject": "Re: Запрос по трикотажу"},
        parts=[text_part("Добрый день! Прайс во вложении.")],
    )])
    monkeypatch.setattr(mail, "_service", lambda creds=None: fake)

    assert mail.mail_bounces(own_address="allberi.otdel.zakupok@gmail.com")["count"] == 0


# --- вложения ----------------------------------------------------------------------------

def test_signature_logos_are_marked_inline():
    """Логотипы подписи имеют Content-ID и не являются документами поставщика."""
    part = {"mimeType": "image/png", "filename": "mailrusigimg_zbL3.png",
            "headers": [{"name": "Content-ID", "value": "<logo@mail.ru>"},
                        {"name": "Content-Disposition", "value": "inline"}],
            "body": {"attachmentId": "a1", "size": 39000}}
    raw = message(headers={"From": "a@b.ru"}, parts=[text_part("текст"), part])

    assert mail.parse_message(raw)["attachments"][0]["inline"] is True


def test_real_attachment_is_not_marked_inline():
    raw = message(headers={"From": "a@b.ru"},
                  parts=[attachment_part("ПРАЙС STUFF.xlsx")])
    assert mail.parse_message(raw)["attachments"][0]["inline"] is False


@pytest.mark.parametrize("filename,kind", [
    ("Прайс.xlsx", "document"), ("прайс.pdf", "document"), ("Условия.docx", "document"),
    ("остатки.xls", "document"), ("данные.csv", "document"),
    ("фото.jpg", "image"), ("образец.JPEG", "image"), ("скан.png", "image"),
    ("архив.rar", "other"), ("файл.exe", "other"), ("без_расширения", "other"),
])
def test_attachment_kind_routing(filename, kind):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    assert mail._attachment_kind(ext) == kind


def test_unknown_binary_is_refused_not_decoded(monkeypatch):
    """PNG, разобранный как utf-8, даёт 12 000 символов мусора — это уехало бы в анализ."""
    monkeypatch.setattr(mail, "mail_attachment_bytes", lambda *a, **kw: b"\x89PNG\x00\x01binary")
    result = mail.mail_attachment_text("m1", {"filename": "непонятно.rar", "size": 1000,
                                              "attachment_id": "a1"})

    assert "не поддержан" in result["error"]
    assert "text" not in result


def test_model_reasoning_never_reaches_the_analysis():
    """Распознавание возвращает и рассуждения модели — для агента это не содержимое фото."""
    leaked = ("<think>The user wants me to extract text. Let me look.</think>\n"
              "СВОБОДНАЯ ПОСАДКА, размер L")
    assert mail._strip_model_reasoning(leaked) == "СВОБОДНАЯ ПОСАДКА, размер L"


def test_unreadable_attachment_does_not_blame_the_supplier(monkeypatch):
    """Свалить свой лимит на «плохой скан» — значит забраковать нормальную фабрику."""
    monkeypatch.setattr(mail, "mail_attachment_bytes", lambda *a, **kw: b"\xff\xd8\xff\xe0jpeg")
    monkeypatch.setattr(mail, "_VISION_ATTEMPTS", 1)
    monkeypatch.setattr(mail, "_VISION_BACKOFF_S", 0)
    import b24bot
    monkeypatch.setattr(b24bot, "_b24_vision_ocr", lambda *a, **kw: "")

    result = mail.mail_attachment_text("m1", {"filename": "фото.jpg", "size": 1000,
                                              "attachment_id": "a1"})

    assert "поставщик тут ни при чём" in result["error"]
    assert "плохого качества" not in result["error"]


def test_oversized_attachment_is_not_downloaded():
    result = mail.mail_attachment_text("m1", {"filename": "огромный.pdf",
                                              "size": 99 * 1024 * 1024, "attachment_id": "a1"})
    assert "больше предела" in result["error"]
