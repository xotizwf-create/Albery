import datetime as dt
from decimal import Decimal

from scripts import ensure_postgres
import wb_cabinet
from wb_cabinet import (
    _ensure_api_version,
    _fin_insert,
    _finance_v2_row,
    _history_page_complete,
    _jwt_claims,
    _num,
    _paid_storage_row,
    _range_complete,
    _stock_report_rows,
    q_rnp,
    q_tax,
    sync_finance_tick,
    sync_stocks,
    wbcab_cards,
)


def test_current_finance_payload_maps_money_strings_and_contract_fields():
    source = {
        "rrdId": 101,
        "reportId": 202,
        "nmId": 303,
        "vendorCode": "sku-1",
        "sku": "4600000000001",
        "retailAmount": "367.10",
        "retailPriceWithDisc": "399.68",
        "forPay": "376.99",
        "deliveryService": "8.50",
        "paidStorage": "12.25",
        "paidAcceptance": "4.75",
        "cashbackCommissionChange": "1.25",
        "sellerOperName": "Продажа",
    }

    row = _finance_v2_row(source)

    assert row["rrd_id"] == 101
    assert row["realizationreport_id"] == 202
    assert row["sa_name"] == "sku-1"
    assert row["barcode"] == "4600000000001"
    assert row["retail_price_withdisc_rub"] == "399.68"
    assert row["delivery_rub"] == "8.50"
    assert row["storage_fee"] == "12.25"
    assert row["paid_acceptance"] == "4.75"
    assert row["cashback_commission_change"] == "1.25"
    assert row["_raw"] is source
    assert _num(row["ppvz_for_pay"]) == Decimal("376.99")
    assert _num("not-a-number") is None


def test_finance_upsert_persists_all_current_money_fields():
    captured = {}

    class Cursor:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

    _fin_insert(Cursor(), _finance_v2_row({
        "rrdId": 101,
        "paidAcceptance": "4.75",
        "additionalPayment": "2.25",
        "cashbackCommissionChange": "1.25",
    }))

    assert "paid_acceptance" in captured["sql"]
    assert "additional_payment=EXCLUDED.additional_payment" in captured["sql"]
    assert "cashback_commission_change=EXCLUDED.cashback_commission_change" in captured["sql"]
    assert captured["sql"].count("%s") == len(captured["params"])


def test_warehouse_report_flattens_transit_and_skips_aggregate_total():
    report = [{
        "brand": "Allberi",
        "subjectName": "Пижамы",
        "vendorCode": "sku-2",
        "nmId": 404,
        "barcode": "4600000000002",
        "techSize": "M",
        "warehouses": [
            {"warehouseName": "В пути до получателей", "quantity": 3},
            {"warehouseName": "В пути возвраты на склад WB", "quantity": 2},
            {"warehouseName": "Всего находится на складах", "quantity": 10},
            {"warehouseName": "Коледино", "quantity": 10},
        ],
    }]

    rows = _stock_report_rows(report)

    assert len(rows) == 3
    by_name = {row["warehouse_name"]: row for row in rows}
    assert by_name["Коледино"]["quantity"] == 10
    assert by_name["В пути до получателей"]["quantity"] == 0
    assert by_name["В пути до получателей"]["in_way_to_client"] == 3
    assert by_name["В пути возвраты на склад WB"]["in_way_from_client"] == 2
    assert "Всего находится на складах" not in by_name


def test_paid_storage_payload_maps_report_amount():
    source = {
        "date": "2026-07-01",
        "nmId": 505,
        "barcode": "4600000000003",
        "warehouse": "Коледино",
        "vendorCode": "sku-3",
        "warehouseCoef": 1.7,
        "warehousePrice": 7.65,
    }

    row = _paid_storage_row(source)

    assert row["nm_id"] == 505
    assert row["warehouse_coef"] == 1.7
    assert row["amount"] == 7.65
    assert row["_raw"] is source


def test_wb_state_migrations_are_always_applied():
    assert "054_wb_sync_state_v2.sql" in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    assert "055_wb_async_reports.sql" in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    assert "056_wb_finance_pagination.sql" in ensure_postgres.ALWAYS_APPLY_MIGRATIONS


def test_token_metadata_and_history_page_completion_are_explicit():
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps({"acc": 1, "t": False}).encode()).decode().rstrip("=")
    assert _jwt_claims(f"header.{payload}.signature")["acc"] == 1
    assert _jwt_claims("not-a-jwt") == {}
    assert _history_page_complete([{}] * 79999) is True
    assert _history_page_complete([{}] * 80000) is False


def test_coverage_is_complete_only_when_it_contains_selected_period():
    assert _range_complete("2026-01-17", "2026-07-17", "2026-01-17", "2026-07-17", True)
    assert not _range_complete("2026-01-18", "2026-02-18", "2026-01-17", "2026-07-17", True)
    assert not _range_complete("2026-01-17", "2026-07-17", "2026-01-17", "2026-07-17", False)


def test_finance_backfill_uses_one_full_range_and_persists_rrd_cursor(monkeypatch, fake_pg):
    calls = []
    writes = []

    class Client:
        token_access = 1

        def call(self, url, body=None, **kwargs):
            calls.append((url, body, kwargs))
            return [{"rrdId": 987, "reportId": 1}]

    monkeypatch.setattr(wb_cabinet, "_state_row", lambda endpoint: {})
    monkeypatch.setattr(wb_cabinet, "_state_set", lambda endpoint, **values: writes.append((endpoint, values)))
    monkeypatch.setattr(wb_cabinet, "_log_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(wb_cabinet, "_fin_insert", lambda cursor, row: None)
    fake_pg(wb_cabinet, lambda sql, params: [])

    assert sync_finance_tick(Client(), max_pages=1) == 1
    body = calls[0][1]
    assert (dt.date.fromisoformat(body["dateTo"]) - dt.date.fromisoformat(body["dateFrom"])).days == wb_cabinet.BACKFILL_DAYS
    assert body["limit"] == 100000
    assert body["rrdId"] == 0
    assert writes[-1][1]["page_cursor"] == 987
    assert writes[-1][1]["done"] is False


def test_dashboard_does_not_publish_profit_without_finance_and_costs(monkeypatch):
    monkeypatch.setattr(wb_cabinet, "q_finance_groups", lambda *args: {
        "sales_retail": 0.0, "returns_retail": 0.0,
        "sales_withdisc": 0.0, "returns_withdisc": 0.0,
        "forpay_sales": 0.0, "forpay_returns": 0.0,
        "commission_actual": 0.0, "logistics": 0.0, "adv": 500.0,
        "penalty": 0.0, "deduction": 0.0, "acquiring": 0.0, "storage": 0.0,
        "rebill_logistics": 0.0, "paid_acceptance": 0.0, "additional_payment": 0.0,
        "installment_cofinancing": 0.0, "cashback_commission": 0.0,
        "cogs": 0.0, "cogs_missing_barcodes": 7,
        "finance_rows": 0, "finance_done": False, "finance_min": None, "finance_max": None,
        "finance_status": "partial", "finance_blocked_until": None, "adv_allocated": True,
    })
    monkeypatch.setattr(wb_cabinet, "q_finance_daily", lambda *args: [])

    result = q_tax("2026-01-17", "2026-07-17", None, "usn_d", None)

    assert result["operating_profit"] is None
    assert result["quality"]["finance_ready"] is False
    assert result["quality"]["profit_ready"] is False


def test_api_version_upgrade_clears_retired_quota_and_report_task(monkeypatch):
    writes = []
    monkeypatch.setattr(wb_cabinet, "_state_row", lambda endpoint: {"api_version": 1})
    monkeypatch.setattr(wb_cabinet, "_state_set", lambda endpoint, **values: writes.append((endpoint, values)))

    _ensure_api_version("stocks", 2)

    assert writes == [("stocks", {
        "api_version": 2,
        "blocked_until": None,
        "status": "pending",
        "note": None,
        "task_id": None,
        "task_date_from": None,
        "task_date_to": None,
        "task_started_at": None,
    })]


def test_stock_sync_queues_resumable_report(monkeypatch):
    writes = []
    logs = []

    class Client:
        def call(self, url, params=None, **kwargs):
            assert url.endswith("/api/v1/warehouse_remains")
            assert params["groupByNm"] == "true"
            assert params["groupByBarcode"] == "true"
            return {"data": {"taskId": "task-1"}}

    monkeypatch.setattr(wb_cabinet, "_state_row", lambda endpoint: {})
    monkeypatch.setattr(wb_cabinet, "_state_set", lambda endpoint, **values: writes.append((endpoint, values)))
    monkeypatch.setattr(wb_cabinet, "_log_run", lambda *args, **kwargs: logs.append((args, kwargs)))

    assert sync_stocks(Client()) == 0
    assert writes[0][0] == "stocks"
    assert writes[0][1]["task_id"] == "task-1"
    assert writes[0][1]["status"] == "queued"
    assert logs


def test_rnp_selected_article_keeps_calendar_row_and_never_mixes_cabinet_ad_spend(fake_pg):
    def responder(sql, params):
        if "generate_series" not in sql:
            return []
        return [{
            "day": dt.date(2026, 7, 16),
            "orders_cnt": 2,
            "orders_rub": Decimal("1200.50"),
            "sales_cnt": 1,
            "sales_rub": Decimal("650.25"),
            "returns_cnt": 0,
            "for_pay_rub": Decimal("500.10"),
            "cogs_rub": Decimal("200.00"),
            "stock_qty": 7,
            "adv_rub": Decimal("0"),
            "drr_pct": Decimal("0"),
        }]

    cursor = fake_pg(wb_cabinet, responder)
    rows = q_rnp("2026-07-16", "2026-07-16", "Allberi", 12345)

    sql, params = cursor.executed[-1]
    assert "s.nm_id = %s" in sql
    assert "SELECT NULL::date AS day, 0::numeric AS adv WHERE FALSE" in sql
    assert "FROM wb_adv_costs" not in sql
    assert params.count(12345) == 3
    assert rows == [{
        "day": "2026-07-16",
        "orders_cnt": 2,
        "orders_rub": 1200.5,
        "sales_cnt": 1,
        "sales_rub": 650.25,
        "returns_cnt": 0,
        "for_pay_rub": 500.1,
        "cogs_rub": 200.0,
        "stock_qty": 7,
        "adv_rub": 0.0,
        "drr_pct": 0.0,
    }]


def test_card_search_covers_wb_article_supplier_article_name_and_subject(fake_pg):
    cursor = fake_pg(wb_cabinet, lambda sql, params: [])

    with wb_cabinet.app.test_request_context("/api/wb-cab/cards?q=12345"):
        response = wbcab_cards()

    sql, params = cursor.executed[-1]
    assert response.get_json() == {"cards": [], "total": 0}
    assert "nm_id::text ILIKE %s" in sql
    assert "vendor_code ILIKE %s" in sql
    assert "title ILIKE %s" in sql
    assert "subject_name ILIKE %s" in sql
    assert params[:4] == ["%12345%"] * 4
