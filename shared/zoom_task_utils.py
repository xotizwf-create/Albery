"""Pure Zoom operational task formatting helpers for Albery.

This module intentionally has no Flask, DB, network, Bitrix, or secret access.
It characterizes existing app.py behavior and keeps the same helper names
exported through app.py.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

MSK_TZ = ZoneInfo("Europe/Moscow")


ZOOM_ARTIFACT_LABELS = {
    "screenshot": "скриншот", "link": "ссылка", "file": "файл",
    "comment": "комментарий", "photo": "фото",
    "скриншот": "скриншот", "ссылка": "ссылка", "файл": "файл",
    "комментарий": "комментарий", "фото": "фото",
}

ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO = (
    "Также во время созвона были выделены следующие задачи, добавьте себе задачи, которые считаете нужными, "
    "в комментарии напишите, что добавили, а что нет, подтвердите артефактом"
)


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MSK_TZ)
    return parsed


def _safe_parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        parsed = _parse_datetime(text)
        return parsed.astimezone(MSK_TZ).date() if parsed else None


def extract_zoom_operational_tasks_section(note: str) -> str:
    text = str(note or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    collecting = False
    section_lines: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not collecting:
            if re.match(r"^\s*(?:4[.)]|IV[.)]?)\s*\**\s*Операционные задачи", line, re.IGNORECASE):
                collecting = True
            continue
        if re.match(
            r"^\s*(?:[5-9][.)]|1[0-2][.)]|V[.)]?|VI[.)]?|VII[.)]?|VIII[.)]?|IX[.)]?)\s+\**\s*"
            r"(?:Поведенческие|Риски|Проблемы|Блокеры|Решения|Итоги|Вывод|Следующие|Контроль|Рекомендации)",
            line,
            re.IGNORECASE,
        ):
            break
        section_lines.append(raw.rstrip())
    return "\n".join(section_lines).strip()


def sentence_case_ru(value: Any) -> str:
    text = str(value or "").strip().rstrip(".")
    return text[:1].upper() + text[1:] if text else text


def first_text_value(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def split_zoom_operational_task_items(section: str) -> list[str]:
    text = str(section or "").strip()
    if not text:
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        markers = list(re.finditer(r"(?:^|\s)(\d+)[).]\s+", line))
        if len(markers) <= 1:
            lines.append(line)
            continue
        for index, marker in enumerate(markers):
            start = marker.start()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(line)
            item = line[start:end].strip()
            if item:
                lines.append(item)
    return lines


def extract_zoom_labeled_parts(text: str) -> tuple[str, dict[str, str]]:
    label_pattern = re.compile(r"(Срок|Критерий(?:\s+результата)?|Подтверждение|Статус|Источник)\s*:", re.IGNORECASE)
    matches = list(label_pattern.finditer(text))
    if not matches:
        return text.strip().strip(". "), {}
    unlabeled_text = text[:matches[0].start()].strip().strip(". ")
    labels: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1).lower().replace(" ", "_")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip().strip(". ")
        labels[key] = value
    return unlabeled_text, labels


def parse_zoom_operational_task_line(line: str, fallback_number: int) -> dict[str, Any] | None:
    text = str(line or "").strip()
    if not text:
        return None
    match = re.match(r"^\s*(\d+)[.)]\s*(.*)$", text, re.DOTALL)
    if match:
        number = _to_int(match.group(1)) or fallback_number
        body = match.group(2).strip()
    else:
        number = fallback_number
        body = text

    assignee_name = ""
    strict_assignee = re.match(r"^Ответственный:\s*(.*?)\.\s*(.*)$", body, re.IGNORECASE | re.DOTALL)
    if strict_assignee:
        assignee_name = strict_assignee.group(1).strip()
        body = strict_assignee.group(2).strip()
    elif "—" in body:
        assignee_name, body = [part.strip() for part in body.split("—", 1)]
    elif " - " in body:
        assignee_name, body = [part.strip() for part in body.split(" - ", 1)]

    body = re.sub(r"^Задача:\s*", "", body, flags=re.IGNORECASE).strip()
    task_text, labels = extract_zoom_labeled_parts(body)
    result_criteria = first_text_value(labels.get("критерий_результата"), labels.get("критерий"))
    expected_artifact = first_text_value(labels.get("подтверждение"), "")
    deadline_text = first_text_value(labels.get("срок"), "срок не указан")
    status = first_text_value(labels.get("статус"), "planned")
    source = first_text_value(labels.get("источник"), "")
    if not task_text:
        return None
    return {
        "number": number,
        "assignee_name": first_text_value(assignee_name, "Требует назначения"),
        "bitrix_user_id": None,
        "task_text": sentence_case_ru(task_text),
        "deadline_text": deadline_text.strip().rstrip(".") or "срок не указан",
        "result_criteria": result_criteria.strip().rstrip("."),
        "expected_artifact": expected_artifact.strip().rstrip("."),
        "status": status.strip().rstrip(".") or "planned",
        "source": source.strip().rstrip("."),
        "raw": {"source_line": text},
    }


def normalize_zoom_operational_tasks(
    section: str = "",
    analysis: dict[str, Any] | None = None,
    existing_tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    source_items: list[Any] = []
    if existing_tasks:
        source_items = existing_tasks
    elif isinstance(analysis, dict):
        for key in ("operational_tasks", "tasks"):
            value = analysis.get(key)
            if isinstance(value, list) and value:
                source_items = value
                break
    for index, item in enumerate(source_items, start=1):
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence_times = [
            str(evidence_item.get("time") or "").strip()
            for evidence_item in evidence
            if isinstance(evidence_item, dict) and str(evidence_item.get("time") or "").strip()
        ]
        task_text = first_text_value(item.get("task_text"), item.get("task"), item.get("action"), item.get("text"))
        result_criteria = first_text_value(
            item.get("result_criteria"),
            item.get("success_criteria"),
            item.get("criteria"),
            item.get("criterion"),
        )
        expected_artifact = first_text_value(item.get("expected_artifact"), "")
        expected_artifact = ZOOM_ARTIFACT_LABELS.get(expected_artifact.strip().lower(), expected_artifact.strip())
        deadline_text = first_text_value(item.get("deadline_text"), item.get("deadline"), "срок не указан")
        assignee_name = first_text_value(
            item.get("assignee_name"),
            item.get("responsible"),
            item.get("responsible_name"),
            item.get("person_name"),
            item.get("org_person"),
            item.get("display_owner"),
            item.get("owner"),
            "Требует назначения",
        )
        if not task_text:
            continue
        tasks.append({
            "number": _to_int(item.get("number")) or index,
            "assignee_name": assignee_name,
            "bitrix_user_id": _to_int(_first_non_empty(item.get("bitrix_user_id"), item.get("user_id"))),
            "task_text": sentence_case_ru(task_text),
            "deadline_text": deadline_text.strip().rstrip(".") or "срок не указан",
            "result_criteria": result_criteria.strip().rstrip("."),
            "expected_artifact": expected_artifact,
            "status": first_text_value(item.get("status"), "planned"),
            "source": first_text_value(item.get("source"), item.get("timecode"), ", ".join(evidence_times)),
            "raw": item.get("raw") if isinstance(item.get("raw"), dict) else item,
        })

    section_tasks: list[dict[str, Any]] = []
    for raw in split_zoom_operational_task_items(section):
        parsed = parse_zoom_operational_task_line(raw, len(section_tasks) + 1)
        if parsed:
            section_tasks.append(parsed)
    if section_tasks and len(section_tasks) > len(tasks):
        return section_tasks
    return tasks or section_tasks


def format_zoom_operational_tasks_for_bitrix(tasks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, task in enumerate(tasks, start=1):
        task_text = sentence_case_ru(task.get("task_text"))
        result_criteria = str(task.get("result_criteria") or "").strip().rstrip(".")
        expected_artifact = str(task.get("expected_artifact") or "").strip().rstrip(".")
        deadline_text = str(task.get("deadline_text") or "срок не указан").strip().rstrip(".")
        line = f"{index}. {task_text}."
        line += f" Срок: {deadline_text}."
        if result_criteria:
            line += f" Критерий: {result_criteria}."
        if expected_artifact:
            line += f" Подтверждение: {expected_artifact}."
        lines.append(line)
    return "\n".join(lines).strip()


def clean_zoom_operational_tasks_section(section: str) -> str:
    return format_zoom_operational_tasks_for_bitrix(normalize_zoom_operational_tasks(section=section))


def build_zoom_card_description(
    dispatch_summary: str,
    leader_message: str,
    tasks: list[dict[str, Any]],
) -> str:
    """Description of one person's "Итоги созвона" task: summary, eval, then task list."""
    parts: list[str] = []
    if dispatch_summary.strip():
        parts.append(dispatch_summary.strip())
    if leader_message.strip():
        parts.append(f"Оценка Вас как руководителя: {leader_message.strip()}")
    if tasks:
        task_text = format_zoom_operational_tasks_for_bitrix(tasks)
        if task_text:
            parts.append(f"{ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO}\n\n{task_text}")
    return "\n\n".join(parts).strip()


def zoom_dispatch_title(call: dict[str, Any]) -> str:
    call_date = _safe_parse_date(call.get("date"))
    if call_date is None:
        start_for_date = _parse_datetime(call.get("start_time_msk"))
        call_date = start_for_date.astimezone(MSK_TZ).date() if start_for_date else None
    date_prefix = call_date.strftime("%d.%m") if call_date else ""

    time_text = str(call.get("time_text") or "").strip()
    start_text = ""
    end_text = ""
    if "-" in time_text:
        left, right = time_text.split("-", 1)
        start_text = left.strip()
        end_text = right.strip()
    else:
        start_dt = _parse_datetime(call.get("start_time_msk"))
        end_dt = _parse_datetime(call.get("end_time_msk"))
        start_text = start_dt.astimezone(MSK_TZ).strftime("%H:%M") if start_dt else time_text
        end_text = end_dt.astimezone(MSK_TZ).strftime("%H:%M") if end_dt else ""

    suffix_parts = [part for part in [date_prefix, start_text] if part]
    suffix = ", ".join(suffix_parts)
    if end_text:
        suffix = f"{suffix} - {end_text}" if suffix else end_text
    return f"Итоги созвона {suffix or 'созвон'}".strip()


def format_zoom_lead_task_list(tasks: list[dict[str, Any]]) -> str:
    """Task list for the lead card — each task is a separate multi-line block so nothing blends."""
    blocks: list[str] = []
    for index, task in enumerate(tasks, start=1):
        assignee = str(task.get("assignee_name") or "Требует назначения").strip()
        task_text = sentence_case_ru(task.get("task_text")).strip().rstrip(".")
        result_criteria = str(task.get("result_criteria") or "").strip().rstrip(".")
        expected_artifact = str(task.get("expected_artifact") or "").strip().rstrip(".")
        deadline_text = (str(task.get("deadline_text") or "").strip().rstrip(".")) or "срок не указан"
        rows = [f"{index}. {assignee}"]
        rows.append(f"   • Задача: {task_text}.")
        rows.append(f"   • Срок: {deadline_text}.")
        if result_criteria:
            rows.append(f"   • Критерий: {result_criteria}.")
        if expected_artifact:
            rows.append(f"   • Подтверждение: {expected_artifact}.")
        blocks.append("\n".join(rows))
    return "\n\n".join(blocks).strip()


def build_zoom_lead_card_description(
    dispatch_summary: str,
    leader_message: str,
    tasks: list[dict[str, Any]],
) -> str:
    """Lead card with clear sections and separators: summary, leader evaluation, tasks to assign."""
    sep = "\n\n──────────────────────────────\n\n"
    parts: list[str] = []
    if dispatch_summary.strip():
        parts.append("📋 Сводка созвона\n\n" + dispatch_summary.strip())
    if leader_message.strip():
        parts.append("🧭 Оценка вас как руководителя\n\n" + leader_message.strip())
    task_text = format_zoom_lead_task_list(tasks)
    if task_text:
        parts.append(
            "✅ Задачи для постановки сотрудникам\n"
            "Поставьте задачи подчинённым в Битрикс; в конце дня отметьте в комментарии, "
            "что поставили, а что нет, и подтвердите артефактом.\n\n"
            + task_text
        )
    return sep.join(parts).strip()
