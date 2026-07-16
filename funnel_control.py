"""Контроль CRM-воронок Albery — детерминированно (без хода LLM: точно, дёшево, надёжно).

Две автоматизации (задача владельца 16.07):
  check   (Пн/Ср/Пт 09:00) — проверяет заполненность/консистентность/следующие шаги/сроки по всем
          контролируемым воронкам и ПИШЕТ СНИМОК СОСТОЯНИЯ в БД (funnel_control_snapshots).
          НИКОМУ не шлёт — только копит данные для сравнения «делали люди что-то или нет».
  summary (Ср 15:00) — по каждому владельцу воронки собирает КАРТИНУ (что оплачено/в пути/номера/
          сроки), сравнивает с прошлой проверкой (что изменилось), внизу — рекомендации, и создаёт
          НОВУЮ ЗАДАЧУ владельцу воронки (Артур — закупка+пополнение, Наталья — рекламации).

--dry-run: печатает, ничего не пишет в БД и не создаёт задач.
"""
import os
import sys
import json
import logging
from datetime import datetime

sys.path.insert(0, "/var/www/albery")
from dotenv import load_dotenv
load_dotenv("/var/www/albery/.env")

from mcp import context_server as cs
from app import msk_now
from attachments import connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

FUNNELS = {
    2: {"name": "Закупка товара на WB", "checks": {"empty_card", "payment", "next_task", "overdue"}},
    4: {"name": "Пополнение МП/склад", "checks": {"next_task", "overdue"}},
    6: {"name": "Рекламации/брак", "checks": {"next_task", "overdue"}},
}
# владелец воронки → его воронки (для сводки-задачи)
OWNERS = {
    28: {"name": "Артур", "funnels": [2, 4]},
    30: {"name": "Наталья", "funnels": [6]},
}
PORTAL = "https://b24-0xrp3s.bitrix24.ru"
CREATOR_ID = 22  # ИИ Агент
# На время тестов сводки-задачи ставятся на ИИ Агента (22), а не на реальных владельцев.
# Убрать (поставить None), когда переключаем в боевой режим на Артура/Наталью.
TEST_RESPONSIBLE = int(os.getenv("FUNNEL_SUMMARY_TEST_RESPONSIBLE", "22") or "0") or None

# Серьёзность проблемы (для статуса): crit — данные противоречат/пустые; warn — не хватает шага/срок.
_CRIT = ("оплата не подтверждена", "пустая карточка")
_WARN = ("нет следующего", "просрочено")

F_PAID = "UF_CRM_1783670137991"       # Оплата произведена (60=да, 58=нет)
F_SUM = "UF_CRM_1783669649285"        # Сумма заказа (текст)
F_SUPPLIER = "UF_CRM_1783611990212"
F_PRODUCT = "UF_CRM_1783612114247"
F_ORDER_NO = "UF_CRM_WB_ORDER_NO"     # № заказа WB
F_PROD_STAGE = "UF_CRM_WB_PROD_STAGE"
F_ARRIVAL = "UF_CRM_1783671293981"    # Дата прихода товара в КРД
PLAN_DATES = {
    "UF_CRM_WB_PROD_READY_PLAN": (None, "план готовности пр-ва"),
    "UF_CRM_WB_PLAN_SHIP_DATE": ("UF_CRM_WB_ACTUAL_SHIP_DATE", "плановая отгрузка"),
    "UF_CRM_1783671254915": (F_ARRIVAL, "план приёмки в КРД"),
}
SELECT = (["ID", "TITLE", "STAGE_ID", F_PAID, F_SUM, F_SUPPLIER, F_PRODUCT, F_ORDER_NO,
           F_PROD_STAGE, F_ARRIVAL]
          + list(PLAN_DATES) + [a for a, _ in PLAN_DATES.values() if a])

DDL = """
CREATE TABLE IF NOT EXISTS funnel_control_snapshots (
  id serial PRIMARY KEY,
  run_ts timestamptz NOT NULL,
  funnel_id int NOT NULL,
  deal_id int NOT NULL,
  title text, stage text, sem text, paid text,
  has_next boolean, filled boolean,
  problems text[] NOT NULL DEFAULT '{}',
  fingerprint text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fcs_funnel_run ON funnel_control_snapshots (funnel_id, run_ts DESC);
"""


def _ensure_table():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)


def _has_next_task(deal_id) -> bool:
    acts = cs._crm_call("crm.activity.list", {
        "filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": int(deal_id), "COMPLETED": "N"},
        "select": ["ID"]}).get("result") or []
    return bool(acts)


def _date(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except Exception:  # noqa: BLE001
        return None


def collect(funnel_id: int) -> list[dict]:
    """Текущее состояние каждой сделки воронки + вычисленные проблемы."""
    cfg = FUNNELS[funnel_id]
    checks = cfg["checks"]
    stages = {s["stage_id"]: s for s in cs._crm_stages(funnel_id)}
    deals = cs._crm_call("crm.deal.list", {
        "filter": {"CATEGORY_ID": funnel_id}, "select": SELECT,
        "order": {"ID": "ASC"}, "start": -1}).get("result") or []
    today = msk_now().date()
    out = []
    for d in deals:
        st = stages.get(d.get("STAGE_ID"), {})
        sem = (st.get("semantics") or "")[:1].upper()   # S успех / F провал / иначе в работе
        if sem == "F":
            continue
        is_new = int(st.get("sort") or 0) <= 10 or "NEW" in (d.get("STAGE_ID") or "")
        paid_raw = str(d.get(F_PAID) or "")
        paid = "да" if paid_raw == "60" else ("нет" if paid_raw == "58" else "")
        filled = bool(d.get(F_SUPPLIER) or d.get(F_PRODUCT) or d.get(F_SUM))
        has_next = _has_next_task(d["ID"])
        probs = []
        if "empty_card" in checks and not is_new and sem != "S" and not filled:
            probs.append("пустая карточка (нет поставщика/товара/суммы)")
        if "payment" in checks and sem == "S" and paid != "да":
            probs.append("этап «успешна», но оплата не подтверждена")
        if "next_task" in checks and sem != "S" and not has_next:
            probs.append("нет следующего дела/задачи")
        if "overdue" in checks and sem != "S":
            for pf, (af, label) in PLAN_DATES.items():
                pv, av = d.get(pf), (d.get(af) if af else None)
                pd = _date(pv) if pv and not av else None
                if pd and pd < today:
                    probs.append(f"просрочено: {label} ({pd.isoformat()})")
        out.append({
            "id": int(d["ID"]), "funnel": funnel_id,
            "title": (d.get("TITLE") or "").strip() or f"Сделка #{d['ID']}",
            "stage": st.get("name") or d.get("STAGE_ID"), "sem": sem, "paid": paid,
            "has_next": has_next, "filled": filled, "problems": probs,
            "order_no": (d.get(F_ORDER_NO) or "").strip(),
            "prod_stage": (d.get(F_PROD_STAGE) or "").strip(),
            "ship_plan": d.get("UF_CRM_WB_PLAN_SHIP_DATE") or d.get("UF_CRM_WB_ACTUAL_SHIP_DATE"),
            "arrival": d.get(F_ARRIVAL),
            "url": f"{PORTAL}/crm/deal/details/{d['ID']}/",
            "fingerprint": f"{st.get('name')}|{paid}|{has_next}|{filled}|{'/'.join(sorted(probs))}",
        })
    return out


# ---------- check (Пн/Ср/Пт): снимок в БД, тишина -------------------------------------------
def run_check(dry=False):
    _ensure_table()
    run_ts = msk_now()
    total = 0
    rows = []
    for fid in FUNNELS:
        for d in collect(fid):
            total += 1
            rows.append((run_ts, fid, d["id"], d["title"], d["stage"], d["sem"], d["paid"],
                         d["has_next"], d["filled"], d["problems"], d["fingerprint"]))
    if dry:
        probs = sum(1 for r in rows if r[9])
        print(f"[check dry] воронок {len(FUNNELS)}, сделок {total}, с проблемами {probs} — снимок НЕ пишется")
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO funnel_control_snapshots "
                "(run_ts, funnel_id, deal_id, title, stage, sem, paid, has_next, filled, problems, fingerprint) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    logging.info("funnel-control check: снимок %s сделок сохранён (run_ts=%s)", total, run_ts.isoformat())


def _baseline(funnel_id: int) -> dict:
    """Снимок предыдущей проверки (последний run_ts со ВЧЕРАШНЕЙ или ранее датой) → {deal_id: row}."""
    start = msk_now().replace(hour=0, minute=0, second=0, microsecond=0)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT max(run_ts) AS rt FROM funnel_control_snapshots "
                        "WHERE funnel_id=%s AND run_ts < %s", (funnel_id, start))
            row = cur.fetchone()
            rt = row and row["rt"]
            if not rt:
                return {}
            cur.execute("SELECT deal_id, stage, paid, has_next, problems, fingerprint "
                        "FROM funnel_control_snapshots WHERE funnel_id=%s AND run_ts=%s",
                        (funnel_id, rt))
            return {r["deal_id"]: dict(r) for r in cur.fetchall()}


# ---------- summary (Ср): картина + diff + рекомендации → задача владельцу --------------------
def _ref(d) -> str:
    return f"[URL={d['url']}]{d['title']}[/URL]"


def _short(d) -> str:
    return f"«{d['title']}»"


def _severity(deals: list[dict]):
    crit = [d for d in deals if any(any(k in p for k in _CRIT) for p in d["problems"])]
    warn = [d for d in deals if d not in crit and any(any(k in p for k in _WARN) for p in d["problems"])]
    if crit:
        return ("🔴", f"[b]🔴 Требует внимания[/b] — {len(crit)} сделок с серьёзными проблемами "
                f"(не подтверждена оплата / пустые карточки)"
                + (f" и ещё {len(warn)} без следующего шага" if warn else "") + ".")
    if warn:
        return ("🟡", f"[b]🟡 Мелкие недочёты[/b] — {len(warn)} сделок без следующего шага или с "
                "просрочкой; серьёзных проблем нет.")
    return ("🟢", "[b]🟢 Всё в порядке[/b] — сделки заполнены и в движении.")


def _funnel_picture(fid: int, deals: list[dict], base: dict) -> str:
    name = FUNNELS[fid]["name"]
    active = [d for d in deals if d["sem"] != "S"]
    won = [d for d in deals if d["sem"] == "S"]
    lines = [f"[b]{name}[/b] — активных {len(active)}" + (f", закрытых «успешна» {len(won)}" if won else "")]
    if fid == 2:
        paid = sum(1 for d in deals if d["paid"] == "да")
        lines.append(f"Деньги: оплата подтверждена у {paid}, НЕ подтверждена у {len(deals) - paid}.")

    if active:
        lines.append("\n[b]Сейчас ведутся:[/b]")
        for d in active:
            bits = [_ref(d) + " — " + d["stage"]]
            if d["order_no"]:
                bits.append("заказ " + d["order_no"])
            sd = _date(d["ship_plan"]) if d["ship_plan"] else None
            if sd:
                bits.append("отгрузка " + sd.isoformat())
            ad = _date(d["arrival"]) if d["arrival"] else None
            if ad:
                bits.append("приход " + ad.isoformat())
            line = "— " + " · ".join(bits)
            if d["problems"]:
                line += "\n   ⚠️ " + "; ".join(d["problems"])
            lines.append(line)

    won_unpaid = [d for d in won if d["paid"] != "да"]
    if won_unpaid:
        lines.append("\n[b]В этапе «успешна», но оплата не подтверждена:[/b]")
        for d in won_unpaid:
            lines.append(f"— {_ref(d)}")

    if base:
        moved = [d["title"] for d in deals if (base.get(d["id"]) or {}).get("fingerprint") != d["fingerprint"]
                 and d["id"] in base]
        new = [d["title"] for d in deals if d["id"] not in base]
        stalled = [d["title"] for d in deals if d["problems"] and d["id"] in base
                   and (base.get(d["id"]) or {}).get("fingerprint") == d["fingerprint"]]
        lines.append("\n[b]Что изменилось с прошлой проверки:[/b]")
        chunks = []
        if moved:
            chunks.append("двигались: " + ", ".join(f"«{t}»" for t in moved))
        if new:
            chunks.append("новые: " + ", ".join(f"«{t}»" for t in new))
        lines.append(("• " + "; ".join(chunks)) if chunks else "• движения по сделкам не было.")
        if stalled:
            lines.append("• без изменений и с проблемами: " + ", ".join(f"«{t}»" for t in stalled))
    else:
        lines.append("\n[i]Первая проверка — сравнивать пока не с чем (со следующей будет видно, что сделали).[/i]")
    return "\n".join(lines)


def _recommendations(all_deals: list[dict]) -> str:
    def names(pred):
        picked = [_short(d) for d in all_deals if pred(d)]
        return f"{len(picked)} — " + ", ".join(picked) if picked else ""
    recs = []
    unpaid = names(lambda d: any("оплата не подтверждена" in p for p in d["problems"]))
    if unpaid:
        recs.append(f"[b]Подтвердить оплату[/b] ({unpaid}) — они в этапе «успешна», но оплата не отмечена "
                    "(поставьте «да» в поле «Оплата произведена» или верните сделку на актуальный этап).")
    no_next = names(lambda d: "нет следующего дела/задачи" in d["problems"])
    if no_next:
        recs.append(f"[b]Проставить следующий шаг[/b] ({no_next}) — задачу или дело с дедлайном, "
                    "иначе непонятно, кто двигает сделку дальше.")
    empty = names(lambda d: any("пустая карточка" in p for p in d["problems"]))
    if empty:
        recs.append(f"[b]Заполнить карточку[/b] ({empty}) — поставщик, товар, сумма.")
    overdue = names(lambda d: any("просрочено" in p for p in d["problems"]))
    if overdue:
        recs.append(f"[b]Обновить просроченные сроки[/b] ({overdue}) — плановые даты в прошлом без факта.")
    if any(d["funnel"] == 2 and d["sem"] != "S" and not (d["prod_stage"] or d["ship_plan"] or d["arrival"])
           for d in all_deals):
        recs.append("[b]Заполнять производственные и логистические поля[/b] по закупке (стадия пр-ва, "
                    "даты отгрузки/прихода в КРД) — иначе не видно, где товар и когда он придёт.")
    if not recs:
        return "Рекомендаций нет — данные в порядке. ✅"
    return "\n".join(f"{i}. {r}" for i, r in enumerate(recs, 1))


def run_summary(dry=False):
    _ensure_table()
    today = msk_now().date().strftime("%d.%m.%Y")
    for owner_id, cfg in OWNERS.items():
        pictures, all_deals = [], []
        for fid in cfg["funnels"]:
            deals = collect(fid)
            pictures.append(_funnel_picture(fid, deals, _baseline(fid)))
            all_deals += deals
        icon, status_line = _severity(all_deals)
        total = len(all_deals)
        active = sum(1 for d in all_deals if d["sem"] != "S")
        head = (f"[b]Сводка по воронкам «{cfg['name']}» на {today}[/b]\n"
                f"{status_line}\n"
                f"Сделок всего: {total} — в работе {active}, закрыто «успешна» {total - active}.")
        body = head + "\n\n" + "\n\n".join(pictures)
        body += "\n\n[b]⚠️ Что срочно исправить:[/b]\n" + _recommendations(all_deals)
        title = f"{icon} Воронки «{cfg['name']}»: сводка на {today}"
        responsible = TEST_RESPONSIBLE or owner_id
        if dry:
            print(f"\n===== ЗАДАЧА (отв. id {responsible}) =====\nЗаголовок: {title}\n{body}")
            continue
        res = cs.tool_create_bitrix_task({
            "title": title, "description": body,
            "responsible_bitrix_user_id": responsible, "creator_bitrix_user_id": CREATOR_ID,
            "deadline": (msk_now().replace(hour=18, minute=0, second=0, microsecond=0)).isoformat(),
            "confirm_past_deadline": True,
            "result_criteria": "Воронка актуализирована: данные заполнены, проставлены следующие шаги, проблемы устранены.",
        })
        logging.info("funnel summary «%s» → отв.%s: task %s", cfg["name"], responsible, res.get("task_id"))
        print(f"создана задача {res.get('task_id')} (воронки {cfg['name']}, отв. id {responsible})")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    dry = "--dry-run" in sys.argv
    if mode == "summary":
        run_summary(dry)
    else:
        run_check(dry)
