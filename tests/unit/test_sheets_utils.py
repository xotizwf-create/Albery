from shared.sheets_utils import (
    a1_column_name,
    formula_argument_separator_for_locale,
    normalize_formula_for_separator,
    normalize_sheet_values_for_locale,
    readability_dimension_requests,
    sheet_column_pixel_width,
)


def test_formula_separator_follows_google_sheet_locale():
    assert formula_argument_separator_for_locale("ru_RU") == ";"
    assert formula_argument_separator_for_locale("de_DE") == ";"
    assert formula_argument_separator_for_locale("en_US") == ","
    assert formula_argument_separator_for_locale(None) == ","


def test_normalize_formula_only_replaces_commas_outside_quotes():
    assert normalize_formula_for_separator('=SUM(A1,B1,"x,y")', ";") == '=SUM(A1;B1;"x,y")'
    assert normalize_formula_for_separator("plain,a,b", ";") == "plain,a,b"
    assert normalize_formula_for_separator("=SUM(A1,B1)", ",") == "=SUM(A1,B1)"


def test_normalize_sheet_values_wraps_scalar_rows():
    assert normalize_sheet_values_for_locale([["=SUM(A1,B1)"], "=SUM(C1,D1)"], ";") == [
        ["=SUM(A1;B1)"],
        ["=SUM(C1;D1)"],
    ]


def test_a1_column_name_handles_bounds():
    assert a1_column_name(0) == "A"
    assert a1_column_name(1) == "A"
    assert a1_column_name(26) == "Z"
    assert a1_column_name(27) == "AA"
    assert a1_column_name(52) == "AZ"


def test_sheet_column_pixel_width_prefers_readability():
    values = [["Клиент", "Сумма ₽"], ["Очень длинное русское название", "125000"]]
    assert sheet_column_pixel_width(values, 0) >= 150
    assert sheet_column_pixel_width(values, 1) >= 135


def test_readability_dimension_requests_caps_dimensions_and_adds_row_height():
    requests = readability_dimension_requests(123, [["A", "B"]], width=99, row_count=500)
    assert requests[0]["autoResizeDimensions"]["dimensions"]["endIndex"] == 26
    assert requests[1]["autoResizeDimensions"]["dimensions"]["endIndex"] == 200
    assert any(
        req.get("updateDimensionProperties", {}).get("range", {}).get("dimension") == "ROWS"
        and req["updateDimensionProperties"]["properties"] == {"pixelSize": 32}
        for req in requests
    )
