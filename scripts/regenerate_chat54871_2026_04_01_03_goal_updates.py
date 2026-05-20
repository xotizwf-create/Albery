from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


DIALOG_ID = "chat54871"

SALE_GOAL_TITLE = (
    "Поддерживать план распродажи остатков через ежедневное регулирование цен/скидок, "
    "контроль платного хранения и контроль Ларетто"
)
FIN_GOAL_TITLE = "Завершить аудит финансового и бухгалтерского контура и подготовить план внедрения нового контура учета"


def current_analysis(report_date: date) -> dict:
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
                (DIALOG_ID, report_date),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"Current report not found for {report_date}")
            raw = row["raw_ai_json"] or {}
            analysis = raw.get("analysis") if isinstance(raw, dict) and isinstance(raw.get("analysis"), dict) else {}
            return copy.deepcopy(analysis)


def reset_test_goals() -> None:
    with app.pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM user_goals
                    WHERE source_type = 'chat'
                      AND goal_title = ANY(%s)
                    """,
                    ([SALE_GOAL_TITLE, FIN_GOAL_TITLE],),
                )


def save_local_report(report_date: date, analysis: dict) -> None:
    for key in app.CHAT_REPORT_ITEM_KEYS:
        analysis.setdefault(key, [])
    analysis.setdefault("goal_updates", [])
    report_text = app.structured_chat_report_text(analysis)
    app.save_chat_daily_report(
        DIALOG_ID,
        report_date,
        report_text,
        "local-goal-update-test",
        {
            "source": "local_goal_update_test",
            "model": "local-goal-update-test",
            "analysis": analysis,
            "note": "Generated without OpenAI API to test automatic goal creation and goal_updates.",
        },
    )


def build_2026_04_01() -> dict:
    analysis = current_analysis(date(2026, 4, 1))
    analysis["goals"] = [
        {
            "text": SALE_GOAL_TITLE,
            "person_name": "Наталья Викторовна Горюнова",
            "goal_level": "employee",
            "scope": "employee",
            "assigned_at": "2026-04-01",
            "period_type": "week",
            "period_start": "2026-04-01",
            "period_end": "2026-04-06",
            "deadline": "2026-04-06",
            "success_metrics": (
                "Ежедневно выполнены регулирование цен/скидок под план распродажи, "
                "контроль платного хранения и контроль Ларетто; риски и переносы фиксируются в отчете."
            ),
            "expected_result": "План распродажи остатков управляется ежедневно, без потери контроля по ценам, хранению и Ларетто.",
            "confidence": 0.92,
            "evidence_message_ids": [2268947],
        },
        {
            "text": FIN_GOAL_TITLE,
            "person_name": "Артур Игоревич Степанян",
            "goal_level": "employee",
            "scope": "employee",
            "assigned_at": "2026-04-01",
            "period_type": "week",
            "period_start": "2026-04-01",
            "period_end": "2026-04-03",
            "deadline": "2026-04-03",
            "success_metrics": "Аудит финансового и бухгалтерского контура закрыт, подготовлен план внедрения нового контура учета.",
            "expected_result": "Есть понятная картина текущего фин/бух контура и план перехода на новый контур учета.",
            "confidence": 0.9,
            "evidence_message_ids": [2268041],
        },
    ]
    analysis["goal_updates"] = [
        {
            "goal_title": SALE_GOAL_TITLE,
            "person_name": "Наталья Викторовна Горюнова",
            "goal_level": "employee",
            "status_after": "active",
            "progress_text": "01.04 выполнены регулирование цен/скидок, контроль платного хранения и контроль Ларетто; план распродажи на неделю пересобран.",
            "progress_percent": 25,
            "metric_value": "3 из 3 ежедневных контрольных блоков выполнены за 01.04",
            "risk_level": "medium",
            "is_completed": False,
            "is_cancelled": False,
            "confidence": 0.92,
            "evidence_message_ids": [2268947],
        },
        {
            "goal_title": FIN_GOAL_TITLE,
            "person_name": "Артур Игоревич Степанян",
            "goal_level": "employee",
            "status_after": "active",
            "progress_text": "По аудиту фин/бух контура зафиксирован прогресс 70%, но завершение и план внедрения нового контура не подтверждены.",
            "progress_percent": 70,
            "metric_value": "аудит 70%",
            "risk_level": "medium",
            "is_completed": False,
            "is_cancelled": False,
            "confidence": 0.9,
            "evidence_message_ids": [2268041],
        },
    ]
    return analysis


def build_2026_04_02() -> dict:
    analysis = current_analysis(date(2026, 4, 2))
    analysis["goals"] = []
    analysis["goal_updates"] = [
        {
            "goal_title": SALE_GOAL_TITLE,
            "person_name": "Наталья Викторовна Горюнова",
            "goal_level": "employee",
            "status_after": "active",
            "progress_text": "02.04 продолжено управление распродажей: зафиксированы ежедневные задачи по регулированию цен/скидок, контролю хранения и Ларетто на 03.04.",
            "progress_percent": 50,
            "metric_value": "контур контроля перенесен в план 03.04",
            "risk_level": "medium",
            "is_completed": False,
            "is_cancelled": False,
            "confidence": 0.9,
            "evidence_message_ids": [2270711],
        },
        {
            "goal_title": FIN_GOAL_TITLE,
            "person_name": "Артур Игоревич Степанян",
            "goal_level": "employee",
            "status_after": "active",
            "progress_text": "02.04 есть движение по документам и карте процессов, но аудит фин/бух контура и план внедрения нового контура не закрыты.",
            "progress_percent": 75,
            "metric_value": "есть движение, закрытия нет",
            "risk_level": "medium",
            "is_completed": False,
            "is_cancelled": False,
            "confidence": 0.9,
            "evidence_message_ids": [2270713, 2270845, 2270849],
        },
    ]
    return analysis


def build_2026_04_03() -> dict:
    analysis = current_analysis(date(2026, 4, 3))
    analysis["goals"] = []
    analysis["goal_updates"] = [
        {
            "goal_title": SALE_GOAL_TITLE,
            "person_name": "Наталья Викторовна Горюнова",
            "goal_level": "employee",
            "status_after": "active",
            "progress_text": "03.04 Наталья снова выполнила ежедневное регулирование цен/скидок, контроль платного хранения и контроль Ларетто; задачи перенесены в план на 06.04.",
            "progress_percent": 75,
            "metric_value": "3 из 3 ежедневных контрольных блоков выполнены за 03.04",
            "risk_level": "medium",
            "is_completed": False,
            "is_cancelled": False,
            "confidence": 0.92,
            "evidence_message_ids": [2271923],
        },
        {
            "goal_title": FIN_GOAL_TITLE,
            "person_name": "Артур Игоревич Степанян",
            "goal_level": "employee",
            "status_after": "active",
            "progress_text": "03.04 Артур не предоставил факт за день и планы; хвосты по аудиту фин/бух контура и плану внедрения остаются без закрытия после срока.",
            "progress_percent": 75,
            "metric_value": "просрочено, закрытия нет",
            "risk_level": "high",
            "is_completed": False,
            "is_cancelled": False,
            "confidence": 0.95,
            "evidence_message_ids": [2271917, 2271921],
        },
    ]
    return analysis


def print_results() -> None:
    with app.pg_connect() as conn:
        with conn.cursor() as cur:
            print("REPORTS")
            cur.execute(
                """
                SELECT r.report_date, r.version, r.is_current, r.extracted_goals_count,
                       (SELECT COUNT(*) FROM goal_progress_events e WHERE e.chat_daily_report_id = r.id) AS goal_update_events
                FROM chat_daily_reports r
                JOIN chats c ON c.id = r.chat_id
                WHERE c.dialog_id = %s
                  AND r.report_date BETWEEN %s AND %s
                  AND r.is_current = TRUE
                ORDER BY r.report_date
                """,
                (DIALOG_ID, date(2026, 4, 1), date(2026, 4, 3)),
            )
            for row in cur.fetchall():
                print(
                    f"{row['report_date']} version={row['version']} "
                    f"goals={row['extracted_goals_count']} updates={row['goal_update_events']}"
                )

            print("\nGOALS")
            cur.execute(
                """
                SELECT g.id, g.goal_title, g.status, g.period_start, g.period_end, u.full_name AS owner_name
                FROM user_goals g
                LEFT JOIN users u ON u.id = g.user_id
                WHERE g.source_type = 'chat'
                  AND g.goal_title = ANY(%s)
                ORDER BY g.goal_title
                """,
                ([SALE_GOAL_TITLE, FIN_GOAL_TITLE],),
            )
            for row in cur.fetchall():
                print(f"{row['status']} | {row['goal_title']} | {row['period_start']}..{row['period_end']} | owner={row['owner_name']}")

            print("\nEVENTS")
            cur.execute(
                """
                SELECT e.report_date, g.goal_title, e.status_before, e.status_after,
                       e.progress_percent, e.risk_level, e.progress_text
                FROM goal_progress_events e
                JOIN user_goals g ON g.id = e.goal_id
                WHERE g.goal_title = ANY(%s)
                ORDER BY e.report_date, g.goal_title, e.created_at
                """,
                ([SALE_GOAL_TITLE, FIN_GOAL_TITLE],),
            )
            for row in cur.fetchall():
                print(
                    f"{row['report_date']} | {row['status_before']}->{row['status_after']} | "
                    f"{row['progress_percent']}% | {row['risk_level']} | {row['goal_title']} | {row['progress_text']}"
                )


def main() -> None:
    reset_test_goals()
    for report_date, builder in [
        (date(2026, 4, 1), build_2026_04_01),
        (date(2026, 4, 2), build_2026_04_02),
        (date(2026, 4, 3), build_2026_04_03),
    ]:
        save_local_report(report_date, builder())
    print_results()


if __name__ == "__main__":
    main()
