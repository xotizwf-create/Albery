from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


DIALOG_ID = "chat54871"
REPORT_DATE = date(2026, 4, 27)


def main() -> None:
    with app.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.raw_ai_json
                FROM chat_daily_reports r
                JOIN chats c ON c.id = r.chat_id
                WHERE c.dialog_id = %s
                  AND r.report_date = %s
                  AND r.is_current = TRUE
                """,
                (DIALOG_ID, REPORT_DATE),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Current report not found")
            raw = row["raw_ai_json"] or {}
            previous = raw.get("analysis") if isinstance(raw, dict) and isinstance(raw.get("analysis"), dict) else {}

    analysis = copy.deepcopy(previous)
    analysis["summary"] = ""
    analysis["quality_assessment"] = ""
    analysis["goals"] = [
        {
            "text": "Полностью подготовить и сдать в работу пакет документов и материалов для старта отдела продаж",
            "person_name": "Наталья Викторовна Горюнова",
            "goal_level": "employee",
            "scope": "employee",
            "assigned_at": "2026-04-27",
            "period_type": "week",
            "period_start": "2026-04-27",
            "period_end": "2026-04-30",
            "deadline": "2026-04-30",
            "success_metrics": "К 30.04.2026 подготовлены и переданы в работу материалы для старта отдела продаж: таблицы заказов, списки текущих задач, инструкции, документы и доступная структура хранения.",
            "expected_result": "Отдел продаж получает готовый пакет документов и материалов, достаточный для старта работы без дополнительного сбора базовой информации.",
            "confidence": 0.9,
            "evidence_message_ids": [2295107],
        },
        {
            "text": "Завершить приемку административного блока: доступы, реестр документов, актуализация доступов и управляемый архив",
            "person_name": "Наталья Викторовна Горюнова",
            "goal_level": "employee",
            "scope": "employee",
            "assigned_at": "2026-04-27",
            "period_type": "week",
            "period_start": "2026-04-27",
            "period_end": "2026-04-30",
            "deadline": "2026-04-30",
            "success_metrics": "К 30.04.2026 оформлены доступы, собран реестр документов, актуализированы доступы и организован управляемый архив.",
            "expected_result": "Административный блок принят и может использоваться как управляемая база документов, доступов и архивов.",
            "confidence": 0.9,
            "evidence_message_ids": [2295107],
        },
        {
            "text": "Завершить финализацию и проверку по отделу продаж и административному блоку",
            "person_name": "Наталья Викторовна Горюнова",
            "goal_level": "employee",
            "scope": "employee",
            "assigned_at": "2026-04-27",
            "period_type": "week",
            "period_start": "2026-04-27",
            "period_end": "2026-04-30",
            "deadline": "2026-04-30",
            "success_metrics": "Проверены и закрыты ключевые пункты по отделу продаж и административному блоку, выявленные недочеты зафиксированы и переданы ответственным.",
            "expected_result": "К 30.04.2026 зона отдела продаж и административный блок готовы к дальнейшей работе без критичных незакрытых пунктов.",
            "confidence": 0.88,
            "evidence_message_ids": [2295107],
        },
        {
            "text": "Достичь прибыли 45 000 000",
            "person_name": "Компания",
            "goal_level": "company",
            "scope": "company",
            "assigned_at": "2026-04-27",
            "period_type": "year",
            "period_start": "2026-04-27",
            "period_end": "2027-12-31",
            "deadline": "2027-12-31",
            "success_metrics": "Прибыль компании достигла 45 000 000 к 31.12.2027.",
            "expected_result": "Компания выходит на целевой финансовый результат 45 000 000 прибыли.",
            "confidence": 0.95,
            "evidence_message_ids": [2295125],
        },
        {
            "text": "Обеспечить ROI не менее 100% годовых за счет контроля unit-экономики, оборачиваемости капитала и устойчивой операционной системы",
            "person_name": "Компания",
            "goal_level": "company",
            "scope": "company",
            "assigned_at": "2026-04-27",
            "period_type": "year",
            "period_start": "2026-04-27",
            "period_end": "2026-12-31",
            "deadline": "2026-12-31",
            "success_metrics": "ROI не менее 100% годовых, контроль unit-экономики, оборачиваемости капитала и устойчивой операционной системы; отклонения по ключевым показателям не более 10%.",
            "expected_result": "Финансовая модель компании управляется через измеримые показатели ROI, unit-экономики и оборачиваемости капитала.",
            "confidence": 0.95,
            "evidence_message_ids": [2295125],
        },
        {
            "text": "Собрать и запустить базовую управляемую систему Albery 2.0",
            "person_name": "Артур Игоревич Степанян",
            "goal_level": "project",
            "scope": "project",
            "assigned_at": "2026-04-27",
            "period_type": "project",
            "period_start": "2026-04-27",
            "period_end": "2026-04-30",
            "deadline": "2026-04-30",
            "success_metrics": "К 30.04.2026 собрана и запущена базовая система Albery 2.0: оцифрованы 100% ключевых процессов, заданы зоны ответственности, базовые регламенты и контрольные метрики.",
            "expected_result": "Albery 2.0 работает как базовая управляемая система с понятными процессами, ответственными, метриками и регулярным управлением.",
            "confidence": 0.9,
            "evidence_message_ids": [2295125],
        },
        {
            "text": "Завершить полный контур передачи дел и обеспечить автономное функционирование системы Albery 2.0",
            "person_name": "Артур Игоревич Степанян",
            "goal_level": "project",
            "scope": "project",
            "assigned_at": "2026-04-27",
            "period_type": "project",
            "period_start": "2026-04-27",
            "period_end": "2026-04-30",
            "deadline": "2026-04-30",
            "success_metrics": "Переданы ключевые дела, документы, доступы, ответственные зоны и управленческие метрики; система может функционировать без ручного восстановления контекста.",
            "expected_result": "Команда получает автономно работающий контур Albery 2.0 с управляемой передачей дел и закрепленными ответственными.",
            "confidence": 0.9,
            "evidence_message_ids": [2295125],
        },
    ]

    for key in app.CHAT_REPORT_ITEM_KEYS:
        analysis.setdefault(key, [])

    with app.pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM user_goals
                    WHERE source_type = 'chat'
                      AND goal_title LIKE '%%?%%'
                      AND created_at::date >= CURRENT_DATE - INTERVAL '1 day'
                    """
                )
                cur.execute(
                    """
                    DELETE FROM user_goals
                    WHERE source_type = 'chat'
                      AND goal_level = 'employee'
                      AND goal_title = ANY(%s)
                    """,
                    (
                        [
                            "Собрать и запустить базовую управляемую систему Albery 2.0",
                            "Завершить полный контур передачи дел и обеспечить автономное функционирование системы Albery 2.0",
                        ],
                    ),
                )

    report_text = app.structured_chat_report_text(analysis)
    app.save_chat_daily_report(
        DIALOG_ID,
        REPORT_DATE,
        report_text,
        "local-manual-goal-extraction",
        {
            "source": "local_manual_goal_extraction",
            "model": "local-manual-goal-extraction",
            "analysis": analysis,
            "note": "Generated without OpenAI API from stored chat messages, OCR text, and existing structured report.",
        },
    )

    with app.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.version, r.is_current, r.extracted_goals_count
                FROM chat_daily_reports r
                JOIN chats c ON c.id = r.chat_id
                WHERE c.dialog_id = %s AND r.report_date = %s
                ORDER BY r.version DESC
                LIMIT 1
                """,
                (DIALOG_ID, REPORT_DATE),
            )
            report = cur.fetchone()
            print(f"report_id={report['id']} version={report['version']} current={report['is_current']} goals={report['extracted_goals_count']}")
            cur.execute(
                """
                SELECT g.goal_title, g.goal_level, g.period_type, g.period_start, g.period_end,
                       u.full_name AS user_name, m.full_name AS manager_name,
                       g.success_metrics, g.expected_result, g.confidence
                FROM user_goals g
                LEFT JOIN users u ON u.id = g.user_id
                LEFT JOIN users m ON m.id = g.manager_id
                WHERE g.source_type = 'chat'
                  AND g.goal_title = ANY(%s)
                ORDER BY g.goal_level, g.period_end, g.goal_title
                """,
                ([goal["text"] for goal in analysis["goals"]],),
            )
            for goal in cur.fetchall():
                print(
                    f"{goal['goal_level']} | {goal['goal_title']} | "
                    f"{goal['period_start']}..{goal['period_end']} | "
                    f"user={goal['user_name'] or '-'} | manager={goal['manager_name'] or '-'}"
                )


if __name__ == "__main__":
    main()
