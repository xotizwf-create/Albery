from decimal import Decimal

from scripts import ensure_postgres
import wb_cabinet
from wb_cabinet import (
    _ensure_api_version,
    _finance_v2_row,
    _num,
    _paid_storage_row,
    _stock_report_rows,
    sync_stocks,
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
    assert row["_raw"] is source
    assert _num(row["ppvz_for_pay"]) == Decimal("376.99")
    assert _num("not-a-number") is None


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
