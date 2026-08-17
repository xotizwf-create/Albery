"""Меньше обращений к Google — не ради сети, а ради ходов модели.

Замер живой сессии 17.08.2026 (создание таблицы «Расходы и доходы»): 14 вызовов
инструментов, суммарно 33 секунды работы Google — при том что сессия заняла 5 минут 2
секунды. То есть на Google ушло 11% времени, а 89% — паузы между вызовами, где думает
модель (самые большие 70 и 115 секунд). Отсюда правило: один лишний вызов стоит ~20
секунд, один лишний сетевой поход — ~1 секунду. Экономить надо ВЫЗОВЫ.

Отдельно закреплено, что экономия не должна отменять проверку результата: пакетное
чтение существует ровно для того, чтобы перечитывать написанное было дёшево.
"""
from __future__ import annotations

import pytest

import app


class _Recorder:
    def __init__(self):
        self.batch_get_calls = 0
        self.get_calls = 0
        self.meta_calls = 0
        self.polish_calls = 0

    # --- values() ---
    def batchGet(self, spreadsheetId, ranges, valueRenderOption):  # noqa: N803
        self.batch_get_calls += 1
        self._pending = {"valueRanges": [{"range": r, "values": [["x"]]} for r in ranges]}
        return self

    def get(self, spreadsheetId=None, range=None, valueRenderOption=None, fields=None, **kw):  # noqa: A002,N803
        if range is not None:
            self.get_calls += 1
            self._pending = {"range": range, "values": [["x"]]}
        else:
            self.meta_calls += 1
            self._pending = {"properties": {"locale": "ru_RU"}}
        return self

    def execute(self):
        return self._pending

    def values(self):
        return self

    def spreadsheets(self):
        return self


@pytest.fixture()
def rec(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(app, "_build_sheets_service", lambda: r)
    monkeypatch.setattr(app, "_google_user_credentials", lambda: object())
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: r)
    app._SHEET_LOCALE_CACHE.clear()
    return r


def test_several_ranges_are_read_in_one_call(rec):
    """Три диапазона — один вызов инструмента, а не три хода модели."""
    out = app.read_google_sheet_values("SID", ["'A'!A1:B2", "'B'!A1:B2", "'C'!A1:B2"])

    assert rec.batch_get_calls == 1
    assert rec.get_calls == 0
    assert out["range_count"] == 3
    assert len(out["ranges"]) == 3


def test_single_range_still_works_unchanged(rec):
    """Старый способ вызова обязан продолжать работать ровно как раньше."""
    out = app.read_google_sheet_values("SID", "'A'!A1:B2")

    assert rec.get_calls == 1
    assert rec.batch_get_calls == 0
    assert out["values"] == [["x"]]
    assert "ranges" not in out


def test_empty_range_list_is_rejected(rec):
    with pytest.raises(ValueError):
        app.read_google_sheet_values("SID", [])


def test_locale_is_fetched_once_per_spreadsheet(rec):
    """Локаль не меняется годами, а стоила 1,15 с на КАЖДОЕ форматирование."""
    assert app._sheet_locale(rec, "SID") == "ru_RU"
    assert app._sheet_locale(rec, "SID") == "ru_RU"
    assert app._sheet_locale(rec, "SID") == "ru_RU"

    assert rec.meta_calls == 1, "локаль обязана запрашиваться один раз на таблицу"


def test_locale_cache_is_per_spreadsheet(rec):
    app._sheet_locale(rec, "SID-1")
    app._sheet_locale(rec, "SID-2")

    assert rec.meta_calls == 2, "разные таблицы не должны делить кеш локали"
