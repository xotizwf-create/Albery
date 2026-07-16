"""Контроль CRM-воронок Albery — детерминированная проверка (без хода LLM: точно, дёшево, надёжно).

Режимы:
  daily   — пройти по контролируемым воронкам, найти проблемы, при наличии → алерт владельцу.
  weekly  — напоминание владельцам актуализировать воронки перед встречей руководителей (среда).
  --dry-run добавляется к режиму: печатает, что отправил бы, НО НЕ шлёт сообщения.

Владельцы (задача владельца 16.07): Закупка(C2)+Пополнение(C4) → Артур (28); Рекламации(C6) → Наталья (30).
Тестовая воронка закупки (C14) и служебная «Общая» (C0) не контролируются.
"""
import os
import sys
import logging
from datetime import datetime

sys.path.insert(0, "/var/www/albery")
from dotenv import load_dotenv
load_dotenv("/var/www/albery/.env")

from mcp import context_server as cs
import b24bot as b
from app import msk_now

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# funnel_id -> {name, owner_id, owner_name, checks}
FUNNELS = {
    2: {"name": "Закупка товара на WB", "owner_id": 28, "owner": "Артур",
        "checks": {"empty_card", "payment", "next_task", "overdue"}},
    4: {"name": "Пополнение МП/склад", "owner_id": 28, "owner": "Артур",
        "checks": {"next_task", "overdue"}},
    6: {"name": "Рекламации/брак", "owner_id": 30, "owner": "Наталья",
        "checks": {"next_task", "overdue"}},
}
PORTAL = "https://b24-0xrp3s.bitrix24.ru"

F_PAID = "UF_CRM_1783670137991"       # Оплата произведена (enum: 60=да, 58=нет)
F_SUM = "UF_CRM_1783669649285"        # Сумма заказа (текст)
F_SUPPLIER = "UF_CRM_1783611990212"   # Поставщик
F_PRODUCT = "UF_CRM_1783612114247"    # Товар
# плановая дата -> (поле факта или None, человекочитаемое имя)
PLAN_DATES = {
    "UF_CRM_WB_PROD_READY_PLAN": (None, "план готовности пр-ва"),
    "UF_CRM_WB_PLAN_SHIP_DATE": ("UF_CRM_WB_ACTUAL_SHIP_DATE", "плановая отгрузка"),
    "UF_CRM_1783671254915": ("UF_CRM_1783671293981", "план приёмки в КРД"),
}
SELECT = (["ID", "TITLE", "STAGE_ID", F_PAID, F_SUM, F_SUPPLIER, F_PRODUCT]
          + list(PLAN_DATES) + [a for a, _ in PLAN_DATES.values() if a])


def _has_next_task(deal_id) -> bool:
    acts = cs._crm_call("crm.activity.list", {
        "filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": int(deal_id), "COMPLETED": "N"},
        "select": ["ID"],
    }).get("result") or []
    return bool(acts)


def _parse_date(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except Exception:  # noqa: BLE001
        return None


def check_funnel(cid: int):
    cfg = FUNNELS[cid]
    checks = cfg["checks"]
    stages = {s["stage_id"]: s for s in cs._crm_stages(cid)}
    deals = cs._crm_call("crm.deal.list", {
        "filter": {"CATEGORY_ID": cid}, "select": SELECT,
        "order": {"ID": "ASC"}, "start": -1,
    }).get("result") or []
    today = msk_now().date()
    found = []
    for d in deals:
        st = stages.get(d.get("STAGE_ID"), {})
        # Bitrix may return semantics as 'S'/'F'/'P' or full words ('success'/'failure'/'process')
        sem = (st.get("semantics") or "")[:1].upper()   # S=успех, F=провал, иначе в работе
        if sem == "F":                      # провалено — не контролируем
            continue
        is_new = int(st.get("sort") or 0) <= 10 or "NEW" in (d.get("STAGE_ID") or "")
        title = (d.get("TITLE") or "").strip()
        probs = []
        if "empty_card" in checks and not is_new and sem != "S":
            if not (d.get(F_SUPPLIER) or d.get(F_PRODUCT) or d.get(F_SUM)):
                probs.append("пустая карточка (нет поставщика/товара/суммы)")
        if "payment" in checks and sem == "S":
            if str(d.get(F_PAID) or "") != "60":   # 60 = «да»
                probs.append("этап «успешна», но оплата не подтверждена")
        if "next_task" in checks and sem != "S":
            if not _has_next_task(d["ID"]):
                probs.append("нет следующего дела/задачи")
        if "overdue" in checks and sem != "S":   # у закрытых «успешных» прошедшие планы — не проблема
            for planf, (actf, label) in PLAN_DATES.items():
                pv = d.get(planf)
                if pv and not (actf and d.get(actf)):
                    pd = _parse_date(pv)
                    if pd and pd < today:
                        probs.append(f"просрочено: {label} ({pd.isoformat()})")
        if probs:
            found.append({
                "id": d["ID"], "title": title or f"Сделка #{d['id']}",
                "stage": st.get("name"), "problems": probs,
                "url": f"{PORTAL}/crm/deal/details/{d['ID']}/",
            })
    return found


def build_owner_alerts():
    """owner_id -> (owner_name, {funnel_name: [deal problems]})"""
    by_owner = {}
    for cid, cfg in FUNNELS.items():
        found = check_funnel(cid)
        if not found:
            continue
        by_owner.setdefault(cfg["owner_id"], (cfg["owner"], {}))[1][cfg["name"]] = found
    return by_owner


def _fmt_alert(owner_name, funnels):
    n = sum(len(v) for v in funnels.values())
    lines = [f"[b]⚠️ Контроль воронок — {n} сделок требуют внимания[/b]"]
    for fname, deals in funnels.items():
        lines.append(f"\n[b]{fname}:[/b]")
        for d in deals:
            probs = "; ".join(d["problems"])
            lines.append(f"— [URL={d['url']}]{d['title']}[/URL] · {d['stage']}: {probs}")
    lines.append("\nПоправьте, пожалуйста, данные в этих сделках.")
    return "\n".join(lines)


def _fmt_weekly(owner_name, funnel_names):
    return (f"[b]🗓 Напоминание: завтра встреча руководителей[/b]\n"
            f"Актуализируйте, пожалуйста, свои воронки перед встречей: {', '.join(funnel_names)}.\n"
            f"Проверьте: все поля заполнены, завершённые сделки закрыты, у активных проставлен "
            f"следующий шаг (задача/дело), плановые даты не просрочены.")


def run_daily(dry=False):
    by_owner = build_owner_alerts()
    if not by_owner:
        logging.info("funnel-control daily: проблем не найдено, тишина")
        return
    for owner_id, (owner_name, funnels) in by_owner.items():
        msg = _fmt_alert(owner_name, funnels)
        if dry:
            print(f"\n===== АЛЕРТ → {owner_name} (id {owner_id}) =====\n{msg}")
        else:
            ok, err = b._albery_bitrix_notify(msg, dialog_id=str(owner_id))
            logging.info("funnel-control alert → %s (%s): ok=%s err=%s", owner_name, owner_id, ok, err)


def run_weekly(dry=False):
    # owner_id -> funnel names they own
    owners = {}
    for cfg in FUNNELS.values():
        owners.setdefault((cfg["owner_id"], cfg["owner"]), []).append(cfg["name"])
    for (owner_id, owner_name), fnames in owners.items():
        msg = _fmt_weekly(owner_name, fnames)
        if dry:
            print(f"\n===== ЕЖЕНЕДЕЛЬНОЕ → {owner_name} (id {owner_id}) =====\n{msg}")
        else:
            ok, err = b._albery_bitrix_notify(msg, dialog_id=str(owner_id))
            logging.info("funnel-control weekly → %s (%s): ok=%s err=%s", owner_name, owner_id, ok, err)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    dry = "--dry-run" in sys.argv
    if mode == "weekly":
        run_weekly(dry)
    else:
        run_daily(dry)
