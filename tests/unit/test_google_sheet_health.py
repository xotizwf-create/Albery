"""Проверка таблицы ловит логические дефекты, которые агент о себе не сообщает.

17.08.2026 владелец: «агент жёстко тупит и делает неправильные решения в таблице, не
перепроверяет логику». Разбор на проде: агент собрал таблицу «Расходы и доходы» —
выпадающие списки, фильтр, условное форматирование, синтаксически верные SUMIF — и
оставил единственную строку данных с ПУСТЫМ «Типом». Сводка считает SUMIF по «Типу»,
поэтому показала 0,00 ₽ доходов и расходов при живой записи на 232 ₽. Отчёт агента:
«Таблицу доработал и проверил».

Проверять свои ДЕЙСТВИЯ («значения записаны») недостаточно — нужно проверять РЕЗУЛЬТАТ
(«таблица осмысленна»). Промптом это не лечится, поэтому проверка детерминированная.
Данные ниже — дословно с той таблицы.
"""
from __future__ import annotations

import pytest

import app


class _FakeValues:
    def __init__(self, grids: dict[str, list[list]]):
        self._grids = grids
        self._pending: dict | None = None

    def batchGet(self, spreadsheetId, ranges, valueRenderOption):  # noqa: N803
        titles = [r.split("'")[1] for r in ranges]
        key = "formula" if valueRenderOption == "FORMULA" else "shown"
        self._pending = {"valueRanges": [{"values": self._grids[key].get(t, [])} for t in titles]}
        return self

    def execute(self):
        return self._pending


class _FakeSpreadsheets:
    def __init__(self, titles: list[str], grids: dict[str, dict]):
        self._titles = titles
        self._values = _FakeValues(grids)
        self._pending: dict | None = None

    def get(self, spreadsheetId, fields=None, **kw):  # noqa: N803
        self._pending = {
            "properties": {"title": "Расходы и доходы"},
            "sheets": [{"properties": {"title": t, "gridProperties": {"rowCount": 1000,
                                                                     "columnCount": 26}}}
                       for t in self._titles],
        }
        return self

    def execute(self):
        return self._pending

    def values(self):
        return self._values


def _install(monkeypatch, titles, formula_grids, shown_grids):
    fake = _FakeSpreadsheets(titles, {"formula": formula_grids, "shown": shown_grids})
    monkeypatch.setattr(app, "_build_sheets_service", lambda: type("S", (), {
        "spreadsheets": staticmethod(lambda: fake)})())


# --- Дословные данные сломанной таблицы владельца ---------------------------------------
OPERATIONS_HEADER = ["Дата", "Тип", "Категория", "Описание", "Сумма, ₽", "Способ оплаты",
                     "Комментарий"]
BROKEN_ROW = ["", "", "Зарплата", "", 232, "Карта"]          # «Тип» пустой
SUMMARY_FORMULAS = [
    ["Показатель", "Сумма, ₽"],
    ["Доходы", "=SUMIF('Операции'!B:B;\"Доход\";'Операции'!E:E)"],
    ["Расходы", "=SUMIF('Операции'!B:B;\"Расход\";'Операции'!E:E)"],
    ["Баланс", "=B2-B3"],
]
SUMMARY_SHOWN_BROKEN = [
    ["Показатель", "Сумма, ₽"],
    ["Доходы", "0,00 ₽"],
    ["Расходы", "0,00 ₽"],
    ["Баланс", "0,00 ₽"],
]


def test_detects_the_real_defect_that_reached_the_owner(monkeypatch):
    """Итог 0 ₽ при живой записи на 232 ₽ — из-за пустой колонки-критерия."""
    _install(
        monkeypatch, ["Операции", "Сводка"],
        {"Операции": [OPERATIONS_HEADER, BROKEN_ROW], "Сводка": SUMMARY_FORMULAS},
        {"Операции": [OPERATIONS_HEADER, BROKEN_ROW], "Сводка": SUMMARY_SHOWN_BROKEN},
    )
    report = app.check_google_sheet_health("SID")

    assert report["ok"] is False
    kinds = {p["kind"] for p in report["problems"]}
    assert "criteria_column_blank" in kinds, report["problems"]

    blank = next(p for p in report["problems"] if p["kind"] == "criteria_column_blank")
    assert "B" in blank["detail"] and "Операции" in blank["detail"]
    assert "2" in blank["detail"], "должна называться конкретная строка с пустым полем"


def test_healthy_table_passes(monkeypatch):
    """Та же таблица с заполненным «Типом» — претензий быть не должно."""
    good_row = ["01.08.2026", "Доход", "Зарплата", "аванс", 232, "Карта"]
    _install(
        monkeypatch, ["Операции", "Сводка"],
        {"Операции": [OPERATIONS_HEADER, good_row], "Сводка": SUMMARY_FORMULAS},
        {"Операции": [OPERATIONS_HEADER, good_row],
         "Сводка": [["Показатель", "Сумма, ₽"], ["Доходы", "232,00 ₽"],
                    ["Расходы", "0,00 ₽"], ["Баланс", "232,00 ₽"]]},
    )
    report = app.check_google_sheet_health("SID")

    kinds = {p["kind"] for p in report["problems"]}
    assert "criteria_column_blank" not in kinds, report["problems"]


@pytest.mark.parametrize("bad", ["#REF!", "#DIV/0!", "#N/A", "#VALUE!"])
def test_cell_errors_are_reported(monkeypatch, bad):
    _install(
        monkeypatch, ["Лист"],
        {"Лист": [["Итог"], ["=A1/0"]]},
        {"Лист": [["Итог"], [bad]]},
    )
    report = app.check_google_sheet_health("SID")

    assert any(p["kind"] == "cell_error" and bad in p["detail"] for p in report["problems"])


def test_headers_without_data_are_reported(monkeypatch):
    """«Красивая» пустая таблица — частый способ отчитаться о несделанной работе."""
    _install(monkeypatch, ["Операции"], {"Операции": [OPERATIONS_HEADER]},
             {"Операции": [OPERATIONS_HEADER]})
    report = app.check_google_sheet_health("SID")

    assert any(p["kind"] == "no_data_rows" for p in report["problems"])


def test_zero_aggregate_without_data_is_not_a_defect(monkeypatch):
    """Пустая таблица честно даёт 0 — обвинять формулу не в чем."""
    _install(
        monkeypatch, ["Операции", "Сводка"],
        {"Операции": [OPERATIONS_HEADER], "Сводка": SUMMARY_FORMULAS},
        {"Операции": [OPERATIONS_HEADER], "Сводка": SUMMARY_SHOWN_BROKEN},
    )
    report = app.check_google_sheet_health("SID")

    kinds = {p["kind"] for p in report["problems"]}
    assert "criteria_column_blank" not in kinds
    assert "empty_aggregate" not in kinds


def test_reads_are_batched_not_per_sheet(monkeypatch):
    """Проверка не должна сама стать источником медлительности."""
    calls: list[str] = []
    fake = _FakeSpreadsheets(
        ["Операции", "Сводка"],
        {"formula": {"Операции": [OPERATIONS_HEADER, BROKEN_ROW], "Сводка": SUMMARY_FORMULAS},
         "shown": {"Операции": [OPERATIONS_HEADER, BROKEN_ROW], "Сводка": SUMMARY_SHOWN_BROKEN}},
    )
    original = fake.values().batchGet

    def counting(*a, **kw):
        calls.append("batchGet")
        return original(*a, **kw)

    monkeypatch.setattr(fake.values(), "batchGet", counting)
    monkeypatch.setattr(app, "_build_sheets_service", lambda: type("S", (), {
        "spreadsheets": staticmethod(lambda: fake)})())

    app.check_google_sheet_health("SID")

    assert len(calls) == 2, "два пакетных чтения на всю таблицу, а не по одному на лист"
