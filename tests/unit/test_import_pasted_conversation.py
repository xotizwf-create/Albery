from __future__ import annotations

from datetime import datetime, timezone

from scripts import import_pasted_conversation as importer


def write(tmp_path, text):
    path = tmp_path / "разговор.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_who_said_what(tmp_path):
    path = write(
        tmp_path,
        """telegram_id: 212850563
username: yulia1344
name: Юлия

клиент: Здравствуйте, интересуют условия
мы: Добрый день! Отправляю условия.
клиент: Спасибо, изучу
""",
    )

    header, messages = importer.parse_file(path)

    assert header["telegram_id"] == "212850563"
    assert header["username"] == "yulia1344"
    assert [m["author_type"] for m in messages] == ["client", "operator", "client"]
    assert messages[0]["text"] == "Здравствуйте, интересуют условия"


def test_multiline_message_keeps_its_paragraphs(tmp_path):
    """В переписке абзацы — обычное дело: продолжение не должно превращаться в новое
    сообщение неизвестного автора."""
    path = write(
        tmp_path,
        """клиент: Добрый день!
Хотел уточнить по срокам
и по стоимости
мы: Отвечаю по порядку
""",
    )

    _header, messages = importer.parse_file(path)

    assert len(messages) == 2
    assert messages[0]["text"] == "Добрый день!\nХотел уточнить по срокам\nи по стоимости"


def test_explicit_timestamps_are_respected(tmp_path):
    path = write(
        tmp_path,
        "[24.07.2026 14:05] клиент: А по срокам что?\n[24.07.2026 14:40] мы: Две недели\n",
    )

    _header, messages = importer.parse_file(path)

    assert messages[0]["occurred_at"] == datetime(2026, 7, 24, 14, 5, tzinfo=timezone.utc)
    assert messages[1]["occurred_at"] == datetime(2026, 7, 24, 14, 40, tzinfo=timezone.utc)


def test_missing_times_keep_the_order(tmp_path):
    """Без времени порядок важнее точности: сообщения обязаны идти как в переписке."""
    path = write(tmp_path, "клиент: раз\nмы: два\nклиент: три\n")
    _header, messages = importer.parse_file(path)
    start = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)

    importer.fill_times(messages, start)

    stamps = [m["occurred_at"] for m in messages]
    assert stamps == sorted(stamps)
    assert stamps[0] == start


def test_unknown_author_is_glued_to_the_previous_message(tmp_path):
    path = write(tmp_path, "клиент: вопрос\nСофья: а это кто\n")

    _header, messages = importer.parse_file(path)

    assert len(messages) == 1
    assert "Софья: а это кто" in messages[0]["text"]
