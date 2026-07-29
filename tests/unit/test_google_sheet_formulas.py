"""Формулы Google Sheets в русской локали — тупик, из-за которого агент «стал тупить».

Реальный случай (Евгений Палей, dialog 14, 28.07.2026, ходы 1263–1266): агент делал копию
калькулятора, писал формулы и получал `#ERROR!`. В переписке он сам вывел верную причину
(«нужна запятая вместо точки»), исправил — и снова получил ошибку, после чего отчитался
об успехе с выдуманными числами.

Причина не в модели. В ru_RU-таблице через USER_ENTERED:
  * `=B3*0.14` — точка не десятичный разделитель → `#ERROR!` (проверено на живом API);
  * `=B3*0,14` — корректно, НО наш собственный нормализатор превращал ЛЮБУЮ запятую вне
    кавычек в `;`, то есть `=B3*0;14` → снова `#ERROR!`.
Оба пути вели в ошибку: записать дробное число в русскую таблицу было НЕВОЗМОЖНО.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def app_mod():
    import app

    return app


class TestFormulaDialect:
    """Конвертация формулы под локаль таблицы: и англо-, и русскоязычный диалект."""

    def test_us_decimal_point_becomes_comma(self, app_mod):
        # Модель пишет как привыкла — точка-разделитель дроби. Должно доехать как 0,14.
        assert app_mod.convert_formula_to_locale("=B3*0.14", ";", ",") == "=B3*0,14"

    def test_ru_decimal_comma_survives(self, app_mod):
        # ГЛАВНЫЙ регресс: раньше здесь получалось "=B3*0;14" — формула из прода.
        assert app_mod.convert_formula_to_locale("=B3*0,14", ";", ",") == "=B3*0,14"

    def test_us_argument_comma_becomes_semicolon(self, app_mod):
        assert app_mod.convert_formula_to_locale("=SUM(A1,A2)", ";", ",") == "=SUM(A1;A2)"
        assert app_mod.convert_formula_to_locale('=IF(A1>0,"да","нет")', ";", ",") == '=IF(A1>0;"да";"нет")'

    def test_us_argument_comma_between_digits_becomes_semicolon(self, app_mod):
        # Внутри вызова функции запятая между цифрами — разделитель аргументов.
        assert app_mod.convert_formula_to_locale("=ROUND(A1*2,3)", ";", ",") == "=ROUND(A1*2;3)"

    def test_ru_formula_with_semicolons_keeps_decimal_commas(self, app_mod):
        # Если формула уже русская (есть `;`), запятая между цифрами — дробь, не аргумент.
        assert app_mod.convert_formula_to_locale("=ROUND(B1*0,3;2)", ";", ",") == "=ROUND(B1*0,3;2)"

    def test_commas_inside_strings_untouched(self, app_mod):
        assert app_mod.convert_formula_to_locale('=A1&"один, два"', ";", ",") == '=A1&"один, два"'

    def test_english_locale_is_passthrough(self, app_mod):
        assert app_mod.convert_formula_to_locale("=SUM(A1,A2)", ",", ".") == "=SUM(A1,A2)"

    def test_non_formula_values_untouched(self, app_mod):
        assert app_mod.convert_formula_to_locale("обычный текст, с запятой", ";", ",") == "обычный текст, с запятой"
        assert app_mod.convert_formula_to_locale(42, ";", ",") == 42

    def test_numeric_string_gets_locale_decimal(self, app_mod):
        # "0.14" в ru-таблице молча становится ТЕКСТОМ — ошибки нет, а расчёт сломан.
        assert app_mod.convert_formula_to_locale("0.14", ";", ",") == "0,14"
        # но версии/даты/артикулы трогать нельзя
        assert app_mod.convert_formula_to_locale("1.2.3", ";", ",") == "1.2.3"
        assert app_mod.convert_formula_to_locale("28.07.2026", ";", ",") == "28.07.2026"

    def test_locale_separators(self, app_mod):
        assert app_mod._formula_argument_separator_for_locale("ru_RU") == ";"
        assert app_mod._formula_decimal_separator_for_locale("ru_RU") == ","
        assert app_mod._formula_argument_separator_for_locale("en_US") == ","
        assert app_mod._formula_decimal_separator_for_locale("en_US") == "."


class FakeRuSheets:
    """Минимальная модель ru_RU-таблицы Google: как она реально парсит USER_ENTERED.

    Проверено на живом API 29.07.2026 в таблице locale=ru_RU:
      `=A1*0.14` → #ERROR!, `=A1*0,14` → 14, `=SUM(A1,A1)` → #ERROR!, `=SUM(A1;A1)` → 200.
    """

    def __init__(self, locale: str = "ru_RU"):
        self.locale = locale
        self.cells: dict[str, str] = {}
        self.writes: list[list[list]] = []

    # --- google-api-python-client shape ---
    def spreadsheets(self):
        return self

    def get(self, spreadsheetId=None, fields=None, **kw):  # noqa: N803
        return _Exec({"properties": {"locale": self.locale}})

    def values(self):
        return _Values(self)

    # --- ru_RU parser ---
    def evaluate(self, raw):
        if not isinstance(raw, str) or not raw.startswith("="):
            return raw if isinstance(raw, str) else str(raw)
        body = _strip_quoted(raw)
        import re

        for fn in re.findall(r"([A-Za-zА-Яа-я][A-Za-zА-Яа-я0-9_.]*)\s*\(", body):
            if fn.upper() not in {"SUM", "IF", "ROUND", "СУММ", "ЕСЛИ", "ОКРУГЛ"}:
                return "#NAME?"
        if re.search(r"\d\.\d", body):  # точка как дробь — не ru
            return "#ERROR!"
        if re.search(r"[-+*/^]\s*-?\d+;", body):  # `;` разорвал число: =B3*0;14
            return "#ERROR!"
        if re.search(r"[A-Za-zА-Яа-я0-9)\"]\s*,", body):  # запятая как аргумент — не ru
            if not re.search(r"\d,\d", body):
                return "#ERROR!"
        return "OK"


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _Values:
    def __init__(self, sheet: FakeRuSheets):
        self.sheet = sheet

    def update(self, spreadsheetId=None, range=None, valueInputOption=None, body=None, **kw):  # noqa: A002,N803
        values = (body or {}).get("values") or []
        self.sheet.writes.append(values)
        self.sheet.cells = {}
        for r, row in enumerate(values, start=1):
            for c, cell in enumerate(row or [], start=1):
                self.sheet.cells[f"{r}:{c}"] = self.sheet.evaluate(cell)
        return _Exec({"updatedRange": range, "updatedCells": sum(len(r or []) for r in values)})

    def get(self, spreadsheetId=None, range=None, valueRenderOption=None, **kw):  # noqa: A002,N803
        if not self.sheet.cells:
            return _Exec({"values": []})
        rows = max(int(k.split(":")[0]) for k in self.sheet.cells)
        cols = max(int(k.split(":")[1]) for k in self.sheet.cells)
        out = [
            [self.sheet.cells.get(f"{r}:{c}", "") for c in _py_range(1, cols + 1)]
            for r in _py_range(1, rows + 1)
        ]
        return _Exec({"values": out})


_py_range = range


def _strip_quoted(text: str) -> str:
    out, in_q = [], False
    for ch in text:
        if ch == '"':
            in_q = not in_q
            continue
        if not in_q:
            out.append(ch)
    return "".join(out)


class TestWriteRetriesInsteadOfFailing:
    """Запись должна доводить формулу до рабочего вида, а не падать и не врать об успехе."""

    @pytest.fixture
    def patched(self, app_mod, monkeypatch):
        sheet = FakeRuSheets()
        monkeypatch.setattr(app_mod, "_google_user_credentials", lambda: object())
        monkeypatch.setattr(
            app_mod, "_build_sheets_service", lambda creds=None: sheet, raising=False,
        )
        return app_mod, sheet

    def test_ru_decimal_comma_written_as_is(self, patched):
        app_mod, sheet = patched
        res = app_mod.write_google_sheet_values("SID", "B38", [["=B3*0,14"]])
        assert sheet.writes[-1] == [["=B3*0,14"]]
        assert res["formula_errors"] == 0

    def test_us_decimal_point_converted(self, patched):
        app_mod, sheet = patched
        res = app_mod.write_google_sheet_values("SID", "B38", [["=B3*0.14"]])
        assert sheet.writes[-1] == [["=B3*0,14"]]
        assert res["formula_errors"] == 0

    def test_broken_formula_fails_loudly_after_all_variants(self, patched):
        app_mod, sheet = patched
        with pytest.raises(RuntimeError) as exc:
            app_mod.write_google_sheet_values("SID", "B38", [["=НЕТТАКОЙ(A1.B2)"]])
        message = str(exc.value)
        # Отказ обязан быть предметным: какая ячейка, что вернул Google, что мы записали.
        assert "#NAME?" in message and "A1" in message and "НЕТТАКОЙ" in message
