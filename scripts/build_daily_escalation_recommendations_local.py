from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from build_owner_manager_digest_local import (
    MODEL as DIGEST_MODEL,
    PersonFact,
    clean_person_name,
    collect_facts,
    dedupe_facts,
    load_current_chat_reports,
    load_users,
    manager_for_person,
)


MODEL = "local_daily_escalation_layer4_no_external_ai_v1"


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def priority_rank(priority: str | None) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(priority or "medium"), 2)


def trigger_personal(person: str, facts: list[PersonFact], target: date) -> list[dict[str, Any]]:
    """Type A: only hard triggers; max 3 items, no daily digest."""
    signals: list[dict[str, Any]] = []
    if person in {"Требует назначения", "Все руководители"}:
        return signals
    for fact in dedupe_facts(facts):
        deadline = parse_date(fact.deadline)
        days_to_deadline = (deadline - target).days if deadline else None
        days_silent = max(0, (target - parse_date(fact.date)).days) if parse_date(fact.date) else 0
        status = (fact.status or "").lower()
        reason = None
        if deadline and days_to_deadline is not None and days_to_deadline <= 2 and status in {"no_info", "assigned", "planned", "unanswered", "unclear"}:
            reason = f"дедлайн {deadline.isoformat()} близко или уже прошел, подтвержденного движения нет"
        if deadline and days_to_deadline is not None and days_to_deadline < 0 and status not in {"done", "cancelled"}:
            reason = f"просрочка {abs(days_to_deadline)} дн., переноса/закрытия нет"
        if fact.kind == "unanswered_question" and status in {"unanswered", "unclear"} and days_silent >= 2:
            reason = f"вопрос висит без закрывающего ответа {days_silent} дн."
        if reason:
            signals.append(
                {
                    "recipient": person,
                    "type": "personal",
                    "priority": "high" if deadline and days_to_deadline is not None and days_to_deadline < 0 else fact.priority,
                    "subject": fact.text,
                    "reason": reason,
                    "due": fact.deadline or "срок не указан",
                    "source": fact.source,
                    "sendable_text": (
                        f"{person}, по пункту «{fact.text}» нужен статус.\n"
                        f"Причина: {reason}.\n"
                        f"Ожидаемый ответ: текущий статус, новый срок если есть перенос, или подтверждение закрытия."
                    ),
                }
            )
    signals.sort(key=lambda x: priority_rank(x.get("priority")))
    return signals[:3]


def trigger_manager(
    manager: str,
    users_by_name: dict[str, dict[str, Any]],
    facts_by_person: dict[str, list[PersonFact]],
    target: date,
) -> dict[str, Any] | None:
    """Type B: signals about subordinates + manager's own hard signals."""
    subordinate_signals: list[dict[str, Any]] = []
    own_signals = trigger_personal(manager, facts_by_person.get(manager, []), target)
    for person, facts in facts_by_person.items():
        if person == manager:
            continue
        if manager_for_person(person, users_by_name) != manager:
            continue
        for signal in trigger_personal(person, facts, target):
            signal["manager"] = manager
            subordinate_signals.append(signal)
    subordinate_signals.sort(key=lambda x: priority_rank(x.get("priority")))
    if not subordinate_signals and not own_signals:
        return None
    lines = [
        f"Адресный отчет руководителю: {manager}",
        f"Дата: {target.isoformat()}",
        "",
        "1. Сигналы по подчиненным",
    ]
    if subordinate_signals:
        for sig in subordinate_signals[:8]:
            lines.append(f"- {sig['recipient']}: {sig['subject']}. Причина: {sig['reason']}. Срок: {sig['due']}.")
    else:
        lines.append("- Сигналов по подчиненным нет.")
    lines.extend(["", "2. Ваши собственные сигналы"])
    if own_signals:
        for sig in own_signals[:3]:
            lines.append(f"- {sig['subject']}. Причина: {sig['reason']}. Срок: {sig['due']}.")
    else:
        lines.append("- Собственных сигналов нет.")
    return {
        "manager_name": manager,
        "type": "manager",
        "subordinate_signals": subordinate_signals[:8],
        "own_signals": own_signals[:3],
        "sendable_text": "\n".join(lines),
    }


def trigger_owner(
    facts_by_person: dict[str, list[PersonFact]],
    manager_reports: list[dict[str, Any]],
    target: date,
    lookback_days: int,
) -> dict[str, Any] | None:
    """Type C: strict critical owner signals only."""
    critical: list[str] = []
    no_response_by_person: dict[str, int] = defaultdict(int)
    high_by_manager: dict[str, int] = defaultdict(int)
    for person, facts in facts_by_person.items():
        deduped = dedupe_facts(facts)
        for fact in deduped:
            deadline = parse_date(fact.deadline)
            overdue_days = (target - deadline).days if deadline else 0
            status = (fact.status or "").lower()
            if deadline and overdue_days >= 5 and status not in {"done", "cancelled"}:
                critical.append(f"{person}: «{fact.text}» просрочено на {overdue_days} дн., реакции/переноса нет.")
            if status in {"no_info", "unanswered", "unclear"}:
                no_response_by_person[person] += 1
            if fact.priority in {"high", "critical"}:
                mgr = person if person in {m.get("manager_name") for m in manager_reports} else None
                if mgr:
                    high_by_manager[mgr] += 1
    for person, count in sorted(no_response_by_person.items(), key=lambda x: -x[1]):
        if count >= 5:
            critical.append(f"{person}: {count} пунктов без подтвержденной реакции за {lookback_days} дн.; нужен разбор причины.")
    for report in manager_reports:
        manager = report["manager_name"]
        signal_count = len(report.get("subordinate_signals") or []) + len(report.get("own_signals") or [])
        if signal_count >= 5:
            critical.append(f"{manager}: {signal_count} эскалационных сигналов в зоне руководителя на дату {target.isoformat()}.")
    critical = list(dict.fromkeys(critical))
    if not critical:
        return None
    lines = [
        "Сводка собственнику по критическим эскалациям",
        f"Дата: {target.isoformat()}",
        "",
        "1. Главные сигналы",
    ]
    for item in critical[:8]:
        lines.append(f"- {item}")
    lines.extend(["", "2. Что требуется от собственника"])
    lines.append("- Назначить владельцев по пунктам без ответственного и потребовать статус по просроченным вопросам.")
    lines.append("- Не разбирать операционные задачи вручную, а проверить, почему не сработал руководитель/контур контроля.")
    return {
        "type": "owner",
        "signals": critical[:12],
        "sendable_text": "\n".join(lines),
    }


def save_owner_daily(target: date, layer4: dict[str, Any], raw_input: dict[str, Any]) -> str:
    summary = (
        f"Layer 4 эскалация за {target.isoformat()}: "
        f"персональных сигналов {len(layer4['personal_recommendations'])}, "
        f"отчетов руководителям {len(layer4['manager_reports'])}, "
        f"сигнал собственнику: {'да' if layer4.get('owner_summary') else 'нет'}."
    )
    report_text_parts = [summary]
    if layer4.get("owner_summary"):
        report_text_parts.extend(["", layer4["owner_summary"]["sendable_text"]])
    with app.pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("UPDATE owner_daily_reports SET is_current = FALSE WHERE report_date = %s AND is_current = TRUE", (target,))
                cur.execute("SELECT COALESCE(MAX(version), 0) + 1 AS version FROM owner_daily_reports WHERE report_date = %s", (target,))
                version = int(cur.fetchone()["version"])
                cur.execute(
                    """
                    INSERT INTO owner_daily_reports (
                        report_date, version, is_current, generated_at,
                        summary, dynamics_summary, risks_summary, recommendations, report_text, raw_json
                    ) VALUES (%s, %s, TRUE, now(), %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        target,
                        version,
                        summary,
                        "Ежедневный отчет построен только по триггерам Layer 4, не как общая сводка активности.",
                        "\n".join(layer4.get("owner_summary", {}).get("signals", [])) if layer4.get("owner_summary") else "",
                        "\n".join(sig["sendable_text"] for sig in layer4["personal_recommendations"][:10]),
                        "\n".join(report_text_parts),
                        app.pg_json({"model": MODEL, "input": raw_input, "layer4": layer4}),
                    ),
                )
                return str(cur.fetchone()["id"])


def save_manager_recommendations(report_id: str, target: date, manager_reports: list[dict[str, Any]], users_by_name: dict[str, dict[str, Any]]) -> int:
    count = 0
    with app.pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM owner_manager_recommendations WHERE owner_daily_report_id = %s", (report_id,))
                for report in manager_reports:
                    manager = users_by_name.get(report["manager_name"])
                    if not manager:
                        continue
                    for sig in (report.get("subordinate_signals") or []) + (report.get("own_signals") or []):
                        employee = users_by_name.get(clean_person_name(sig.get("recipient")) or "")
                        cur.execute(
                            """
                            INSERT INTO owner_manager_recommendations (
                                source_scope, owner_daily_report_id, report_date,
                                manager_user_id, manager_bitrix_user_id,
                                employee_user_id, employee_bitrix_user_id,
                                recommendation_type, priority, subject, recommendation_text,
                                due_date, source_payload, status
                            ) VALUES ('owner_daily', %s, %s, %s, %s, %s, %s, 'followup', %s, %s, %s, %s, %s, 'new')
                            """,
                            (
                                report_id,
                                target,
                                manager["id"],
                                manager["bitrix_user_id"],
                                employee["id"] if employee else None,
                                employee["bitrix_user_id"] if employee else None,
                                sig.get("priority") or "medium",
                                sig.get("subject"),
                                sig.get("sendable_text"),
                                sig.get("due") if re.match(r"^\d{4}-\d{2}-\d{2}$", str(sig.get("due") or "")) else None,
                                app.pg_json(sig),
                            ),
                        )
                        count += 1
    return count


def build(target: date, lookback_days: int) -> tuple[dict[str, Any], dict[str, Any]]:
    start = target - timedelta(days=lookback_days - 1)
    users_by_name, _ = load_users()
    reports = load_current_chat_reports(start, target)
    facts_by_person, totals = collect_facts(reports, target)

    personal: list[dict[str, Any]] = []
    for person, facts in facts_by_person.items():
        personal.extend(trigger_personal(person, facts, target))
    personal.sort(key=lambda x: priority_rank(x.get("priority")))

    managers = sorted({manager_for_person(person, users_by_name) for person in facts_by_person if manager_for_person(person, users_by_name)})
    manager_reports = [r for manager in managers if (r := trigger_manager(manager, users_by_name, facts_by_person, target))]
    owner_summary = trigger_owner(facts_by_person, manager_reports, target, lookback_days)

    layer4 = {
        "target_date": target.isoformat(),
        "lookback_start": start.isoformat(),
        "lookback_days": lookback_days,
        "personal_recommendations": personal,
        "manager_reports": manager_reports,
        "owner_summary": owner_summary,
        "source_stats": totals,
    }
    raw_input = {
        "target_date": target.isoformat(),
        "lookback_start": start.isoformat(),
        "reports_count": len(reports),
        "source": "current chat_daily_reports.raw_ai_json.analysis + org structure",
        "external_ai_used": False,
        "base_digest_model": DIGEST_MODEL,
    }
    return layer4, raw_input


def write_files(layer4: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = layer4["target_date"]
    for sig in layer4["personal_recommendations"]:
        safe = re.sub(r"[^A-Za-zА-Яа-я0-9_.-]+", "_", sig["recipient"]).strip("_")
        path = out_dir / f"personal_{safe}_{target}.txt"
        existing = path.read_text(encoding="utf-8") + "\n\n" if path.exists() else ""
        path.write_text(existing + sig["sendable_text"], encoding="utf-8")
    for report in layer4["manager_reports"]:
        safe = re.sub(r"[^A-Za-zА-Яа-я0-9_.-]+", "_", report["manager_name"]).strip("_")
        (out_dir / f"manager_{safe}_{target}.txt").write_text(report["sendable_text"], encoding="utf-8")
    if layer4.get("owner_summary"):
        (out_dir / f"owner_critical_{target}.txt").write_text(layer4["owner_summary"]["sendable_text"], encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--out-dir", default="exports/daily_escalations")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target = date.fromisoformat(args.date)
    layer4, raw_input = build(target, args.lookback_days)
    report_id = save_owner_daily(target, layer4, raw_input)
    users_by_name, _ = load_users()
    rec_count = save_manager_recommendations(report_id, target, layer4["manager_reports"], users_by_name)
    out_dir = Path(args.out_dir) / target.isoformat()
    write_files(layer4, out_dir)
    print(f"owner_daily_report_id={report_id}")
    print(f"personal_recommendations={len(layer4['personal_recommendations'])}")
    print(f"manager_reports={len(layer4['manager_reports'])}")
    print(f"manager_recommendation_rows={rec_count}")
    print(f"owner_critical={'yes' if layer4.get('owner_summary') else 'no'}")
    print(f"files_dir={out_dir}")


if __name__ == "__main__":
    main()
