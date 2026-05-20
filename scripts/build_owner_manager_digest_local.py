from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


MODEL = "local_owner_manager_digest_no_external_ai_v1"


@dataclass
class PersonFact:
    date: str
    kind: str
    text: str
    status: str | None = None
    deadline: str | None = None
    source: str | None = None
    priority: str = "medium"
    evidence: list[Any] = field(default_factory=list)


def clean_person_name(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    text = re.sub(r"\s*/.*$", "", text)
    text = re.sub(r"\s*\(.*?\)\s*", " ", text)
    text = text.replace("требуется назначение владельца со стороны проекта", "")
    text = text.strip(" .;:-")
    aliases = {
        "Артур Степанян": "Артур Игоревич Степанян",
        "Наталья Горюнова": "Наталья Викторовна Горюнова",
        "Евгений": "Евгений Палей",
        "Евгений Палей": "Евгений Палей",
        "Дмитрий Строгонов": "Дмитрий Строгонов",
        "Сергей Виноградов": "Сергей Виноградов",
        "Анастасия Андрусяк": "Анастасия Андрусяк",
        "Оксана Хапова": "Оксана Александровна Хапова",
        "Олеся Тагирова": "Олеся Сергеевна ТАГИРОВА",
        "Софья Погорелова": "Софья Александровна Погорелова",
    }
    for key, canonical in aliases.items():
        if key.lower() in text.lower():
            return canonical
    if "Все руководители" in text:
        return "Все руководители"
    return text or None


def load_users() -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    users_by_name: dict[str, dict[str, Any]] = {}
    subs_by_manager: dict[str, list[str]] = defaultdict(list)
    with app.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id, u.bitrix_user_id, u.full_name, u.work_position,
                       m.id AS manager_user_id, m.bitrix_user_id AS manager_bitrix_user_id,
                       m.full_name AS manager_name
                FROM users u
                LEFT JOIN users m ON m.id = u.manager_id
                WHERE u.is_active = TRUE
                ORDER BY u.full_name
                """
            )
            for row in cur.fetchall():
                item = dict(row)
                users_by_name[item["full_name"]] = item
                if item.get("manager_name"):
                    subs_by_manager[item["manager_name"]].append(item["full_name"])
    return users_by_name, subs_by_manager


def load_current_chat_reports(start: date, end: date) -> list[dict[str, Any]]:
    with app.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.dialog_id, c.chat_title, r.report_date, r.messages_count,
                       r.files_count, r.ocr_files_count, r.summary, r.raw_ai_json
                FROM chat_daily_reports r
                JOIN chats c ON c.id = r.chat_id
                WHERE r.is_current = TRUE
                  AND r.report_date BETWEEN %s AND %s
                  AND c.is_excluded = FALSE
                ORDER BY r.report_date, c.chat_title
                """,
                (start, end),
            )
            return [dict(row) for row in cur.fetchall()]


def fact_priority(fact: PersonFact, report_end: date) -> str:
    status = (fact.status or "").lower()
    if "risk" in fact.kind:
        return "high"
    if status in {"blocked"}:
        return "critical"
    if status in {"no_info", "unanswered", "unclear"}:
        if fact.deadline:
            try:
                if datetime.fromisoformat(fact.deadline).date() <= report_end:
                    return "high"
            except Exception:
                pass
        return "medium"
    if status in {"assigned", "planned", "high_priority"}:
        return "high" if status == "high_priority" else "medium"
    return "medium"


def add_fact(store: dict[str, list[PersonFact]], person: str | None, fact: PersonFact) -> None:
    owner = clean_person_name(person)
    if not owner:
        owner = "Требует назначения"
    store[owner].append(fact)


def collect_facts(reports: list[dict[str, Any]], report_end: date) -> tuple[dict[str, list[PersonFact]], dict[str, Any]]:
    facts_by_person: dict[str, list[PersonFact]] = defaultdict(list)
    totals = {
        "messages": 0,
        "files": 0,
        "ocr_files": 0,
        "reports": len(reports),
        "no_data_days": [],
        "critical_risks": [],
    }
    for report in reports:
        day = report["report_date"].isoformat()
        totals["messages"] += int(report.get("messages_count") or 0)
        totals["files"] += int(report.get("files_count") or 0)
        totals["ocr_files"] += int(report.get("ocr_files_count") or 0)
        if int(report.get("messages_count") or 0) == 0:
            totals["no_data_days"].append(day)
        raw = report.get("raw_ai_json") if isinstance(report.get("raw_ai_json"), dict) else {}
        analysis = raw.get("analysis") if isinstance(raw, dict) else {}
        if not isinstance(analysis, dict):
            continue

        for item in analysis.get("commitments") or []:
            if not isinstance(item, dict):
                continue
            fact = PersonFact(
                date=day,
                kind="task",
                text=str(item.get("text") or "").strip(),
                status=str(item.get("status") or "planned"),
                deadline=item.get("deadline"),
                source=f"chat:{report['chat_title']}",
                evidence=item.get("evidence_message_ids") or item.get("evidence_zoom_call_ids") or [],
            )
            fact.priority = fact_priority(fact, report_end)
            add_fact(facts_by_person, item.get("person_name"), fact)

        for item in analysis.get("previous_day_tasks") or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("current_status") or item.get("status") or "no_info")
            if status in {"done", "cancelled"}:
                continue
            fact = PersonFact(
                date=day,
                kind="hanging_task",
                text=str(item.get("text") or "").strip(),
                status=status,
                deadline=item.get("deadline"),
                source=f"chat:{report['chat_title']}",
                evidence=item.get("evidence_message_ids") or item.get("evidence_zoom_call_ids") or [],
            )
            fact.priority = fact_priority(fact, report_end)
            add_fact(facts_by_person, item.get("person_name"), fact)

        for item in analysis.get("unanswered_questions") or []:
            if not isinstance(item, dict):
                continue
            fact = PersonFact(
                date=day,
                kind="unanswered_question",
                text=str(item.get("text") or "").strip(),
                status=str(item.get("answer_status") or "unanswered"),
                deadline=None,
                source=f"chat:{report['chat_title']}",
                evidence=item.get("evidence_message_ids") or [],
            )
            fact.priority = fact_priority(fact, report_end)
            add_fact(facts_by_person, item.get("addressed_to") or item.get("asked_by"), fact)

        for item in analysis.get("explicit_risks") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            fact = PersonFact(
                date=day,
                kind="risk",
                text=text,
                status="open",
                deadline=None,
                source=f"chat:{report['chat_title']}",
                evidence=item.get("evidence_message_ids") or item.get("evidence_zoom_call_ids") or [],
            )
            fact.priority = fact_priority(fact, report_end)
            owner = item.get("person_name") or "Требует назначения"
            add_fact(facts_by_person, owner, fact)
            totals["critical_risks"].append(f"{day}: {text}")
    return facts_by_person, totals


def dedupe_facts(facts: list[PersonFact]) -> list[PersonFact]:
    by_key: dict[tuple[str, str], PersonFact] = {}
    status_rank = {
        "critical": 0,
        "blocked": 0,
        "high_priority": 1,
        "no_info": 2,
        "unanswered": 2,
        "unclear": 2,
        "assigned": 3,
        "planned": 3,
        "partial": 4,
        "in_progress": 4,
        "open": 4,
    }
    for fact in facts:
        normalized_text = re.sub(r"\s+", " ", fact.text.lower()).strip(" .;:-")
        key = ("fact", normalized_text)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = fact
            continue
        existing.date = max(existing.date, fact.date)
        if status_rank.get((fact.status or "").lower(), 9) < status_rank.get((existing.status or "").lower(), 9):
            existing.status = fact.status
            existing.kind = fact.kind
        elif fact.status == "no_info":
            existing.status = "no_info"
        if not existing.deadline and fact.deadline:
            existing.deadline = fact.deadline
        if fact.priority in {"critical", "high"}:
            existing.priority = fact.priority
    return sorted(by_key.values(), key=lambda f: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(f.priority, 2), f.date))


def manager_for_person(person: str, users_by_name: dict[str, dict[str, Any]]) -> str | None:
    if person == "Все руководители":
        return "Евгений Палей"
    if person == "Требует назначения":
        return "Евгений Палей"
    user = users_by_name.get(person)
    if not user:
        return "Евгений Палей"
    return user.get("manager_name") or user.get("full_name")


def action_for_fact(person: str, fact: PersonFact, own: bool = False) -> str:
    prefix = "Закрыть у себя" if own else f"Проверить у {person}"
    if fact.kind == "unanswered_question":
        prefix = "Ответить" if own else f"Добиться ответа от {person}"
    if fact.kind == "risk":
        prefix = "Назначить владельца и план реакции по риску" if person == "Требует назначения" else f"Снять риск с {person}"
    deadline = fact.deadline or "срок не указан"
    return f"{prefix}: {fact.text}. Срок: {deadline}. Ожидаемый результат: зафиксирован статус/закрытие в чате или задаче."


def build_manager_reports(
    facts_by_person: dict[str, list[PersonFact]],
    users_by_name: dict[str, dict[str, Any]],
    subs_by_manager: dict[str, list[str]],
    start: date,
    end: date,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    managers = set(subs_by_manager.keys()) | {"Евгений Палей"}
    for person in facts_by_person:
        mgr = manager_for_person(person, users_by_name)
        if mgr:
            managers.add(mgr)

    manager_reports: dict[str, dict[str, Any]] = {}
    manager_recommendations: list[dict[str, Any]] = []
    for manager in sorted(managers):
        subs = sorted(set(subs_by_manager.get(manager, [])))
        subordinate_facts: list[tuple[str, PersonFact]] = []
        own_facts = dedupe_facts(facts_by_person.get(manager, []))
        for person, facts in facts_by_person.items():
            if person == manager:
                continue
            if manager_for_person(person, users_by_name) == manager:
                for fact in dedupe_facts(facts):
                    subordinate_facts.append((person, fact))
        subordinate_facts.sort(key=lambda pf: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(pf[1].priority, 2), pf[0], pf[1].date))

        lines = [
            f"Адресный отчет для руководителя: {manager}",
            f"Период: {start.isoformat()} - {end.isoformat()}",
            "",
            "1. Подчиненные и зона контроля",
        ]
        if subs:
            lines.append(f"- Подчиненные по оргструктуре: {', '.join(subs)}.")
        else:
            lines.append("- Подчиненные по оргструктуре не указаны; рекомендации сформированы по фактическим задачам/рискам.")
        active_people = sorted({p for p, _ in subordinate_facts})
        lines.append(f"- Людей с фактами в периоде: {len(active_people)}.")
        lines.append(f"- Открытых/висящих пунктов по подчиненным: {len(subordinate_facts)}.")
        lines.append("")
        lines.append("2. Что проверить по подчиненным")
        if subordinate_facts:
            for person, fact in subordinate_facts[:12]:
                lines.append(f"- {person}: {fact.text}; статус: {fact.status or '-'}; срок: {fact.deadline or 'срок не указан'}; источник: {fact.source}; приоритет: {fact.priority}.")
        else:
            lines.append("- Нет открытых фактов по подчиненным в текущих отчетах.")
        lines.append("")
        lines.append("3. Ваши собственные задачи и вопросы")
        if own_facts:
            for fact in own_facts[:12]:
                lines.append(f"- {fact.text}; статус: {fact.status or '-'}; срок: {fact.deadline or 'срок не указан'}; источник: {fact.source}; приоритет: {fact.priority}.")
        else:
            lines.append("- Собственных открытых задач/вопросов в текущих отчетах нет.")
        lines.append("")
        lines.append("4. Рекомендации, которые можно отправить")

        actions: list[dict[str, Any]] = []
        for person, fact in subordinate_facts[:10]:
            actions.append(
                {
                    "subject": fact.text[:180],
                    "action": action_for_fact(person, fact, own=False),
                    "person": person,
                    "task_ref": fact.text[:120],
                    "due": fact.deadline or "срок не указан",
                    "priority": fact.priority,
                    "expected_result": "получен статус, срок или подтверждение закрытия",
                    "source": fact.source,
                    "kind": fact.kind,
                }
            )
        for fact in own_facts[:6]:
            actions.append(
                {
                    "subject": fact.text[:180],
                    "action": action_for_fact(manager, fact, own=True),
                    "person": manager,
                    "task_ref": fact.text[:120],
                    "due": fact.deadline or "срок не указан",
                    "priority": fact.priority,
                    "expected_result": "руководитель дал статус или закрыл вопрос",
                    "source": fact.source,
                    "kind": fact.kind,
                }
            )
        if actions:
            for action in actions:
                lines.append(f"- {action['action']} Приоритет: {action['priority']}.")
        else:
            lines.append("- Действий для отправки нет.")

        manager_reports[manager] = {
            "manager_name": manager,
            "subordinates": subs,
            "active_subordinates": active_people,
            "subordinate_open_count": len(subordinate_facts),
            "own_open_count": len(own_facts),
            "actions": actions,
            "sendable_text": "\n".join(lines),
        }
        manager_recommendations.append(
            {
                "manager_name": manager,
                "department": None,
                "actions": actions,
            }
        )
    return manager_reports, manager_recommendations


def build_owner_report(
    start: date,
    end: date,
    reports: list[dict[str, Any]],
    facts_by_person: dict[str, list[PersonFact]],
    totals: dict[str, Any],
    manager_recommendations: list[dict[str, Any]],
) -> dict[str, Any]:
    open_by_person = {person: dedupe_facts(facts) for person, facts in facts_by_person.items()}
    all_facts = [(person, fact) for person, facts in open_by_person.items() for fact in facts]
    all_facts.sort(key=lambda pf: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(pf[1].priority, 2), pf[0]))
    high = [(p, f) for p, f in all_facts if f.priority in {"critical", "high"}]
    no_response = [(p, f) for p, f in all_facts if (f.status or "") in {"no_info", "unanswered", "unclear"}]

    summary = (
        f"За период {start.isoformat()} - {end.isoformat()} обработано текущих дневных отчетов: {len(reports)}, "
        f"сообщений: {totals['messages']}, файлов: {totals['files']}, OCR-файлов: {totals['ocr_files']}. "
        f"Открытых пунктов после дедупликации: {len(all_facts)}, из них высокий/критический приоритет: {len(high)}. "
        f"Дни без новых сообщений: {', '.join(totals['no_data_days']) or 'нет'}."
    )
    risks_summary_lines = []
    for person, fact in high[:12]:
        risks_summary_lines.append(f"- {person}: {fact.text}; статус {fact.status or '-'}; срок {fact.deadline or 'срок не указан'}; источник {fact.source}.")
    for risk in totals["critical_risks"][:8]:
        risks_summary_lines.append(f"- {risk}")
    risks_summary = "\n".join(risks_summary_lines) or "- Критических рисков по текущим отчетам не выделено."

    owner_actions = [
        "Проверить у Артура Степаняна матрицу эскалации, мотивацию сотрудников и статус первой отгрузки Laretto.",
        "Попросить владельца юридического/административного контура назвать ответственного за кадровое делопроизводство и срок переподписания документов.",
        "Добиться статуса по себестоимости Laretto: собраны ли накладные расходы Дмитрием и внесены ли они Анастасией.",
        "Проверить компенсацию 36 491 руб. на карту склада и схему оплаты Бахтиёра.",
    ]
    report_text_lines = [
        "Сводка для собственника",
        f"Период: {start.isoformat()} - {end.isoformat()}",
        "",
        "1. Короткий итог",
        summary,
        "",
        "2. Критические висящие вопросы",
    ]
    if high:
        for person, fact in high[:15]:
            report_text_lines.append(f"- {person}: {fact.text}; статус: {fact.status or '-'}; срок: {fact.deadline or 'срок не указан'}; источник: {fact.source}.")
    else:
        report_text_lines.append("- Нет пунктов высокого/критического приоритета.")
    report_text_lines.extend(["", "3. Без реакции / слабая интенсивность"])
    if no_response:
        for person, fact in no_response[:15]:
            report_text_lines.append(f"- {person}: нет подтвержденного движения по «{fact.text}»; последний статус: {fact.status}; дата: {fact.date}.")
    else:
        report_text_lines.append("- Системного отсутствия реакции по текущему периоду не выделено.")
    report_text_lines.extend(["", "4. Что сделать собственнику"])
    for item in owner_actions:
        report_text_lines.append(f"- {item}")
    report_text_lines.extend(["", "5. Адресные рекомендации руководителям"])
    for mgr in manager_recommendations:
        actions = mgr.get("actions") or []
        if not actions:
            continue
        report_text_lines.append(f"- {mgr['manager_name']}: {len(actions)} действий на контроль.")

    return {
        "summary": summary,
        "dynamics_summary": "Сравнение с прошлым owner-отчетом не выполнялось локальным сборщиком; текущая динамика считается по повторяющимся no_info/пустым дням.",
        "risks_summary": risks_summary,
        "recommendations": owner_actions,
        "manager_recommendations": manager_recommendations,
        "hanging_tasks_by_owner": [
            f"{person}: {fact.text}; статус {fact.status or '-'}; срок {fact.deadline or 'срок не указан'}; источник {fact.source}"
            for person, fact in all_facts[:60]
        ],
        "no_response": [
            f"{person}: {fact.text}; дата {fact.date}; источник {fact.source}"
            for person, fact in no_response[:40]
        ],
        "critical_items": [
            f"{person}: {fact.text}; срок {fact.deadline or 'срок не указан'}"
            for person, fact in high[:40]
        ],
        "report_text": "\n".join(report_text_lines),
    }


def save_owner_weekly(start: date, end: date, analysis: dict[str, Any], raw_input: dict[str, Any]) -> str:
    with app.pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE owner_weekly_reports SET is_current = FALSE WHERE period_start = %s AND period_end = %s AND is_current = TRUE",
                    (start, end),
                )
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM owner_weekly_reports WHERE period_start = %s AND period_end = %s",
                    (start, end),
                )
                version = int(cur.fetchone()["version"])
                cur.execute(
                    """
                    INSERT INTO owner_weekly_reports (
                        period_start, period_end, version, is_current, generated_at,
                        summary, dynamics_summary, risks_summary, recommendations, report_text, raw_json
                    ) VALUES (%s, %s, %s, TRUE, now(), %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        start,
                        end,
                        version,
                        analysis["summary"],
                        analysis["dynamics_summary"],
                        analysis["risks_summary"],
                        "\n".join(f"- {item}" for item in analysis["recommendations"]),
                        analysis["report_text"],
                        app.pg_json({"model": MODEL, "input": raw_input, "analysis": analysis}),
                    ),
                )
                report_id = cur.fetchone()["id"]
    return str(report_id)


def save_recommendation_rows(report_id: str, start: date, end: date, manager_reports: dict[str, dict[str, Any]], users_by_name: dict[str, dict[str, Any]]) -> int:
    count = 0
    with app.pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM owner_manager_recommendations WHERE owner_weekly_report_id = %s", (report_id,))
                for manager, payload in manager_reports.items():
                    manager_user = users_by_name.get(manager) or users_by_name.get("Евгений Палей")
                    if not manager_user:
                        continue
                    for action in payload.get("actions") or []:
                        employee = users_by_name.get(clean_person_name(action.get("person")) or "")
                        cur.execute(
                            """
                            INSERT INTO owner_manager_recommendations (
                                source_scope, owner_weekly_report_id, period_start, period_end,
                                manager_user_id, manager_bitrix_user_id,
                                employee_user_id, employee_bitrix_user_id,
                                recommendation_type, priority, subject, recommendation_text,
                                due_date, source_payload, status
                            ) VALUES ('owner_weekly', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'new')
                            """,
                            (
                                report_id,
                                start,
                                end,
                                manager_user["id"],
                                manager_user["bitrix_user_id"],
                                employee["id"] if employee else None,
                                employee["bitrix_user_id"] if employee else None,
                                "risk" if action.get("kind") == "risk" else "followup",
                                action.get("priority") or "medium",
                                action.get("subject"),
                                action.get("action"),
                                action.get("due") if re.match(r"^\d{4}-\d{2}-\d{2}$", str(action.get("due") or "")) else None,
                                app.pg_json(action),
                            ),
                        )
                        count += 1
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-05-14")
    parser.add_argument("--end", default="2026-05-18")
    parser.add_argument("--out-dir", default="exports/owner_manager_digests")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    users_by_name, subs_by_manager = load_users()
    reports = load_current_chat_reports(start, end)
    facts_by_person, totals = collect_facts(reports, end)
    manager_reports, manager_recommendations = build_manager_reports(facts_by_person, users_by_name, subs_by_manager, start, end)
    owner_analysis = build_owner_report(start, end, reports, facts_by_person, totals, manager_recommendations)
    raw_input = {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "reports_count": len(reports),
        "source": "current chat_daily_reports.raw_ai_json.analysis + org structure",
        "external_ai_used": False,
    }
    owner_analysis["manager_sendable_reports"] = manager_reports
    report_id = save_owner_weekly(start, end, owner_analysis, raw_input)
    rec_count = save_recommendation_rows(report_id, start, end, manager_reports, users_by_name)

    (out_dir / f"owner_summary_{start}_{end}.txt").write_text(owner_analysis["report_text"], encoding="utf-8")
    for manager, payload in manager_reports.items():
        safe = re.sub(r"[^A-Za-zА-Яа-я0-9_.-]+", "_", manager).strip("_")
        (out_dir / f"manager_{safe}_{start}_{end}.txt").write_text(payload["sendable_text"], encoding="utf-8")
    print(f"owner_weekly_report_id={report_id}")
    print(f"manager_recommendations={rec_count}")
    print(f"files_dir={out_dir}")


if __name__ == "__main__":
    main()
