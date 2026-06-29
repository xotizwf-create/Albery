from shared.zoom_task_utils import (
    ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO,
    build_zoom_card_description,
    build_zoom_lead_card_description,
    clean_zoom_operational_tasks_section,
    extract_zoom_labeled_parts,
    extract_zoom_operational_tasks_section,
    format_zoom_lead_task_list,
    format_zoom_operational_tasks_for_bitrix,
    normalize_zoom_operational_tasks,
    parse_zoom_operational_task_line,
    split_zoom_operational_task_items,
    zoom_dispatch_title,
)


def test_extract_zoom_operational_tasks_section_stops_at_next_report_section():
    note = """
1. Контекст
текст
4. **Операционные задачи**
1. Иван — проверить договор. Срок: завтра.
2. Ответственный: Мария. Задача: отправить отчёт. Критерий: отправлен.
5. Риски
не брать эту строку
"""

    section = extract_zoom_operational_tasks_section(note)

    assert "Иван" in section
    assert "Мария" in section
    assert "Риски" not in section


def test_split_zoom_operational_task_items_splits_multiple_numbered_items_on_one_line():
    items = split_zoom_operational_task_items("1. Первое 2) Второе\n3. Третье")

    assert items == ["1. Первое", "2) Второе", "3. Третье"]


def test_parse_zoom_operational_task_line_keeps_existing_label_behavior():
    task = parse_zoom_operational_task_line(
        "2. Ответственный: Иван Петров. Задача: проверить договор. "
        "Срок: завтра. Критерий результата: договор согласован. "
        "Подтверждение: ссылка. Статус: planned. Источник: 00:10",
        1,
    )

    assert task == {
        "number": 2,
        "assignee_name": "Иван Петров",
        "bitrix_user_id": None,
        "task_text": "Проверить договор",
        "deadline_text": "завтра",
        "result_criteria": "договор согласован",
        "expected_artifact": "ссылка",
        "status": "planned",
        "source": "00:10",
        "raw": {
            "source_line": "2. Ответственный: Иван Петров. Задача: проверить договор. "
            "Срок: завтра. Критерий результата: договор согласован. "
            "Подтверждение: ссылка. Статус: planned. Источник: 00:10"
        },
    }


def test_extract_zoom_labeled_parts_preserves_unknown_text_before_first_label():
    text, labels = extract_zoom_labeled_parts("сделать отчёт. Срок: пятница. Критерий: готов")

    assert text == "сделать отчёт"
    assert labels == {"срок": "пятница", "критерий": "готов"}


def test_normalize_from_analysis_maps_alias_fields_and_artifact_labels():
    tasks = normalize_zoom_operational_tasks(
        analysis={
            "tasks": [
                {
                    "action": "собрать обратную связь",
                    "owner": "Анна",
                    "deadline": "до среды",
                    "success_criteria": "ответы собраны",
                    "expected_artifact": "screenshot",
                    "user_id": "77",
                    "evidence": [{"time": "00:01"}, {"time": "00:04"}],
                }
            ]
        }
    )

    assert tasks[0]["task_text"] == "Собрать обратную связь"
    assert tasks[0]["assignee_name"] == "Анна"
    assert tasks[0]["bitrix_user_id"] == 77
    assert tasks[0]["expected_artifact"] == "скриншот"
    assert tasks[0]["source"] == "00:01, 00:04"


def test_section_tasks_win_only_when_there_are_more_than_analysis_tasks():
    analysis = {"operational_tasks": [{"task_text": "оставить старую задачу"}]}
    section = "1. Иван — первая.\n2. Мария — вторая."

    tasks = normalize_zoom_operational_tasks(section=section, analysis=analysis)

    assert [task["assignee_name"] for task in tasks] == ["Иван", "Мария"]


def test_clean_zoom_operational_tasks_section_formats_bitrix_ready_lines():
    text = clean_zoom_operational_tasks_section("1. Иван — проверить договор. Срок: завтра. Критерий: согласован")

    assert text == "1. Проверить договор. Срок: завтра. Критерий: согласован."


def test_format_zoom_operational_tasks_for_bitrix_omits_empty_optional_clauses():
    text = format_zoom_operational_tasks_for_bitrix([
        {"task_text": "позвонить клиенту", "deadline_text": "срок не указан"}
    ])

    assert text == "1. Позвонить клиенту. Срок: срок не указан."
    assert "Критерий:" not in text
    assert "Подтверждение:" not in text


def test_zoom_dispatch_title_supports_time_text_and_iso_datetimes():
    assert zoom_dispatch_title({"date": "2026-06-02", "time_text": "14:00 - 14:42"}) == (
        "Итоги созвона 02.06, 14:00 - 14:42"
    )
    assert zoom_dispatch_title({
        "start_time_msk": "2026-06-02T14:00:00+03:00",
        "end_time_msk": "2026-06-02T14:42:00+03:00",
    }) == "Итоги созвона 02.06, 14:00 - 14:42"


def test_build_zoom_card_description_for_leader_is_personal():
    description = build_zoom_card_description(
        "Обсуждали: поставки.\nРешили: сверить таблицу.",
        "Вы хорошо удержали повестку и мягко вернули обсуждение к срокам.",
        [
            {
                "task_text": "проверить таблицу фабрик",
                "deadline_text": "03.06.2026",
                "result_criteria": "таблица обновлена и отправлена Артуру",
            }
        ],
    )

    assert "Оценка Вас как руководителя: Вы хорошо удержали повестку" in description
    assert ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO in description
    assert "1. Проверить таблицу фабрик. Срок: 03.06.2026. Критерий: таблица обновлена" in description


def test_format_zoom_lead_task_list_keeps_multiline_blocks():
    text = format_zoom_lead_task_list([
        {
            "assignee_name": "Иван",
            "task_text": "проверить таблицу",
            "deadline_text": "завтра",
            "result_criteria": "готово",
            "expected_artifact": "ссылка",
        }
    ])

    assert text == (
        "1. Иван\n"
        "   • Задача: Проверить таблицу.\n"
        "   • Срок: завтра.\n"
        "   • Критерий: готово.\n"
        "   • Подтверждение: ссылка."
    )


def test_build_zoom_lead_card_description_uses_section_separators():
    text = build_zoom_lead_card_description("Сводка", "Оценка", [{"task_text": "сделать", "assignee_name": "Иван"}])

    assert "📋 Сводка созвона" in text
    assert "🧭 Оценка вас как руководителя" in text
    assert "✅ Задачи для постановки сотрудникам" in text
    assert "──────────────────────────────" in text
