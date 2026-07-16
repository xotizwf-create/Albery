# -*- coding: utf-8 -*-
"""WB-кабинет: аналитика Wildberries внутри Albery.

АРХИТЕКТУРА ЗАГРУЗКИ (tick-модель, пуленепробиваемая — переработано 16.07.2026 после
инцидента со спящими процессами):
- НЕТ долгоживущих процессов и многочасовых sleep. systemd-таймер каждые 30 минут запускает
  короткий тик (scripts/wb_sync.py → run_tick), который выходит за минуты.
- Квоты WB — истина в заголовке Retry-After: на 429 тик МГНОВЕННО пишет blocked_until в
  wb_sync_state и переходит к следующему источнику. Закрытый источник не трогается вообще
  (попытки тоже жгут квоту). Никаких ретраев по кругу.
- Бэкфилл истории — резюмируемые курсоры в wb_sync_state (cursor_date/done): финансы по
  7-дневным чанкам (≤20 за тик), реклама по 30-дневным; заказы/выкупы за полгода забираются
  ОДНИМ вызовом (dateFrom полгода назад), когда квота открыта. Переживает kill/reboot/что угодно.
- Лимиты по документации WB: statistics-api и advert-api — порядка 1 запрос/мин на метод
  (v1 orders/sales/stocks отвечают 429 с пометкой deprecated при превышении) → пейсинг 62с
  МЕЖДУ вызовами одного метода ДО обращения. X-Ratelimit-заголовки пишутся в журнал.
UI и агент читают ТОЛЬКО Postgres. Схема: migrations/053 (+054 колонки состояния синка).
"""
from __future__ import annotations

import json
import logging
import os
import time
import datetime as dt
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app import app, pg_connect  # noqa: E402

log = logging.getLogger("wb_cabinet")

STAT = "https://statistics-api.wildberries.ru"
ADV = "https://advert-api.wildberries.ru"
CONTENT = "https://content-api.wildberries.ru"
PRICES = "https://discounts-prices-api.wildberries.ru"
ANALYTICS = "https://seller-analytics-api.wildberries.ru"

BACKFILL_DAYS = int(os.getenv("WB_BACKFILL_DAYS", "182"))


class WBQuotaError(Exception):
    """WB ответил 429 с длинным Retry-After: квота метода закрыта до known-времени.
    Тик не ждёт — записывает blocked_until и идёт дальше."""

    def __init__(self, retry_after: float, detail: str = ""):
        super().__init__(f"quota closed for {retry_after:.0f}s: {detail[:120]}")
        self.retry_after = float(retry_after)
        self.detail = detail


# ------------------------------------------------------------------ client
class WBClient:
    """Строго последовательный клиент. Пейсинг per-method ДО вызова; на 429 с Retry-After
    больше короткого порога — WBQuotaError наверх (без сна). Заголовки лимитов сохраняются."""

    SHORT_WAIT_MAX = 90.0     # столько ещё можно подождать внутри тика
    _PACE_PER_METHOD = 62.0   # лимиты statistics/advert ≈ 1 запрос/мин на метод
    _PACED_HOSTS = ("statistics-api.wildberries.ru", "advert-api.wildberries.ru")

    def __init__(self) -> None:
        self.token = (os.getenv("WB_ANALYTICS_TOKEN") or "").strip()
        if not self.token:
            raise RuntimeError("WB_ANALYTICS_TOKEN is not configured in .env")
        self._last_call = 0.0
        self._last_by_method: dict[str, float] = {}
        self.last_ratelimit: dict[str, str] = {}

    def call(self, url: str, params: dict | None = None, body: Any = None,
             method: str | None = None, min_gap: float = 1.6, tries: int = 3) -> Any:
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        base = url.split("?")[0]
        if any(h in base for h in self._PACED_HOSTS):
            min_gap = max(min_gap, self._PACE_PER_METHOD)
        for attempt in range(1, tries + 1):
            gap = max(self._last_call + 1.6 - time.monotonic(),
                      self._last_by_method.get(base, -1e9) + min_gap - time.monotonic())
            if gap > 0:
                time.sleep(gap)
            req = urllib.request.Request(url, method=method or ("POST" if body is not None else "GET"))
            req.add_header("Authorization", self.token)
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                req.add_header("Content-Type", "application/json")
            self._last_call = time.monotonic()
            self._last_by_method[base] = self._last_call
            try:
                with urllib.request.urlopen(req, data, timeout=120) as r:
                    self.last_ratelimit = {k: v for k, v in r.headers.items()
                                           if k.lower().startswith("x-ratelimit")}
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read()[:200].decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    pass
                if e.code == 429:
                    retry_after = 0.0
                    try:
                        retry_after = float(e.headers.get("Retry-After")
                                            or e.headers.get("X-Ratelimit-Retry") or 0)
                    except Exception:  # noqa: BLE001
                        pass
                    if retry_after > self.SHORT_WAIT_MAX or attempt >= tries:
                        raise WBQuotaError(max(retry_after, 65.0), detail)
                    log.info("wb %s -> 429, short wait %.0fs (try %d)", base, retry_after or 65, attempt)
                    time.sleep((retry_after or 65.0) + 2.0)
                    continue
                if e.code in (500, 502, 503, 504) and attempt < tries:
                    time.sleep(20 * attempt)
                    continue
                raise RuntimeError(f"WB API {e.code}: {detail[:180]} url={base}") from e
            except Exception:  # noqa: BLE001 — network blip
                if attempt < tries:
                    time.sleep(min(20 * attempt, 60))
                    continue
                raise
        raise RuntimeError("unreachable")


def _num(v: Any) -> Any:
    return v if isinstance(v, (int, float)) else None


def _ts(v: Any) -> Any:
    return v or None


# ------------------------------------------------------------------ sync state
def _log_run(endpoint: str, rows: int, ok: bool, error: str | None = None,
             started: dt.datetime | None = None) -> None:
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO wb_sync_log (endpoint, started_at, finished_at, rows_upserted, ok, error) "
                        "VALUES (%s, %s, now(), %s, %s, %s)",
                        (endpoint, started or dt.datetime.now(dt.timezone.utc), rows, ok,
                         (error or "")[:500] or None),
                    )
                    cur.execute(
                        "INSERT INTO wb_sync_state (endpoint, last_run_at, status, note) VALUES (%s, now(), %s, %s) "
                        "ON CONFLICT (endpoint) DO UPDATE SET last_run_at=now(), status=EXCLUDED.status, note=EXCLUDED.note",
                        (endpoint, "ok" if ok else "error", (error or "")[:300] or None),
                    )
    except Exception:  # noqa: BLE001
        log.exception("wb sync log failed")


def _state_row(endpoint: str) -> dict[str, Any]:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM wb_sync_state WHERE endpoint=%s", (endpoint,))
            row = cur.fetchone()
            return dict(row) if row else {}


def _state_set(endpoint: str, **kv: Any) -> None:
    cols = ", ".join(f"{k}=%s" for k in kv)
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("INSERT INTO wb_sync_state (endpoint) VALUES (%s) ON CONFLICT (endpoint) DO NOTHING",
                            (endpoint,))
                cur.execute(f"UPDATE wb_sync_state SET {cols} WHERE endpoint=%s", (*kv.values(), endpoint))


def _blocked(endpoint: str) -> dt.datetime | None:
    bu = _state_row(endpoint).get("blocked_until")
    if bu and bu > dt.datetime.now(dt.timezone.utc):
        return bu
    return None


def _block(endpoint: str, seconds: float) -> dt.datetime:
    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds + 30)
    _state_set(endpoint, blocked_until=until)
    return until


# ------------------------------------------------------------------ sync steps
def sync_orders(client: WBClient, days_back: int | None = None) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    st = _state_row("orders")
    last = st.get("last_from")
    if days_back:
        date_from = (dt.date.today() - dt.timedelta(days=days_back)).isoformat()
    else:
        date_from = ((last - dt.timedelta(hours=1)).isoformat() if last
                     else (dt.date.today() - dt.timedelta(days=3)).isoformat())
    rows = client.call(f"{STAT}/api/v1/supplier/orders", {"dateFrom": date_from, "flag": 0}) or []
    n = 0
    max_lcd = None
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for r in rows:
                    lcd = r.get("lastChangeDate")
                    if lcd and (max_lcd is None or lcd > max_lcd):
                        max_lcd = lcd
                    cur.execute(
                        """
                        INSERT INTO wb_orders (srid, g_number, date, last_change_date, nm_id, barcode,
                            supplier_article, tech_size, brand, subject, category, warehouse_name,
                            warehouse_type, region_name, oblast, country, income_id, is_cancel, cancel_date,
                            total_price, discount_percent, spp, finished_price, price_with_disc, order_type,
                            sticker, raw)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (srid) DO UPDATE SET
                            last_change_date=EXCLUDED.last_change_date, is_cancel=EXCLUDED.is_cancel,
                            cancel_date=EXCLUDED.cancel_date, raw=EXCLUDED.raw, synced_at=now()
                        """,
                        (r.get("srid"), r.get("gNumber"), _ts(r.get("date")), _ts(lcd), r.get("nmId"),
                         r.get("barcode"), r.get("supplierArticle"), r.get("techSize"), r.get("brand"),
                         r.get("subject"), r.get("category"), r.get("warehouseName"), r.get("warehouseType"),
                         r.get("regionName"), r.get("oblastOkrugName"), r.get("countryName"), r.get("incomeID"),
                         bool(r.get("isCancel")),
                         _ts(r.get("cancelDate") if str(r.get("cancelDate", "")).startswith("2") else None),
                         _num(r.get("totalPrice")), _num(r.get("discountPercent")), _num(r.get("spp")),
                         _num(r.get("finishedPrice")), _num(r.get("priceWithDisc")), r.get("orderType"),
                         r.get("sticker"), json.dumps(r, ensure_ascii=False)),
                    )
                    n += 1
    if max_lcd:
        _state_set("orders", last_from=max_lcd)
    if days_back:
        _state_set("orders", done=True)
    _log_run("orders", n, True, started=started)
    return n


def sync_sales(client: WBClient, days_back: int | None = None) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    st = _state_row("sales")
    last = st.get("last_from")
    if days_back:
        date_from = (dt.date.today() - dt.timedelta(days=days_back)).isoformat()
    else:
        date_from = ((last - dt.timedelta(hours=1)).isoformat() if last
                     else (dt.date.today() - dt.timedelta(days=3)).isoformat())
    rows = client.call(f"{STAT}/api/v1/supplier/sales", {"dateFrom": date_from, "flag": 0}) or []
    n = 0
    max_lcd = None
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for r in rows:
                    lcd = r.get("lastChangeDate")
                    if lcd and (max_lcd is None or lcd > max_lcd):
                        max_lcd = lcd
                    sale_id = r.get("saleID") or ""
                    cur.execute(
                        """
                        INSERT INTO wb_sales (sale_id, srid, g_number, date, last_change_date, nm_id, barcode,
                            supplier_article, tech_size, brand, subject, category, warehouse_name, region_name,
                            oblast, country, is_return, total_price, discount_percent, spp, for_pay,
                            finished_price, price_with_disc, payment_sale_amount, order_type, raw)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (sale_id) DO UPDATE SET
                            last_change_date=EXCLUDED.last_change_date, for_pay=EXCLUDED.for_pay,
                            raw=EXCLUDED.raw, synced_at=now()
                        """,
                        (sale_id, r.get("srid"), r.get("gNumber"), _ts(r.get("date")), _ts(lcd), r.get("nmId"),
                         r.get("barcode"), r.get("supplierArticle"), r.get("techSize"), r.get("brand"),
                         r.get("subject"), r.get("category"), r.get("warehouseName"), r.get("regionName"),
                         r.get("oblastOkrugName"), r.get("countryName"), sale_id.startswith("R"),
                         _num(r.get("totalPrice")), _num(r.get("discountPercent")), _num(r.get("spp")),
                         _num(r.get("forPay")), _num(r.get("finishedPrice")), _num(r.get("priceWithDisc")),
                         _num(r.get("paymentSaleAmount")), r.get("orderType"), json.dumps(r, ensure_ascii=False)),
                    )
                    n += 1
    if max_lcd:
        _state_set("sales", last_from=max_lcd)
    if days_back:
        _state_set("sales", done=True)
    _log_run("sales", n, True, started=started)
    return n


def sync_stocks(client: WBClient) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    rows = client.call(f"{STAT}/api/v1/supplier/stocks",
                       {"dateFrom": (dt.date.today() - dt.timedelta(days=1)).isoformat()}) or []
    today = dt.date.today()
    n = 0
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for r in rows:
                    cur.execute(
                        """
                        INSERT INTO wb_stocks_daily (snapshot_date, nm_id, barcode, warehouse_name,
                            supplier_article, brand, subject, tech_size, quantity, in_way_to_client,
                            in_way_from_client, quantity_full, price, discount, raw)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (snapshot_date, nm_id, barcode, warehouse_name) DO UPDATE SET
                            quantity=EXCLUDED.quantity, in_way_to_client=EXCLUDED.in_way_to_client,
                            in_way_from_client=EXCLUDED.in_way_from_client, quantity_full=EXCLUDED.quantity_full,
                            price=EXCLUDED.price, discount=EXCLUDED.discount, raw=EXCLUDED.raw
                        """,
                        (today, r.get("nmId"), r.get("barcode") or "", r.get("warehouseName") or "",
                         r.get("supplierArticle"), r.get("brand"), r.get("subject"), r.get("techSize"),
                         r.get("quantity"), r.get("inWayToClient"), r.get("inWayFromClient"),
                         r.get("quantityFull"), _num(r.get("Price")), _num(r.get("Discount")),
                         json.dumps(r, ensure_ascii=False)),
                    )
                    n += 1
    _log_run("stocks", n, True, started=started)
    return n


def sync_cards(client: WBClient) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    n = 0
    cursor = {"limit": 100}
    while True:
        body = {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}}
        data = client.call(f"{CONTENT}/content/v2/get/cards/list", body=body) or {}
        cards = data.get("cards") or []
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    for c in cards:
                        photo = ""
                        ph = c.get("photos") or []
                        if ph and isinstance(ph, list):
                            photo = (ph[0] or {}).get("tm") or (ph[0] or {}).get("big") or ""
                        cur.execute(
                            """
                            INSERT INTO wb_cards (nm_id, imt_id, vendor_code, brand, title, subject_id,
                                subject_name, photo_url, raw, updated_at)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                            ON CONFLICT (nm_id) DO UPDATE SET vendor_code=EXCLUDED.vendor_code,
                                brand=EXCLUDED.brand, title=EXCLUDED.title, subject_name=EXCLUDED.subject_name,
                                photo_url=EXCLUDED.photo_url, raw=EXCLUDED.raw, updated_at=now()
                            """,
                            (c.get("nmID"), c.get("imtID"), c.get("vendorCode"), c.get("brand"), c.get("title"),
                             c.get("subjectID"), c.get("subjectName"), photo, json.dumps(c, ensure_ascii=False)),
                        )
                        n += 1
        cur_resp = data.get("cursor") or {}
        if len(cards) < cursor.get("limit", 100):
            break
        cursor = {"limit": 100, "updatedAt": cur_resp.get("updatedAt"), "nmID": cur_resp.get("nmID")}
    _log_run("cards", n, True, started=started)
    return n


def sync_prices(client: WBClient) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    n = 0
    offset = 0
    today = dt.date.today()
    while True:
        data = client.call(f"{PRICES}/api/v2/list/goods/filter", {"limit": 1000, "offset": offset}) or {}
        goods = ((data.get("data") or {}).get("listGoods")) or []
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    for g in goods:
                        for s in (g.get("sizes") or [{}]):
                            cur.execute(
                                """
                                INSERT INTO wb_prices_current (snapshot_date, nm_id, size_id, price,
                                    discounted_price, discount, club_discount, raw)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (snapshot_date, nm_id, size_id) DO UPDATE SET
                                    price=EXCLUDED.price, discounted_price=EXCLUDED.discounted_price,
                                    discount=EXCLUDED.discount, club_discount=EXCLUDED.club_discount, raw=EXCLUDED.raw
                                """,
                                (today, g.get("nmID"), s.get("sizeID") or 0, _num(s.get("price")),
                                 _num(s.get("discountedPrice")), _num(g.get("discount")),
                                 _num(g.get("clubDiscount")), json.dumps(g, ensure_ascii=False)),
                            )
                            n += 1
        if len(goods) < 1000:
            break
        offset += 1000
    _log_run("prices", n, True, started=started)
    return n


def _fin_insert(cur, r: dict) -> None:
    cur.execute(
        "INSERT INTO wb_finance_details (rrd_id, realizationreport_id, date_from, date_to, create_dt, rr_dt, "
        "nm_id, brand_name, subject_name, sa_name, ts_name, barcode, doc_type_name, supplier_oper_name, "
        "office_name, order_dt, sale_dt, quantity, retail_price, retail_amount, retail_price_withdisc_rub, "
        "ppvz_for_pay, delivery_rub, delivery_amount, return_amount, storage_fee, penalty, deduction, "
        "acquiring_fee, acquiring_percent, ppvz_sales_commission, commission_percent, ppvz_vw, ppvz_vw_nds, "
        "rebill_logistic_cost, raw) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (rrd_id) DO UPDATE SET raw=EXCLUDED.raw, synced_at=now()",
        (r.get("rrd_id"), r.get("realizationreport_id"), r.get("date_from"), r.get("date_to"),
         r.get("create_dt") or None, r.get("rr_dt") or None, r.get("nm_id"), r.get("brand_name"),
         r.get("subject_name"), r.get("sa_name"), r.get("ts_name"), r.get("barcode"),
         r.get("doc_type_name"), r.get("supplier_oper_name"), r.get("office_name"),
         _ts(r.get("order_dt")), _ts(r.get("sale_dt")), _num(r.get("quantity")),
         _num(r.get("retail_price")), _num(r.get("retail_amount")),
         _num(r.get("retail_price_withdisc_rub")), _num(r.get("ppvz_for_pay")),
         _num(r.get("delivery_rub")), _num(r.get("delivery_amount")), _num(r.get("return_amount")),
         _num(r.get("storage_fee")), _num(r.get("penalty")), _num(r.get("deduction")),
         _num(r.get("acquiring_fee")), _num(r.get("acquiring_percent")),
         _num(r.get("ppvz_sales_commission")), _num(r.get("commission_percent")),
         _num(r.get("ppvz_vw")), _num(r.get("ppvz_vw_nds")), _num(r.get("rebill_logistic_cost")),
         json.dumps(r, ensure_ascii=False)),
    )


def sync_finance_tick(client: WBClient, chunk_days: int = 7, max_chunks: int = 20) -> int:
    """Финансы: резюмируемый бэкфилл по курсору + хвост до сегодня. Каждый чанк фиксируется
    в состоянии сразу — обрыв в любом месте продолжится со следующего чанка."""
    st = _state_row("finance")
    today = dt.date.today()
    cursor = st.get("cursor_date") or (today - dt.timedelta(days=BACKFILL_DAYS))
    total = 0
    chunks = 0
    while cursor < today and chunks < max_chunks:
        started = dt.datetime.now(dt.timezone.utc)
        nxt = min(cursor + dt.timedelta(days=chunk_days), today)
        rrd = 0
        n = 0
        while True:
            rows = client.call(
                f"{STAT}/api/v5/supplier/reportDetailByPeriod",
                {"dateFrom": cursor.isoformat(), "dateTo": nxt.isoformat(), "limit": 100000, "rrdid": rrd},
            )
            if not rows:
                break
            with pg_connect() as conn:
                with conn.transaction():
                    with conn.cursor() as curq:
                        for r in rows:
                            _fin_insert(curq, r)
                            n += 1
            rrd = rows[-1].get("rrd_id") or 0
            if len(rows) < 100000 or not rrd:
                break
        _state_set("finance", cursor_date=nxt)
        _log_run(f"finance {cursor.isoformat()}..{nxt.isoformat()}", n, True, started=started)
        total += n
        cursor = nxt
        chunks += 1
    if cursor >= today:
        # хвост догнан: держим курсор в последних 10 днях, чтобы каждый тик обновлял свежие отчёты
        _state_set("finance", cursor_date=today - dt.timedelta(days=10), done=True)
    return total


def sync_adv_tick(client: WBClient) -> int:
    """Реклама: бэкфилл 30-дневными чанками по курсору, затем инкремент последних 31 дня."""
    st = _state_row("adv")
    today = dt.date.today()
    cursor = st.get("cursor_date") or (today - dt.timedelta(days=BACKFILL_DAYS))
    total = 0
    while cursor < today:
        started = dt.datetime.now(dt.timezone.utc)
        nxt = min(cursor + dt.timedelta(days=30), today)
        rows = client.call(f"{ADV}/adv/v1/upd", {"from": cursor.isoformat(), "to": nxt.isoformat()}) or []
        n = 0
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as curq:
                    for r in rows:
                        curq.execute(
                            "INSERT INTO wb_adv_costs (upd_num, upd_time, upd_sum, advert_id, campaign_name, "
                            "advert_type, payment_type, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                            "ON CONFLICT (upd_num, advert_id) DO UPDATE SET upd_sum=EXCLUDED.upd_sum, raw=EXCLUDED.raw",
                            (r.get("updNum"), _ts(r.get("updTime")), _num(r.get("updSum")), r.get("advertId"),
                             r.get("campName"), r.get("advertType"), str(r.get("paymentType") or ""),
                             json.dumps(r, ensure_ascii=False)),
                        )
                        n += 1
        _state_set("adv", cursor_date=nxt)
        _log_run(f"adv {cursor.isoformat()}..{nxt.isoformat()}", n, True, started=started)
        total += n
        cursor = nxt
    _state_set("adv", cursor_date=today - dt.timedelta(days=7), done=True)
    return total


# ------------------------------------------------------------------ tick runner
def run_tick() -> dict[str, Any]:
    """Один короткий проход по всем источникам с учётом квот. Вызывается таймером каждые 30 мин."""
    client = WBClient()
    out: dict[str, Any] = {}

    def _needs_history(table: str) -> bool:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) AS c FROM {table}")
                return (cur.fetchone() or {}).get("c", 0) == 0

    steps: list[tuple[str, Any]] = [
        ("cards", lambda: sync_cards(client)),
        ("prices", lambda: sync_prices(client)),
        ("stocks", lambda: sync_stocks(client)),
        ("orders", lambda: sync_orders(
            client, days_back=BACKFILL_DAYS if (not _state_row("orders").get("done") and _needs_history("wb_orders")) else None)),
        ("sales", lambda: sync_sales(
            client, days_back=BACKFILL_DAYS if (not _state_row("sales").get("done") and _needs_history("wb_sales")) else None)),
        ("adv", lambda: sync_adv_tick(client)),
        ("finance", lambda: sync_finance_tick(client)),
    ]
    for name, fn in steps:
        bu = _blocked(name)
        if bu:
            out[name] = f"quota until {bu.astimezone(dt.timezone(dt.timedelta(hours=3))).strftime('%d.%m %H:%M')}"
            continue
        try:
            out[name] = fn()
        except WBQuotaError as q:
            until = _block(name, q.retry_after)
            msk = until.astimezone(dt.timezone(dt.timedelta(hours=3))).strftime("%d.%m %H:%M")
            _log_run(name, 0, False, f"квота WB закрыта до {msk} МСК (Retry-After {q.retry_after:.0f}с)")
            out[name] = f"quota -> blocked until {msk}"
        except Exception as e:  # noqa: BLE001
            _log_run(name, 0, False, str(e)[:300])
            out[name] = f"ERROR: {str(e)[:160]}"
            log.exception("wb tick step %s failed", name)
    return out


# Обратная совместимость со старым entrypoint (wb_sync.py вызывает sync_all)
def sync_all(initial_days: int | None = None) -> dict[str, Any]:  # noqa: ARG001 — история в курсорах
    return run_tick()


# ------------------------------------------------------------------ queries (разделы UI)
def _brand_where(brand: str | None, col: str = "brand") -> tuple[str, list]:
    if brand and brand.strip() and brand.strip().lower() != "все":
        return f" AND {col} = %s", [brand.strip()]
    return "", []


def q_brands() -> list[str]:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT brand FROM wb_cards WHERE brand IS NOT NULL AND brand<>'' ORDER BY 1")
            return [r["brand"] for r in cur.fetchall()]


def q_summary(d_from: str, d_to: str, brand: str | None) -> dict[str, Any]:
    bw_o, bp = _brand_where(brand)
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*) FILTER (WHERE NOT is_cancel) AS orders_cnt,
                           COALESCE(sum(price_with_disc) FILTER (WHERE NOT is_cancel),0) AS orders_rub,
                           count(*) FILTER (WHERE is_cancel) AS cancels_cnt
                    FROM wb_orders WHERE date >= %s AND date < (%s::date + 1) {bw_o}""",
                [d_from, d_to, *bp])
            orders = dict(cur.fetchone())
            cur.execute(
                f"""SELECT count(*) FILTER (WHERE NOT is_return) AS sales_cnt,
                           COALESCE(sum(price_with_disc) FILTER (WHERE NOT is_return),0) AS sales_rub,
                           COALESCE(sum(for_pay) FILTER (WHERE NOT is_return),0) AS for_pay_rub,
                           count(*) FILTER (WHERE is_return) AS returns_cnt,
                           COALESCE(sum(price_with_disc) FILTER (WHERE is_return),0) AS returns_rub
                    FROM wb_sales WHERE date >= %s AND date < (%s::date + 1) {bw_o}""",
                [d_from, d_to, *bp])
            sales = dict(cur.fetchone())
            cur.execute(
                f"""SELECT COALESCE(sum(quantity),0) AS stock_qty, COALESCE(sum(in_way_to_client),0) AS in_way_to,
                           COALESCE(sum(in_way_from_client),0) AS in_way_from
                    FROM wb_stocks_daily WHERE snapshot_date = (SELECT max(snapshot_date) FROM wb_stocks_daily) {bw_o}""",
                bp)
            stocks = dict(cur.fetchone())
            cur.execute(
                f"""SELECT date_trunc('day', date)::date AS day,
                           count(*) FILTER (WHERE NOT is_cancel) AS orders_cnt,
                           COALESCE(sum(price_with_disc) FILTER (WHERE NOT is_cancel),0) AS orders_rub
                    FROM wb_orders WHERE date >= %s AND date < (%s::date + 1) {bw_o}
                    GROUP BY 1 ORDER BY 1""",
                [d_from, d_to, *bp])
            daily = [dict(r) for r in cur.fetchall()]
            cur.execute(
                f"""SELECT o.nm_id, max(o.supplier_article) AS article, max(c.title) AS title,
                           max(c.photo_url) AS photo, count(*) AS orders_cnt,
                           COALESCE(sum(o.price_with_disc),0) AS orders_rub
                    FROM wb_orders o LEFT JOIN wb_cards c ON c.nm_id = o.nm_id
                    WHERE o.date >= %s AND o.date < (%s::date + 1) AND NOT o.is_cancel {_brand_where(brand, 'o.brand')[0]}
                    GROUP BY o.nm_id ORDER BY orders_rub DESC LIMIT 10""",
                [d_from, d_to, *bp])
            top = [dict(r) for r in cur.fetchall()]
    return {"orders": orders, "sales": sales, "stocks": stocks, "daily": daily, "top_articles": top}


def q_articles(d_from: str, d_to: str, brand: str | None) -> list[dict[str, Any]]:
    """Таблица «По артикулам»: остаток, динамика остатков (14 дн), заказы, скорость, по дням."""
    bw, bp = _brand_where(brand, "c.brand")
    days = max(1, (dt.date.fromisoformat(d_to) - dt.date.fromisoformat(d_from)).days + 1)
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH ord AS (
                    SELECT nm_id, count(*) AS cnt, COALESCE(sum(price_with_disc),0) AS rub
                    FROM wb_orders WHERE date >= %s AND date < (%s::date + 1) AND NOT is_cancel GROUP BY nm_id),
                by_day AS (
                    SELECT nm_id, jsonb_object_agg(day, cnt) AS days FROM (
                        SELECT nm_id, date_trunc('day', date)::date::text AS day, count(*) AS cnt
                        FROM wb_orders WHERE date >= %s AND date < (%s::date + 1) AND NOT is_cancel
                        GROUP BY nm_id, 2) t GROUP BY nm_id),
                stock_now AS (
                    SELECT nm_id, sum(quantity) AS qty FROM wb_stocks_daily
                    WHERE snapshot_date = (SELECT max(snapshot_date) FROM wb_stocks_daily) GROUP BY nm_id),
                stock_hist AS (
                    SELECT nm_id, jsonb_agg(qty ORDER BY snapshot_date) AS spark FROM (
                        SELECT nm_id, snapshot_date, sum(quantity) AS qty FROM wb_stocks_daily
                        WHERE snapshot_date >= CURRENT_DATE - 14 GROUP BY nm_id, snapshot_date) t GROUP BY nm_id)
                SELECT c.nm_id, c.vendor_code, c.title, c.brand, c.subject_name, c.photo_url,
                       COALESCE(sn.qty,0) AS stock_qty, sh.spark AS stock_spark,
                       COALESCE(o.cnt,0) AS orders_cnt, COALESCE(o.rub,0) AS orders_rub,
                       round(COALESCE(o.cnt,0)::numeric / %s, 1) AS orders_per_day,
                       bd.days AS orders_by_day
                FROM wb_cards c
                LEFT JOIN ord o ON o.nm_id = c.nm_id
                LEFT JOIN by_day bd ON bd.nm_id = c.nm_id
                LEFT JOIN stock_now sn ON sn.nm_id = c.nm_id
                LEFT JOIN stock_hist sh ON sh.nm_id = c.nm_id
                WHERE 1=1 {bw}
                ORDER BY orders_rub DESC NULLS LAST
                """,
                [d_from, d_to, d_from, d_to, days, *bp])
            return [dict(r) for r in cur.fetchall()]


def q_finance_groups(d_from: str, d_to: str, brand: str | None) -> dict[str, Any]:
    """Базовые агрегаты финотчёта за период (по rr_dt) — сырьё для ОПиУ/ДДС/налогов."""
    bw, bp = _brand_where(brand, "brand_name")
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                  COALESCE(sum(retail_amount) FILTER (WHERE doc_type_name='Продажа'),0)  AS sales_retail,
                  COALESCE(sum(retail_amount) FILTER (WHERE doc_type_name='Возврат'),0)  AS returns_retail,
                  COALESCE(sum(retail_price_withdisc_rub) FILTER (WHERE doc_type_name='Продажа'),0) AS sales_withdisc,
                  COALESCE(sum(retail_price_withdisc_rub) FILTER (WHERE doc_type_name='Возврат'),0) AS returns_withdisc,
                  COALESCE(sum(ppvz_for_pay) FILTER (WHERE doc_type_name='Продажа'),0)   AS forpay_sales,
                  COALESCE(sum(ppvz_for_pay) FILTER (WHERE doc_type_name='Возврат'),0)   AS forpay_returns,
                  COALESCE(sum(quantity) FILTER (WHERE doc_type_name='Продажа'),0)       AS qty_sales,
                  COALESCE(sum(quantity) FILTER (WHERE doc_type_name='Возврат'),0)       AS qty_returns,
                  COALESCE(sum(delivery_rub),0)   AS logistics,
                  COALESCE(sum(storage_fee),0)    AS storage,
                  COALESCE(sum(penalty),0)        AS penalty,
                  COALESCE(sum(deduction),0)      AS deduction,
                  COALESCE(sum(acquiring_fee),0)  AS acquiring,
                  COALESCE(sum(retail_amount - ppvz_for_pay - delivery_rub) FILTER (WHERE doc_type_name='Продажа'),0) AS commission_est
                FROM wb_finance_details
                WHERE rr_dt >= %s AND rr_dt <= %s {bw}
                """,
                [d_from, d_to, *bp])
            g = dict(cur.fetchone())
            cur.execute(
                f"""SELECT COALESCE(sum(upd_sum),0) AS adv FROM wb_adv_costs
                    WHERE upd_time >= %s AND upd_time < (%s::date + 1)""",
                [d_from, d_to])
            g["adv"] = cur.fetchone()["adv"]
            # себестоимость проданного (по баркодам из финотчёта × wb_cost_prices)
            cur.execute(
                f"""SELECT COALESCE(sum(cp.cost * f.quantity),0) AS cogs,
                           count(DISTINCT f.barcode) FILTER (WHERE cp.barcode IS NULL) AS missing_barcodes
                    FROM wb_finance_details f LEFT JOIN wb_cost_prices cp ON cp.barcode = f.barcode
                    WHERE f.rr_dt >= %s AND f.rr_dt <= %s AND f.doc_type_name='Продажа' {bw}""",
                [d_from, d_to, *bp])
            row = dict(cur.fetchone())
            g["cogs"] = row["cogs"]
            g["cogs_missing_barcodes"] = row["missing_barcodes"]
    for k, v in list(g.items()):
        if v is not None and not isinstance(v, (int, str)):
            g[k] = float(v)
    return g


def q_pnl(d_from: str, d_to: str, brand: str | None) -> dict[str, Any]:
    """ОПиУ: помесячные строки + итог."""
    bw, bp = _brand_where(brand, "brand_name")
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT to_char(date_trunc('month', rr_dt), 'YYYY-MM') AS month,
                  COALESCE(sum(retail_amount) FILTER (WHERE doc_type_name='Продажа'),0)
                    - COALESCE(sum(retail_amount) FILTER (WHERE doc_type_name='Возврат'),0) AS revenue,
                  COALESCE(sum(ppvz_for_pay) FILTER (WHERE doc_type_name='Продажа'),0)
                    - COALESCE(sum(ppvz_for_pay) FILTER (WHERE doc_type_name='Возврат'),0) AS payout,
                  COALESCE(sum(delivery_rub),0) AS logistics,
                  COALESCE(sum(storage_fee),0) AS storage,
                  COALESCE(sum(penalty),0) + COALESCE(sum(deduction),0) AS penalties
                FROM wb_finance_details WHERE rr_dt >= %s AND rr_dt <= %s {bw}
                GROUP BY 1 ORDER BY 1
                """,
                [d_from, d_to, *bp])
            months = [dict(r) for r in cur.fetchall()]
    g = q_finance_groups(d_from, d_to, brand)
    commission = g["sales_retail"] - g["returns_retail"] - (g["forpay_sales"] - g["forpay_returns"]) - g["logistics"]
    pnl = {
        "revenue": g["sales_retail"] - g["returns_retail"],
        "commission": commission,
        "logistics": g["logistics"],
        "storage": g["storage"],
        "penalties": g["penalty"] + g["deduction"],
        "adv": g["adv"],
        "cogs": g["cogs"],
        "cogs_missing_barcodes": g["cogs_missing_barcodes"],
    }
    pnl["operating_profit"] = (pnl["revenue"] - pnl["commission"] - pnl["logistics"] - pnl["storage"]
                               - pnl["penalties"] - pnl["adv"] - pnl["cogs"])
    for m in months:
        for k, v in list(m.items()):
            if v is not None and not isinstance(v, (int, str)):
                m[k] = float(v)
    return {"months": months, "total": pnl}


def q_cashflow(d_from: str, d_to: str, brand: str | None) -> list[dict[str, Any]]:
    """ДДС: понедельные отчёты WB — поступления/удержания по realizationreport_id."""
    bw, bp = _brand_where(brand, "brand_name")
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT realizationreport_id, min(date_from) AS date_from, max(date_to) AS date_to,
                  COALESCE(sum(ppvz_for_pay) FILTER (WHERE doc_type_name='Продажа'),0)
                    - COALESCE(sum(ppvz_for_pay) FILTER (WHERE doc_type_name='Возврат'),0) AS payout,
                  COALESCE(sum(delivery_rub),0) AS logistics,
                  COALESCE(sum(storage_fee),0) AS storage,
                  COALESCE(sum(penalty),0) + COALESCE(sum(deduction),0) AS deductions,
                  COALESCE(sum(ppvz_for_pay) FILTER (WHERE doc_type_name='Продажа'),0)
                    - COALESCE(sum(ppvz_for_pay) FILTER (WHERE doc_type_name='Возврат'),0)
                    - COALESCE(sum(delivery_rub),0) - COALESCE(sum(storage_fee),0)
                    - COALESCE(sum(penalty),0) - COALESCE(sum(deduction),0) AS net_to_account
                FROM wb_finance_details WHERE rr_dt >= %s AND rr_dt <= %s {bw}
                GROUP BY realizationreport_id ORDER BY min(date_from)
                """,
                [d_from, d_to, *bp])
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        for k, v in list(r.items()):
            if v is not None and not isinstance(v, (int, str)) and k != "realizationreport_id":
                r[k] = float(v) if not isinstance(v, dt.date) else v.isoformat()
    return rows


def q_rnp(d_from: str, d_to: str, brand: str | None) -> list[dict[str, Any]]:
    """РНП «Рука на пульсе»: день к дню. Расширяемо: добавляй колонки в этот запрос."""
    bw, bp = _brand_where(brand)
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH o AS (SELECT date_trunc('day', date)::date AS day, count(*) AS cnt,
                                  COALESCE(sum(price_with_disc),0) AS rub
                           FROM wb_orders WHERE NOT is_cancel AND date >= %s AND date < (%s::date+1) {bw} GROUP BY 1),
                     s AS (SELECT date_trunc('day', date)::date AS day,
                                  count(*) FILTER (WHERE NOT is_return) AS cnt,
                                  COALESCE(sum(price_with_disc) FILTER (WHERE NOT is_return),0) AS rub,
                                  count(*) FILTER (WHERE is_return) AS ret_cnt
                           FROM wb_sales WHERE date >= %s AND date < (%s::date+1) {bw} GROUP BY 1),
                     st AS (SELECT snapshot_date AS day, sum(quantity) AS qty
                            FROM wb_stocks_daily WHERE snapshot_date >= %s::date AND snapshot_date <= %s::date
                            {bw} GROUP BY 1),
                     a AS (SELECT date_trunc('day', upd_time)::date AS day, COALESCE(sum(upd_sum),0) AS adv
                           FROM wb_adv_costs WHERE upd_time >= %s AND upd_time < (%s::date+1) GROUP BY 1)
                SELECT d.day::date AS day,
                       COALESCE(o.cnt,0) AS orders_cnt, COALESCE(o.rub,0) AS orders_rub,
                       COALESCE(s.cnt,0) AS sales_cnt, COALESCE(s.rub,0) AS sales_rub,
                       COALESCE(s.ret_cnt,0) AS returns_cnt,
                       COALESCE(st.qty,0) AS stock_qty, COALESCE(a.adv,0) AS adv_rub,
                       CASE WHEN COALESCE(o.rub,0) > 0 THEN round(100*COALESCE(a.adv,0)/o.rub, 1) ELSE 0 END AS drr_pct
                FROM (SELECT generate_series(%s::date, %s::date, '1 day')::date AS day) d
                LEFT JOIN o ON o.day = d.day LEFT JOIN s ON s.day = d.day
                LEFT JOIN st ON st.day = d.day LEFT JOIN a ON a.day = d.day
                ORDER BY d.day
                """,
                [d_from, d_to, *bp, d_from, d_to, *bp, d_from, d_to, *bp, d_from, d_to, d_from, d_to])
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["day"] = r["day"].isoformat()
        for k, v in list(r.items()):
            if v is not None and not isinstance(v, (int, str)):
                r[k] = float(v)
    return rows


# ------------------------------------------------------------------ tax calculator
TAX_MODES = {
    "usn_d":   {"label": "УСН Доходы", "default_rate": 6.0, "base": "income"},
    "usn_dr":  {"label": "УСН Доходы-Расходы", "default_rate": 15.0, "base": "income_minus_expenses"},
    "ausn_d":  {"label": "АУСН Доходы", "default_rate": 8.0, "base": "income"},
    "ausn_dr": {"label": "АУСН Доходы-Расходы", "default_rate": 20.0, "base": "income_minus_expenses"},
    "sng":     {"label": "Страны СНГ", "default_rate": 0.0, "base": "income"},
}
VAT_MODES = {"none": 0.0, "vat5": 5.0, "vat7": 7.0, "vat20": 20.0}


def q_tax(d_from: str, d_to: str, brand: str | None, mode: str, rate: float | None,
          vat_mode: str = "none", vat_refund_percent: float = 0.0) -> dict[str, Any]:
    """Налоговый калькулятор. Прозрачность: каждая строка расчёта в ответе.
    Реализация до СПП = retail_amount(Продажи−Возвраты); после СПП = сумма фактической
    оплаты покупателем (retail_price_withdisc_rub − СПП уже вычтен WB в forPay-механике) —
    для налоговой базы УСН используется реализация ПОСЛЕ СПП (позиция ФНС: доход продавца
    = стоимость реализации, применённая WB), режим АУСН/СНГ — та же база, своя ставка."""
    m = TAX_MODES.get(mode) or TAX_MODES["usn_d"]
    rate = float(rate if rate is not None else m["default_rate"])
    vat_rate = VAT_MODES.get(vat_mode, 0.0)
    g = q_finance_groups(d_from, d_to, brand)

    realization_before_spp = g["sales_retail"] - g["returns_retail"]
    realization_after_spp = g["sales_withdisc"] - g["returns_withdisc"]
    payout = g["forpay_sales"] - g["forpay_returns"]
    commission = realization_before_spp - payout - g["logistics"]
    services_other = g["penalty"] + g["deduction"] + g["acquiring"]
    services_total = commission + g["logistics"] + g["adv"] + services_other + g["storage"]

    income_base = realization_after_spp
    expenses = services_total + g["cogs"]
    if m["base"] == "income":
        tax_base = income_base
    else:
        tax_base = max(0.0, income_base - expenses)
    tax = round(tax_base * rate / 100.0, 2)
    vat = round(income_base * vat_rate / (100.0 + vat_rate), 2) if vat_rate else 0.0
    vat_refund = round(vat * float(vat_refund_percent or 0) / 100.0, 2)
    operating_profit = round(income_base - services_total - g["cogs"] - tax - vat + vat_refund, 2)

    return {
        "mode": mode, "mode_label": m["label"], "rate": rate,
        "vat_mode": vat_mode, "vat_rate": vat_rate, "vat_refund_percent": vat_refund_percent,
        "realization": {"before_spp": round(realization_before_spp, 2),
                        "after_spp": round(realization_after_spp, 2)},
        "services": {"commission": round(commission, 2), "logistics": round(g["logistics"], 2),
                     "adv": round(g["adv"], 2), "storage": round(g["storage"], 2),
                     "other": round(services_other, 2), "total": round(services_total, 2)},
        "taxes_and_costs": {"tax_base": round(tax_base, 2), "tax": tax, "vat": vat,
                            "vat_refund": vat_refund, "cogs": round(g["cogs"], 2),
                            "cogs_missing_barcodes": g["cogs_missing_barcodes"]},
        "payout_from_wb": round(payout, 2),
        "operating_profit": operating_profit,
        "modes_available": {k: v["label"] for k, v in TAX_MODES.items()},
        "vat_modes_available": list(VAT_MODES.keys()),
    }


# ------------------------------------------------------------------ Flask API
def _args() -> tuple[str, str, str | None]:
    from flask import request
    today = dt.date.today()
    d_from = request.args.get("from") or (today - dt.timedelta(days=29)).isoformat()
    d_to = request.args.get("to") or today.isoformat()
    brand = request.args.get("brand") or None
    return d_from, d_to, brand


@app.route("/api/wb-cab/brands")
def wbcab_brands():
    from flask import jsonify
    return jsonify({"brands": q_brands()})


@app.route("/api/wb-cab/summary")
def wbcab_summary():
    from flask import jsonify
    d_from, d_to, brand = _args()
    data = q_summary(d_from, d_to, brand)
    for sec in ("orders", "sales", "stocks"):
        for k, v in list(data[sec].items()):
            if v is not None and not isinstance(v, (int, str)):
                data[sec][k] = float(v)
    for d in data["daily"]:
        d["day"] = d["day"].isoformat()
        d["orders_rub"] = float(d["orders_rub"])
    for t in data["top_articles"]:
        t["orders_rub"] = float(t["orders_rub"])
    return jsonify({"from": d_from, "to": d_to, "brand": brand, **data})


@app.route("/api/wb-cab/articles")
def wbcab_articles():
    from flask import jsonify
    d_from, d_to, brand = _args()
    rows = q_articles(d_from, d_to, brand)
    for r in rows:
        for k, v in list(r.items()):
            if v is not None and not isinstance(v, (int, str, list, dict, bool)):
                r[k] = float(v)
    return jsonify({"from": d_from, "to": d_to, "brand": brand, "articles": rows})


@app.route("/api/wb-cab/pnl")
def wbcab_pnl():
    from flask import jsonify
    d_from, d_to, brand = _args()
    return jsonify({"from": d_from, "to": d_to, "brand": brand, **q_pnl(d_from, d_to, brand)})


@app.route("/api/wb-cab/cashflow")
def wbcab_cashflow():
    from flask import jsonify
    d_from, d_to, brand = _args()
    return jsonify({"from": d_from, "to": d_to, "brand": brand, "reports": q_cashflow(d_from, d_to, brand)})


@app.route("/api/wb-cab/rnp")
def wbcab_rnp():
    from flask import jsonify
    d_from, d_to, brand = _args()
    return jsonify({"from": d_from, "to": d_to, "brand": brand, "days": q_rnp(d_from, d_to, brand)})


@app.route("/api/wb-cab/tax")
def wbcab_tax():
    from flask import jsonify, request
    d_from, d_to, brand = _args()
    mode = request.args.get("mode") or "usn_d"
    rate = request.args.get("rate", type=float)
    vat_mode = request.args.get("vat_mode") or "none"
    vat_refund = request.args.get("vat_refund", type=float) or 0.0
    return jsonify({"from": d_from, "to": d_to, "brand": brand,
                    **q_tax(d_from, d_to, brand, mode, rate, vat_mode, vat_refund)})


@app.route("/api/wb-cab/sync-status")
def wbcab_sync_status():
    from flask import jsonify
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT endpoint, last_run_at, status, note FROM wb_sync_state ORDER BY endpoint")
            state = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT (SELECT count(*) FROM wb_orders) AS orders,
                                  (SELECT count(*) FROM wb_sales) AS sales,
                                  (SELECT count(*) FROM wb_finance_details) AS finance,
                                  (SELECT count(*) FROM wb_cards) AS cards,
                                  (SELECT count(*) FROM wb_stocks_daily) AS stocks,
                                  (SELECT count(*) FROM wb_adv_costs) AS adv,
                                  (SELECT count(*) FROM wb_prices_current) AS prices""")
            counts = dict(cur.fetchone())
            cur.execute("""SELECT 'orders' AS src, min(date)::date::text AS d_min, max(date)::date::text AS d_max FROM wb_orders
                           UNION ALL SELECT 'sales', min(date)::date::text, max(date)::date::text FROM wb_sales
                           UNION ALL SELECT 'finance', min(rr_dt)::text, max(rr_dt)::text FROM wb_finance_details
                           UNION ALL SELECT 'stocks', min(snapshot_date)::text, max(snapshot_date)::text FROM wb_stocks_daily
                           UNION ALL SELECT 'adv', min(upd_time)::date::text, max(upd_time)::date::text FROM wb_adv_costs""")
            ranges = {r["src"]: {"min": r["d_min"], "max": r["d_max"]} for r in cur.fetchall()}
            cur.execute("""SELECT endpoint, started_at, finished_at, rows_upserted, ok, left(coalesce(error,''), 160) AS error
                           FROM wb_sync_log ORDER BY id DESC LIMIT 12""")
            recent = [dict(r) for r in cur.fetchall()]
            # pg_try_advisory_lock(bigint): classid = high 32 bits (0 here), objid = low 32 bits
            cur.execute("SELECT count(*) > 0 AS running FROM pg_locks "
                        "WHERE locktype='advisory' AND classid = 0 AND objid = 984312077")
            running = bool(cur.fetchone()["running"])
    for s in state:
        if s.get("last_run_at"):
            s["last_run_at"] = s["last_run_at"].isoformat()
    for r in recent:
        for k in ("started_at", "finished_at"):
            if r.get(k):
                r[k] = r[k].isoformat()
    blocked = {}
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT endpoint, blocked_until, cursor_date, done FROM wb_sync_state "
                        "WHERE blocked_until IS NOT NULL AND blocked_until > now()")
            for r in cur.fetchall():
                blocked[r["endpoint"]] = r["blocked_until"].astimezone(
                    dt.timezone(dt.timedelta(hours=3))).strftime("%d.%m %H:%M")
    return jsonify({"counts": counts, "ranges": ranges, "recent_runs": recent,
                    "sync_running": running, "blocked": blocked})


@app.route("/api/wb-cab/cost-prices", methods=["GET", "POST"])
def wbcab_cost_prices():
    """Болванка под себестоимость: GET — список+шаблон колонок; POST — upsert строк
    [{barcode, cost, vendor_code?}] (импорт Excel будет поверх этого же эндпоинта)."""
    from flask import jsonify, request
    if request.method == "POST":
        rows = (request.get_json(silent=True) or {}).get("rows") or []
        n = 0
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    for r in rows:
                        bc = str(r.get("barcode") or "").strip()
                        cost = r.get("cost")
                        if not bc or cost is None:
                            continue
                        cur.execute(
                            """INSERT INTO wb_cost_prices (barcode, nm_id, vendor_code, cost, source, updated_at)
                               VALUES (%s,%s,%s,%s,%s,now())
                               ON CONFLICT (barcode) DO UPDATE SET cost=EXCLUDED.cost,
                                   vendor_code=COALESCE(EXCLUDED.vendor_code, wb_cost_prices.vendor_code),
                                   source=EXCLUDED.source, updated_at=now()""",
                            (bc, r.get("nm_id"), r.get("vendor_code"), cost, r.get("source") or "api"))
                        n += 1
        return jsonify({"upserted": n})
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT barcode, nm_id, vendor_code, cost, valid_from, source FROM wb_cost_prices ORDER BY vendor_code NULLS LAST, barcode LIMIT 2000")
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["cost"] = float(r["cost"])
        r["valid_from"] = r["valid_from"].isoformat() if r.get("valid_from") else None
    return jsonify({"template_columns": ["barcode", "vendor_code", "cost"],
                    "note": "Импорт Excel по шаблону будет загружаться сюда же (POST rows).",
                    "rows": rows})


@app.route("/api/wb-cab/tax-settings", methods=["GET", "POST"])
def wbcab_tax_settings():
    from flask import jsonify, request
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        mode = str(b.get("mode") or "usn_d")
        if mode not in TAX_MODES:
            return jsonify({"error": f"mode must be one of {list(TAX_MODES)}"}), 400
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO wb_tax_settings (mode, rate, vat_mode, vat_refund_percent, effective_from) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (mode, b.get("rate") or TAX_MODES[mode]["default_rate"],
                         str(b.get("vat_mode") or "none"), b.get("vat_refund_percent") or 0,
                         b.get("effective_from") or dt.date.today().isoformat()))
        return jsonify({"saved": True})
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT mode, rate, vat_mode, vat_refund_percent, effective_from FROM wb_tax_settings "
                        "WHERE effective_from <= CURRENT_DATE ORDER BY effective_from DESC, id DESC LIMIT 1")
            row = cur.fetchone()
    cur_settings = dict(row) if row else {"mode": "usn_d", "rate": 6.0, "vat_mode": "none", "vat_refund_percent": 0}
    if cur_settings.get("rate") is not None:
        cur_settings["rate"] = float(cur_settings["rate"])
    if cur_settings.get("vat_refund_percent") is not None:
        cur_settings["vat_refund_percent"] = float(cur_settings["vat_refund_percent"])
    if cur_settings.get("effective_from") and not isinstance(cur_settings["effective_from"], str):
        cur_settings["effective_from"] = cur_settings["effective_from"].isoformat()
    return jsonify({"current": cur_settings, "modes": {k: v["label"] for k, v in TAX_MODES.items()},
                    "vat_modes": list(VAT_MODES.keys())})


@app.route("/api/wb-cab/cards")
def wbcab_cards():
    """Lightweight catalogue for the RNP article picker: our real WB cards (nm_id, vendor_code,
    title, brand, photo). Optional q (vendor_code/title/nm_id ILIKE) and brand filters."""
    from flask import jsonify, request
    q = (request.args.get("q") or "").strip()
    brand = (request.args.get("brand") or "").strip()
    where = ["1=1"]
    params: list = []
    if q:
        where.append("(vendor_code ILIKE %s OR title ILIKE %s OR nm_id::text ILIKE %s)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if brand and brand.lower() not in ("", "все"):
        where.append("brand = %s")
        params.append(brand)
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nm_id, vendor_code, title, brand, subject_name, photo_url "
                "FROM wb_cards WHERE " + " AND ".join(where) +
                " ORDER BY vendor_code NULLS LAST, nm_id LIMIT 100",
                params)
            rows = [dict(r) for r in cur.fetchall()]
    return jsonify({"cards": rows, "total": len(rows)})


log.info("wb_cabinet loaded: /api/wb-cab/* routes registered")
