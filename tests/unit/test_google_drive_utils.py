from datetime import datetime, timezone

from shared.google_drive_utils import (
    google_drive_document_content,
    google_drive_document_structured_content,
    google_drive_path_from_parts,
    google_timestamp_for_apps_script,
    parse_google_timestamp,
)


def test_google_drive_path_from_list_and_string():
    assert google_drive_path_from_parts(["  Клиенты ", "", "Документы"], "file.pdf") == "Клиенты / Документы / file.pdf"
    assert google_drive_path_from_parts("Клиенты / / Договоры") == "Клиенты / Договоры"
    assert google_drive_path_from_parts(None) == ""


def test_google_drive_document_content_adds_available_header_fields():
    content = google_drive_document_content(
        {
            "url": "https://drive.example/doc",
            "updated_at": "2026-06-29T10:00:00Z",
            "mime_type": "text/plain",
            "content": "Текст документа",
        }
    )
    assert "Источник: https://drive.example/doc" in content
    assert "Обновлено в Google Drive: 2026-06-29T10:00:00Z" in content
    assert "Тип: text/plain" in content
    assert content.endswith("Текст документа")


def test_google_drive_document_structured_content_renders_blocks_and_tables():
    content = google_drive_document_structured_content(
        {
            "blocks": [
                {"type": "heading", "text": "Раздел"},
                {"type": "paragraph", "text": "Описание"},
                {"type": "list_item", "text": "Пункт"},
                {
                    "type": "table",
                    "title": "Таблица клиентов",
                    "headers": ["Клиент", "Сумма"],
                    "rows": [["Альфа", "100"], ["Бета", ""]],
                    "records": [{"Менеджер": "Иван", "Пусто": ""}],
                },
            ],
            "url": "https://drive.example/doc",
        }
    )
    assert "# Раздел" in content
    assert "- Пункт" in content
    assert "Таблица клиентов" in content
    assert "Строка 1: Клиент: Альфа | Сумма: 100" in content
    assert "Строка 2: Клиент: Бета | Сумма: ∅" in content
    assert "Запись 1:\n- Менеджер: Иван" in content


def test_google_drive_document_structured_content_falls_back_to_plain_content():
    assert google_drive_document_structured_content({"content": "Обычный текст"}) == "Обычный текст"


def test_google_timestamp_roundtrip_helpers():
    parsed = parse_google_timestamp("2026-06-29T10:15:30.123Z")
    assert parsed == datetime(2026, 6, 29, 10, 15, 30, 123000, tzinfo=timezone.utc)
    assert parse_google_timestamp("") is None
    assert parse_google_timestamp("not-a-date") is None
    assert google_timestamp_for_apps_script(parsed) == "2026-06-29T10:15:30.123Z"
    assert google_timestamp_for_apps_script(datetime(2026, 6, 29, 10, 15, 30, 123000)) == "2026-06-29T10:15:30.123Z"
    assert google_timestamp_for_apps_script("nope") == ""
