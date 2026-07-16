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
    28: {"name": "Артур Степанян", "funnels": [2, 4]},
    30: {"name": "Наталья Горюнова", "funnels": [6]},
}
PORTAL = "https://b24-0xrp3s.bitrix24.ru"
CREATOR_ID = 22  # ИИ Агент

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
def _funnel_picture(fid: int, deals: list[dict], base: dict) -> str:
    name = FUNNELS[fid]["name"]
    lines = [f"[b]{name}[/b] — активных сделок: {len(deals)}"]
    paid = sum(1 for d in deals if d["paid"] == "да")
    if fid == 2:
        lines.append(f"Оплачено: {paid} · оплата не подтверждена: {len(deals) - paid}")
    # по этапам
    from collections import Counter
    by_stage = Counter(d["stage"] for d in deals)
    lines.append("По этапам: " + ", ".join(f"{k} — {v}" for k, v in by_stage.items()))
    # заказы в работе: номер + товар/срок
    inwork = [d for d in deals if d["sem"] != "S"]
    if inwork:
        lines.append("\n[b]В работе:[/b]")
        for d in inwork:
            bits = [f"[URL={d['url']}]{d['title']}[/URL]", d["stage"]]
            if d["order_no"]:
                bits.append(f"заказ {d['order_no']}")
            if d["ship_plan"]:
                sd = _date(d["ship_plan"])
                if sd:
                    bits.append(f"отгрузка {sd.isoformat()}")
            if d["arrival"]:
                ad = _date(d["arrival"])
                if ad:
                    bits.append(f"приход {ad.isoformat()}")
            lines.append("— " + " · ".join(bits))
    # что изменилось с прошлой проверки
    if base:
        moved, stalled = [], []
        for d in deals:
            b = base.get(d["id"])
            if not b:
                moved.append(f"{d['title']} (новая в контроле)")
            elif d["fingerprint"] != b["fingerprint"]:
                moved.append(d["title"])
            elif d["problems"]:
                stalled.append(d["title"])
        lines.append("\n[b]С прошлой проверки:[/b]")
        lines.append(("Есть движение: " + ", ".join(moved)) if moved else "Движения по сделкам не было.")
        if stalled:
            lines.append("Без изменений и с проблемами: " + ", ".join(stalled))
    else:
        lines.append("\n[i]Это первая проверка — сравнивать пока не с чем.[/i]")
    return "\n".join(lines)


def _recommendations(all_deals: list[dict]) -> str:
    def ids(pred):
        return ", ".join("#%d" % d["id"] for d in all_deals if pred(d))
    recs = []
    no_next = ids(lambda d: "нет следующего дела/задачи" in d["problems"])
    if no_next:
        recs.append(f"Проставьте следующий шаг (задачу/дело) по сделкам: {no_next}.")
    unpaid = ids(lambda d: any("оплата не подтверждена" in p for p in d["problems"]))
    if unpaid:
        recs.append(f"Подтвердите оплату (поле «Оплата произведена») или верните на актуальный этап: {unpaid}.")
    empty = ids(lambda d: any("пустая карточка" in p for p in d["problems"]))
    if empty:
        recs.append(f"Заполните карточку (поставщик, товар, сумма): {empty}.")
    overdue = ids(lambda d: any("просрочено" in p for p in d["problems"]))
    if overdue:
        recs.append(f"Обновите даты/статус по просроченным сделкам: {overdue}.")
    # общая по закупке (C2) — производственные/логистические поля не заполнены у активных сделок
    if any(d["funnel"] == 2 and d["sem"] != "S" and not (d["prod_stage"] or d["ship_plan"] or d["arrival"])
           for d in all_deals):
        recs.append("Заполняйте производственные и логистические поля (стадия пр-ва, даты отгрузки/"
                    "прихода в КРД) — иначе не видно, где товар и когда он придёт.")
    if not recs:
        return "Рекомендаций нет — данные в порядке. ✅"
    return "\n".join(f"— {r}" for r in recs)


def run_summary(dry=False):
    _ensure_table()
    today = msk_now().date().strftime("%d.%m.%Y")
    for owner_id, cfg in OWNERS.items():
        pictures, all_deals = [], []
        for fid in cfg["funnels"]:
            deals = collect(fid)
            base = _baseline(fid)
            pictures.append(_funnel_picture(fid, deals, base))
            all_deals += deals
        n_probs = sum(1 for d in all_deals if d["problems"])
        head = (f"[b]Сводка по воронкам на {today}[/b]\n"
                f"Всего активных сделок: {len(all_deals)} · требуют внимания: {n_probs}\n")
        body = head + "\n\n".join(pictures)
        body += "\n\n[b]⚠️ Рекомендации — что исправить (срочно):[/b]\n" + _recommendations(all_deals)
        title = f"Воронки: сводка и рекомендации на {today}"
        if dry:
            print(f"\n===== ЗАДАЧА → {cfg['name']} (id {owner_id}) =====\nЗаголовок: {title}\n{body}")
            continue
        res = cs.tool_create_bitrix_task({
            "title": title, "description": body,
            "responsible_bitrix_user_id": owner_id, "creator_bitrix_user_id": CREATOR_ID,
            "deadline": (msk_now().replace(hour=18, minute=0, second=0, microsecond=0)).isoformat(),
            "confirm_past_deadline": True,
            "result_criteria": "Воронка актуализирована: данные заполнены, проставлены следующие шаги, проблемы устранены.",
        })
        logging.info("funnel-control summary → %s (%s): task %s", cfg["name"], owner_id, res.get("task_id"))
        print(f"создана задача {res.get('task_id')} → {cfg['name']}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    dry = "--dry-run" in sys.argv
    if mode == "summary":
        run_summary(dry)
    else:
        run_check(dry)
