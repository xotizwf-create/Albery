"""Контроль CRM-воронок Albery — детерминированно (без хода LLM: точно, дёшево, надёжно).

Две автоматизации (задача владельца 16.07, доработано по замечаниям):
  check   (Пн/Ср/Пт 09:00) — проверяет каждую сделку и ПИШЕТ СНИМОК СОСТОЯНИЯ в БД
          (funnel_control_snapshots). Никому не шлёт — копит для сравнения «двигались или нет».
  summary (Ср 15:00) — «паспорт» каждой сделки: деньги (стоимость партии, оплата и когда),
          производство и сроки (когда придёт), ближайший шаг, блокеры и последние комментарии
          из ленты, что изменилось с прошлой проверки, рекомендации. Создаёт НОВУЮ задачу
          владельцу воронки (на время тестов — на ИИ Агента, env FUNNEL_SUMMARY_TEST_RESPONSIBLE).

--dry-run: печатает, ничего не пишет в БД и не создаёт задач.
"""
import os
import re
import sys
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
    2: {"name": "Закупка товара на WB", "checks": {"empty_card", "payment", "next_task", "overdue", "money"}},
    4: {"name": "Пополнение МП/склад", "checks": {"next_task", "overdue"}},
    6: {"name": "Рекламации/брак", "checks": {"next_task", "overdue"}},
}
OWNERS = {
    28: {"name": "Артур", "funnels": [2, 4]},
    30: {"name": "Наталья", "funnels": [6]},
}
PORTAL = "https://b24-0xrp3s.bitrix24.ru"
CREATOR_ID = 22  # ИИ Агент
# На время тестов сводки ставятся задачей на ИИ Агента (22). В бой: выставить env в 0/пусто.
TEST_RESPONSIBLE = int(os.getenv("FUNNEL_SUMMARY_TEST_RESPONSIBLE", "22") or "0") or None

_CRIT = ("оплата не подтверждена", "пустая карточка", "стоимость партии не указана")
_WARN = ("нет следующего", "просрочено", "канбан показывает 0")
_BLOCKER_RE = re.compile(r"брак|проблем|задерж|расхожден|не соответ|рекламац|срыв|штраф", re.I)

F_PAID = "UF_CRM_1783670137991"       # Оплата произведена (60=да, 58=нет)
F_SUM_TEXT = "UF_CRM_1783669649285"   # «Сумма заказа» (ТЕКСТ — канбан его не видит)
F_SUPPLIER = "UF_CRM_1783611990212"
F_PRODUCT = "UF_CRM_1783612114247"
F_ORDER_NO = "UF_CRM_WB_ORDER_NO"
F_PROD_STAGE = "UF_CRM_WB_PROD_STAGE"
F_PROD_READY = "UF_CRM_WB_PROD_READY_PLAN"
F_PREPAY_SUM = "UF_CRM_WB_PREPAYMENT_SUM"
F_PREPAY_DATE = "UF_CRM_WB_PREPAYMENT_DATE"
F_ARRIVAL = "UF_CRM_1783671293981"    # Дата прихода товара в КРД
F_ARTICLE = "UF_CRM_WB_ARTICLE"       # Артикул(ы)
F_QTY = "UF_CRM_WB_QTY"               # Количество, шт
F_CONTENT = "UF_CRM_WB_ORDER_CONTENT" # Что в заказе
PLAN_DATES = {
    F_PROD_READY: (None, "план готовности пр-ва"),
    "UF_CRM_WB_PLAN_SHIP_DATE": ("UF_CRM_WB_ACTUAL_SHIP_DATE", "плановая отгрузка"),
    "UF_CRM_1783671254915": (F_ARRIVAL, "план приёмки в КРД"),
}
SELECT = (["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CURRENCY_ID", "MOVED_TIME",
           F_PAID, F_SUM_TEXT, F_SUPPLIER, F_PRODUCT, F_ORDER_NO, F_PROD_STAGE,
           F_PREPAY_SUM, F_PREPAY_DATE, F_ARRIVAL, F_ARTICLE, F_QTY, F_CONTENT]
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


def _date(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except Exception:  # noqa: BLE001
        return None


def _dmy(v) -> str:
    d = _date(v)
    return d.strftime("%d.%m") if d else ""


_MONEY_RE = re.compile(r"[-+]?\d[\d\s.,]*")


def _parse_money(txt) -> tuple[float | None, str]:
    """«$21,639.50» → (21639.5, 'USD'); «73188.50» → (73188.5, ''). Терпимо к формату."""
    s = str(txt or "").strip()
    if not s:
        return None, ""
    cur = "USD" if "$" in s else ("RUB" if "₽" in s or "руб" in s.lower() else "")
    m = _MONEY_RE.search(s.replace(" ", " "))
    if not m:
        return None, cur
    num = m.group(0).replace(" ", "")
    # 21,639.50 → 21639.50 ; 21639,50 → 21639.50
    if "," in num and "." in num:
        num = num.replace(",", "")
    elif "," in num:
        num = num.replace(",", ".")
    try:
        return float(num), cur
    except ValueError:
        return None, cur


def _fmt_money(amount: float | None, cur: str) -> str:
    if amount is None:
        return ""
    s = f"{amount:,.2f}".replace(",", " ").replace(".00", "")
    return (f"${s}" if cur == "USD" else f"{s} ₽" if cur == "RUB" else s)


def _deal_extras(deal_id: int) -> dict:
    """Лента + дела одной сделки: последние комментарии, ближайший открытый шаг,
    закрытые «оплатные» дела (когда была оплата), блокеры по ключевым словам."""
    comments = []
    try:
        rows = cs._crm_call("crm.timeline.comment.list", {
            "filter": {"ENTITY_ID": deal_id, "ENTITY_TYPE": "deal"},
            "select": ["CREATED", "COMMENT"], "order": {"CREATED": "DESC"},
        }).get("result") or []
        for c in rows[:2]:
            txt = re.sub(r"\[[^\]]*\]|<[^>]*>", "", str(c.get("COMMENT") or "")).strip()
            if txt:
                comments.append((_dmy(c.get("CREATED")), txt[:160]))
    except Exception:  # noqa: BLE001
        logging.warning("timeline comments failed deal=%s", deal_id)
    acts = []
    try:
        acts = cs._crm_call("crm.activity.list", {
            "filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": deal_id},
            "select": ["SUBJECT", "DEADLINE", "COMPLETED", "DESCRIPTION"],
            "order": {"DEADLINE": "ASC"},
        }).get("result") or []
    except Exception:  # noqa: BLE001
        logging.warning("activities failed deal=%s", deal_id)
    open_acts = [a for a in acts if a.get("COMPLETED") != "Y"]
    next_act = open_acts[0] if open_acts else None
    paid_acts = [a for a in acts if a.get("COMPLETED") == "Y"
                 and re.search(r"оплат", str(a.get("SUBJECT") or ""), re.I)]
    blockers = []
    for a in open_acts:
        blob = f"{a.get('SUBJECT') or ''} {a.get('DESCRIPTION') or ''}"
        if _BLOCKER_RE.search(blob):
            blockers.append(re.sub(r"\s+", " ", str(a.get("SUBJECT") or ""))[:180])
    for when, txt in comments:
        if _BLOCKER_RE.search(txt):
            blockers.append(f"{txt[:160]} (коммент {when})")
    return {"comments": comments, "next_act": next_act, "paid_acts": paid_acts,
            "blockers": blockers, "n_open": len(open_acts)}


def collect(funnel_id: int) -> list[dict]:
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
        sem = (st.get("semantics") or "")[:1].upper()
        if sem == "F":
            continue
        is_new = int(st.get("sort") or 0) <= 10 or "NEW" in (d.get("STAGE_ID") or "")
        paid_raw = str(d.get(F_PAID) or "")
        paid = "да" if paid_raw == "60" else ("нет" if paid_raw == "58" else "")
        filled = bool(d.get(F_SUPPLIER) or d.get(F_PRODUCT) or d.get(F_SUM_TEXT))
        # деньги: штатное поле «Сумма» (его видит канбан) ИЛИ текстовое «Сумма заказа»
        opp = float(d.get("OPPORTUNITY") or 0)
        if opp > 0:
            amount, cur, money_src = opp, (d.get("CURRENCY_ID") or ""), "field"
        else:
            amount, cur = _parse_money(d.get(F_SUM_TEXT))
            money_src = "text" if amount else ""
        extras = _deal_extras(int(d["ID"]))
        has_next = extras["n_open"] > 0
        probs = []
        if "empty_card" in checks and not is_new and sem != "S" and not filled:
            probs.append("пустая карточка (нет поставщика/товара/суммы)")
        if "payment" in checks and sem == "S" and paid != "да":
            probs.append("этап «успешна», но оплата не подтверждена")
        if "next_task" in checks and sem != "S" and not has_next:
            probs.append("нет следующего дела/задачи")
        if "money" in checks and sem != "S" and not is_new:
            if amount is None:
                probs.append("стоимость партии не указана")
            elif money_src == "text":
                probs.append("сумма только в текстовом поле — канбан показывает 0 ₽")
        if "overdue" in checks and sem != "S":
            for pf, (af, label) in PLAN_DATES.items():
                pv, av = d.get(pf), (d.get(af) if af else None)
                pd = _date(pv) if pv and not av else None
                if pd and pd < today:
                    probs.append(f"просрочено: {label} ({pd.strftime('%d.%m')})")
        nxt = extras["next_act"]
        # следующий этап по воронке (ближайший process/success этап после текущего по sort)
        cur_sort = int(st.get("sort") or 0)
        nxt_stage = next((s["name"] for s in sorted(stages.values(), key=lambda x: x["sort"])
                          if s["sort"] > cur_sort and (s.get("semantics") or "P")[:1].upper() != "F"
                          and "провал" not in (s["name"] or "").lower()), "")
        out.append({
            "id": int(d["ID"]), "funnel": funnel_id,
            "title": (d.get("TITLE") or "").strip() or f"Сделка #{d['ID']}",
            "stage": st.get("name") or d.get("STAGE_ID"), "sem": sem, "paid": paid,
            "stage_since": _dmy(d.get("MOVED_TIME")), "next_stage": nxt_stage,
            "article": (d.get(F_ARTICLE) or "").strip(),
            "qty": d.get(F_QTY), "content": (d.get(F_CONTENT) or "").strip(),
            "amount": amount, "cur": cur, "money_src": money_src,
            "prepay": (d.get(F_PREPAY_SUM), _dmy(d.get(F_PREPAY_DATE))),
            "paid_acts": extras["paid_acts"],
            "supplier": (d.get(F_SUPPLIER) or "").split(",")[0].split("ИНН")[0].strip()[:40],
            "product": (d.get(F_PRODUCT) or "").strip()[:60],
            "order_no": (d.get(F_ORDER_NO) or "").strip(),
            "prod_stage": (d.get(F_PROD_STAGE) or "").strip(),
            "prod_ready": _dmy(d.get(F_PROD_READY)),
            "ship_plan": _dmy(d.get("UF_CRM_WB_PLAN_SHIP_DATE") or d.get("UF_CRM_WB_ACTUAL_SHIP_DATE")),
            "arrival": _dmy(d.get(F_ARRIVAL)),
            "next_act": (re.sub(r"\s+", " ", str(nxt.get("SUBJECT") or ""))[:100],
                         _dmy(nxt.get("DEADLINE"))) if nxt else None,
            "comments": extras["comments"], "blockers": extras["blockers"],
            "has_next": has_next, "filled": filled, "problems": probs,
            "url": f"{PORTAL}/crm/deal/details/{d['ID']}/",
            "fingerprint": "|".join([str(st.get("name")), paid, str(has_next), str(filled),
                                     str(amount), (nxt or {}).get("SUBJECT", "") if nxt else "",
                                     "/".join(sorted(probs))]),
        })
    return out


# ---------- check (Пн/Ср/Пт): снимок в БД, тишина -------------------------------------------
def run_check(dry=False):
    _ensure_table()
    run_ts = msk_now()
    rows = []
    for fid in FUNNELS:
        for d in collect(fid):
            rows.append((run_ts, fid, d["id"], d["title"], d["stage"], d["sem"], d["paid"],
                         d["has_next"], d["filled"], d["problems"], d["fingerprint"]))
    if dry:
        print(f"[check dry] сделок {len(rows)}, с проблемами {sum(1 for r in rows if r[9])} — снимок НЕ пишется")
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO funnel_control_snapshots "
                "(run_ts, funnel_id, deal_id, title, stage, sem, paid, has_next, filled, problems, fingerprint) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    logging.info("funnel-control check: снимок %s сделок сохранён", len(rows))


def _snapshot_at(funnel_id: int, where_ts: str, args: tuple) -> dict:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT max(run_ts) AS rt FROM funnel_control_snapshots "
                        f"WHERE funnel_id=%s AND {where_ts}", (funnel_id, *args))
            row = cur.fetchone()
            rt = row and row["rt"]
            if not rt:
                return {}
            cur.execute("SELECT deal_id, stage, paid, has_next, problems, fingerprint "
                        "FROM funnel_control_snapshots WHERE funnel_id=%s AND run_ts=%s",
                        (funnel_id, rt))
            return {r["deal_id"]: dict(r) for r in cur.fetchall()}


def _baseline(funnel_id: int) -> dict:
    """Снимок предыдущего дня (для «с прошлой проверки»)."""
    start = msk_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return _snapshot_at(funnel_id, "run_ts < %s", (start,))


def _week_baseline(funnel_id: int) -> dict:
    """Снимок ~недельной давности (для «изменения за неделю»): последний прогон старше 6 дней,
    иначе самый ранний прогон старше сегодняшнего дня."""
    now = msk_now()
    week = _snapshot_at(funnel_id, "run_ts < %s", (now - __import__("datetime").timedelta(days=6),))
    if week:
        return week
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT min(run_ts) AS rt FROM funnel_control_snapshots "
                        "WHERE funnel_id=%s AND run_ts < %s",
                        (funnel_id, now.replace(hour=0, minute=0, second=0, microsecond=0)))
            row = cur.fetchone()
            rt = row and row["rt"]
            if not rt:
                return {}
            cur.execute("SELECT deal_id, stage, paid, has_next, problems, fingerprint "
                        "FROM funnel_control_snapshots WHERE funnel_id=%s AND run_ts=%s",
                        (funnel_id, rt))
            return {r["deal_id"]: dict(r) for r in cur.fetchall()}


# ---------- summary (Ср): паспорт каждой сделки + diff + рекомендации → задача ---------------
def _ref(d) -> str:
    return f"[URL={d['url']}]{d['title']}[/URL]"


def _severity(deals):
    crit = [d for d in deals if any(any(k in p for k in _CRIT) for p in d["problems"])]
    warn = [d for d in deals if d not in crit and d["problems"]]
    if crit:
        return ("🔴", f"[b]🔴 Требует внимания[/b] — серьёзные проблемы у {len(crit)} сделок "
                f"(оплата/стоимость/пустые карточки)" + (f", ещё {len(warn)} с недочётами" if warn else "") + ".")
    if warn:
        return ("🟡", f"[b]🟡 Недочёты[/b] — {len(warn)} сделок (нет следующего шага / сроки).")
    return ("🟢", "[b]🟢 Всё в порядке[/b] — сделки заполнены и в движении.")


def _week_diff_text(d, bweek: dict) -> str:
    """Человекочитаемые изменения сделки против недельного снимка."""
    if not bweek:
        return "первая проверка — сравнить пока не с чем"
    b = bweek.get(d["id"])
    if not b:
        return "новая сделка (на прошлой проверке её не было)"
    bits = []
    if (b.get("stage") or "") != d["stage"]:
        bits.append(f"этап: «{b.get('stage')}» → «{d['stage']}»")
    if (b.get("paid") or "") != d["paid"]:
        bits.append(f"оплата: «{b.get('paid') or 'не отмечена'}» → «{d['paid'] or 'не отмечена'}»")
    if bool(b.get("has_next")) != d["has_next"]:
        bits.append("появилось следующее дело" if d["has_next"] else "следующее дело закрыто/пропало")
    old_probs, new_probs = set(b.get("problems") or []), set(d["problems"])
    fixed = old_probs - new_probs
    arisen = new_probs - old_probs
    if fixed:
        bits.append("исправлено: " + "; ".join(sorted(fixed)))
    if arisen:
        bits.append("новое: " + "; ".join(sorted(arisen)))
    return "; ".join(bits) if bits else "изменений не было"


def _card_procurement(d, bweek: dict) -> str:
    """Карточка закупки в структуре владельца (задача 1500, формат 16.07)."""
    L = [f"— {_ref(d)} — [b]{d['stage']}[/b]" + (f" (с {d['stage_since']})" if d["stage_since"] else "")]

    # Сумма заказа
    if d["amount"] is not None:
        s = _fmt_money(d["amount"], d["cur"])
        if d["money_src"] == "text":
            s += " (в текстовом поле — на канбане 0 ₽)"
    else:
        s = "не указана ⚠️"
    L.append(f"[b]Сумма заказа:[/b] {s}")

    # Артикулы и название товаров, кол-во
    goods = []
    if d["article"]:
        goods.append("арт. " + d["article"])
    if d["product"]:
        goods.append(d["product"])
    elif d["content"]:
        goods.append(d["content"][:120])
    if d["qty"]:
        goods.append(f"{d['qty']} шт")
    L.append("[b]Артикулы и товары, кол-во:[/b] " + (" · ".join(goods) if goods else "не заполнено ⚠️"))

    # Оплачено и даты оплат
    pay = []
    if d["paid"]:
        pay.append(f"«Оплата произведена»: {d['paid']}")
    pre_sum, pre_date = d["prepay"]
    if pre_sum:
        pay.append(f"предоплата {pre_sum}" + (f" от {pre_date}" if pre_date else ""))
    for a in d["paid_acts"]:
        pay.append(f"«{(a.get('SUBJECT') or '')[:60]}» — выполнено {_dmy(a.get('DEADLINE'))}")
    L.append("[b]Оплачено и даты оплат:[/b] " + ("; ".join(pay) if pay else "данных об оплате нет ⚠️"))

    # Текущий статус (этап + производство + сроки + лента)
    stat = [d["stage"] + (f" с {d['stage_since']}" if d["stage_since"] else "")]
    if d["prod_stage"]:
        stat.append("стадия пр-ва: " + d["prod_stage"])
    if d["prod_ready"]:
        stat.append("готовность пр-ва: " + d["prod_ready"])
    if d["ship_plan"]:
        stat.append("отгрузка: " + d["ship_plan"])
    if d["arrival"]:
        stat.append("приход в КРД: " + d["arrival"])
    line = "; ".join(stat)
    if d["comments"]:
        line += ". Из комментариев: " + " · ".join(f"«{txt}» ({when})" for when, txt in d["comments"])
    L.append("[b]Текущий статус:[/b] " + line)

    # Изменения за неделю
    L.append("[b]Изменения за неделю:[/b] " + _week_diff_text(d, bweek))

    # Текущие блокеры
    nsubj = (d["next_act"][0] if d["next_act"] else "")[:60]
    blk = [b for b in d["blockers"] if not (nsubj and b[:60] == nsubj)]
    L.append("[b]Текущие блокеры:[/b] " + (" | ".join(blk[:2]) if blk else "не зафиксированы"))

    # Незаполненная информация
    missing = []
    if d["amount"] is None:
        missing.append("сумма заказа")
    elif d["money_src"] == "text":
        missing.append("сумма в поле «Сумма» (для канбана)")
    if not (d["article"] or d["product"] or d["content"]):
        missing.append("артикулы/товары")
    if not d["qty"]:
        missing.append("количество")
    if not d["supplier"]:
        missing.append("поставщик")
    if d["sem"] != "S":
        if not d["prod_stage"]:
            missing.append("стадия производства")
        if not d["prod_ready"]:
            missing.append("готовность пр-ва")
        if not d["ship_plan"]:
            missing.append("дата отгрузки")
        if not d["arrival"]:
            missing.append("приход в КРД")
    L.append("[b]Незаполненная информация:[/b] " + (", ".join(missing) if missing else "всё заполнено ✅"))

    # Следующий шаг исходя из воронки
    if d["next_act"]:
        subj, dl = d["next_act"]
        step = f"{subj}" + (f" (до {dl})" if dl else "")
    elif d["sem"] == "S":
        step = "сделка закрыта — подтвердить оплату и архивировать"
    else:
        step = ("дело не назначено ⚠️"
                + (f" — по воронке следующий этап «{d['next_stage']}»" if d["next_stage"] else ""))
    L.append("[b]Следующий шаг исходя из воронки:[/b] " + step)

    # Что нужно доработать
    todo = []
    if any("оплата не подтверждена" in p for p in d["problems"]):
        todo.append("подтвердить оплату (поле «Оплата произведена») или вернуть сделку на актуальный этап")
    if not d["has_next"] and d["sem"] != "S":
        todo.append("назначить ответственное дело/задачу с дедлайном")
    if d["money_src"] == "text":
        todo.append("перенести сумму в поле «Сумма» — сейчас на канбане 0 ₽")
    if d["amount"] is None:
        todo.append("указать сумму заказа")
    if missing and d["sem"] != "S":
        todo.append("заполнить: " + ", ".join(m for m in missing if "Сумма" not in m and "сумма" not in m)[:160])
    for p in d["problems"]:
        if "просрочено" in p:
            todo.append("обновить " + p)
    L.append("[b]Что нужно доработать:[/b] " + ("; ".join(dict.fromkeys(todo)) if todo else "ничего — карточка в порядке ✅"))
    return "\n".join(L)


def _passport(d) -> str:
    """Полный паспорт одной активной сделки: деньги, оплата, производство, шаг, блокеры, лента."""
    head = f"— {_ref(d)} — [b]{d['stage']}[/b]" + (f" (на этапе с {d['stage_since']})" if d["stage_since"] else "")
    lines = [head]
    ctx = []
    if d["product"]:
        ctx.append(d["product"])
    if d["supplier"]:
        ctx.append("поставщик: " + d["supplier"])
    if d["order_no"]:
        ctx.append("заказ №" + d["order_no"])
    if ctx:
        lines.append("   " + " · ".join(ctx))
    # деньги и производство — это поля воронки ЗАКУПКИ; в других воронках показываем только то, что есть
    is_procurement = d["funnel"] == 2
    if is_procurement or d["amount"] is not None:
        money = _fmt_money(d["amount"], d["cur"]) if d["amount"] is not None else "[b]не указана[/b] ⚠️"
        m = f"   💰 Партия: {money}"
        if d["money_src"] == "text":
            m += " (в текстовом поле — на канбане 0 ₽)"
        pre_sum, pre_date = d["prepay"]
        if pre_sum:
            m += f" · предоплата {pre_sum}" + (f" от {pre_date}" if pre_date else "")
        if d["paid"]:
            m += f" · «Оплата произведена»: {d['paid']}"
        if d["paid_acts"]:
            a = d["paid_acts"][-1]
            m += f" · оплата: дело «{(a.get('SUBJECT') or '')[:50]}» закрыто {_dmy(a.get('DEADLINE'))}"
        lines.append(m)
    if is_procurement:
        prod = []
        if d["prod_stage"]:
            prod.append("стадия: " + d["prod_stage"])
        prod.append("готовность пр-ва: " + (d["prod_ready"] or "не указана"))
        prod.append("отгрузка: " + (d["ship_plan"] or "не указана"))
        prod.append("приход в КРД: " + (d["arrival"] or "не указан"))
        lines.append("   🏭 " + " · ".join(prod))
    # ближайший шаг
    if d["next_act"]:
        subj, dl = d["next_act"]
        lines.append(f"   👣 Ближайший шаг: {subj}" + (f" (до {dl})" if dl else ""))
    else:
        lines.append("   👣 Ближайший шаг: [b]не назначен[/b] ⚠️")
    # блокеры (без дубля с «ближайшим шагом» — это одно и то же дело)
    nsubj = (d["next_act"][0] if d["next_act"] else "")[:60]
    blk = [b for b in d["blockers"] if not (nsubj and b[:60] == nsubj)]
    if blk:
        lines.append("   ⛔ Блокеры: " + " | ".join(blk[:2]))
    # лента
    if d["comments"]:
        lines.append("   💬 " + " · ".join(f"{when}: «{txt}»" for when, txt in d["comments"]))
    # прочие проблемы (кроме уже показанных выше)
    shown = ("стоимость", "нет следующего", "канбан")
    rest = [p for p in d["problems"] if not any(s in p for s in shown)]
    if rest:
        lines.append("   ⚠️ " + "; ".join(rest))
    return "\n".join(lines)


def _funnel_picture(fid: int, deals: list[dict], base: dict) -> str:
    name = FUNNELS[fid]["name"]
    active = [d for d in deals if d["sem"] != "S"]
    won = [d for d in deals if d["sem"] == "S"]
    total_usd = sum(d["amount"] for d in active if d["amount"] and d["cur"] in ("USD", ""))
    lines = [f"[b]═══ {name} ═══[/b]",
             f"Активных {len(active)}" + (f" на ~{_fmt_money(total_usd, 'USD')}" if total_usd else "")
             + (f", закрытых «успешна» {len(won)}" if won else "")]
    if fid == 2:
        # Воронка закупки: структурированная карточка по КАЖДОЙ закупке (формат владельца).
        bweek = _week_baseline(fid)
        won_unpaid = [d for d in won if d["paid"] != "да"]
        won_paid = [d for d in won if d["paid"] == "да"]
        lines.append("")
        for d in active + won_unpaid:
            lines.append(_card_procurement(d, bweek) + "\n")
        if won_paid:
            lines.append("[b]Закрыты и оплачены (без карточки):[/b] "
                         + ", ".join(f"{_ref(d)}" + (f" · {_fmt_money(d['amount'], d['cur'])}" if d["amount"] else "")
                                     for d in won_paid))
        return "\n".join(lines)
    if active:
        lines.append("")
        lines += [_passport(d) + "\n" for d in active]
    won_unpaid = [d for d in won if d["paid"] != "да"]
    if won_unpaid:
        s = sum(d["amount"] for d in won_unpaid if d["amount"])
        lines.append(f"[b]«Успешна», но оплата не подтверждена ({len(won_unpaid)} на ~{_fmt_money(s, 'USD')}):[/b]")
        for d in won_unpaid:
            lines.append(f"— {_ref(d)}" + (f" · {_fmt_money(d['amount'], d['cur'])}" if d["amount"] else ""))
    if base:
        moved = [d["title"] for d in deals if d["id"] in base and base[d["id"]]["fingerprint"] != d["fingerprint"]]
        new = [d["title"] for d in deals if d["id"] not in base]
        stalled = [d["title"] for d in deals
                   if d["problems"] and d["id"] in base and base[d["id"]]["fingerprint"] == d["fingerprint"]]
        lines.append("\n[b]С прошлой проверки:[/b]")
        chunks = []
        if moved:
            chunks.append("двигались: " + ", ".join(f"«{t}»" for t in moved))
        if new:
            chunks.append("новые: " + ", ".join(f"«{t}»" for t in new))
        lines.append(("• " + "; ".join(chunks)) if chunks else "• движения не было.")
        if stalled:
            lines.append("• стоят с проблемами без изменений: " + ", ".join(f"«{t}»" for t in stalled))
    else:
        lines.append("\n[i]Первая проверка — со следующей будет видно, что изменилось.[/i]")
    return "\n".join(lines)


def _recommendations(all_deals) -> str:
    def names(pred):
        picked = [f"«{d['title']}»" for d in all_deals if pred(d)]
        return f"{len(picked)} — " + ", ".join(picked) if picked else ""
    recs = []
    unpaid = names(lambda d: any("оплата не подтверждена" in p for p in d["problems"]))
    if unpaid:
        recs.append(f"[b]Подтвердить оплату[/b] ({unpaid}) — сделки «успешны», но оплата не отмечена: "
                    "поставьте «да» в поле «Оплата произведена» или верните на актуальный этап.")
    kanban0 = names(lambda d: any("канбан показывает 0" in p for p in d["problems"]))
    if kanban0:
        recs.append(f"[b]Перенести сумму в поле «Сумма»[/b] ({kanban0}) — стоимость вбита в текстовое "
                    "поле, поэтому на канбане сделки висят с 0 ₽ и общую стоимость этапов не видно.")
    nomoney = names(lambda d: "стоимость партии не указана" in d["problems"])
    if nomoney:
        recs.append(f"[b]Указать стоимость партии[/b] ({nomoney}).")
    no_next = names(lambda d: "нет следующего дела/задачи" in d["problems"])
    if no_next:
        recs.append(f"[b]Проставить следующий шаг[/b] ({no_next}) — дело/задачу с дедлайном.")
    empty = names(lambda d: any("пустая карточка" in p for p in d["problems"]))
    if empty:
        recs.append(f"[b]Заполнить карточку[/b] ({empty}) — поставщик, товар, сумма.")
    overdue = names(lambda d: any("просрочено" in p for p in d["problems"]))
    if overdue:
        recs.append(f"[b]Обновить просроченные сроки[/b] ({overdue}).")
    if any(d["funnel"] == 2 and d["sem"] != "S"
           and not (d["prod_stage"] or d["prod_ready"] or d["ship_plan"] or d["arrival"]) for d in all_deals):
        recs.append("[b]Заполнять производственные и логистические даты[/b] (стадия пр-ва, готовность, "
                    "отгрузка, приход в КРД) — сейчас невозможно ответить, где товар и когда придёт.")
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
        head = (f"[b]Сводка по воронкам «{cfg['name']}» на {today}[/b]\n{status_line}\n"
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
    _reg_key = f"crond:albery-funnel-control:{'summary' if mode == 'summary' else 'check'}"
    try:
        if mode == "summary":
            run_summary(dry)
        else:
            run_check(dry)
    except Exception as exc:
        if not dry:
            from shared.automation_registry import mark_system_run
            mark_system_run(_reg_key, "error", error=str(exc)[:300])
        raise
    if not dry:
        from shared.automation_registry import mark_system_run
        mark_system_run(_reg_key, "ok")
