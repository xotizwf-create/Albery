"""Pure Google Sheets value and formatting helpers.

This module intentionally avoids Google API clients, Flask state, database
connections, network calls, and secrets.  It only prepares values/ranges/request
payload fragments for callers in app.py.
"""

from __future__ import annotations

from typing import Any


def formula_argument_separator_for_locale(locale: str | None) -> str:
    """Return the Google Sheets formula argument separator for a spreadsheet locale."""
    loc = (locale or "").lower()
    if loc.startswith(("ru", "uk", "be", "de", "fr", "es", "it", "pl", "pt", "nl", "cs", "da", "fi", "sv", "tr")):
        return ";"
    return ","


def normalize_formula_for_separator(value: Any, separator: str) -> Any:
    """Convert comma-separated formula arguments to semicolons outside quoted strings.

    This prevents #ERROR in ru_RU sheets when an LLM writes English-style
    formulas.  Cell values that are not formulas are returned unchanged.
    """
    if separator != ";" or not isinstance(value, str) or not value.startswith("="):
        return value
    out: list[str] = []
    in_double = False
    in_single = False
    escape_next = False
    for ch in value:
        if escape_next:
            out.append(ch)
            escape_next = False
            continue
        if ch == "\\":
            out.append(ch)
            escape_next = True
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            continue
        if ch == "," and not in_double and not in_single:
            out.append(";")
        else:
            out.append(ch)
    return "".join(out)


def normalize_sheet_values_for_locale(values: list, separator: str) -> list:
    return [
        [normalize_formula_for_separator(cell, separator) for cell in (row if isinstance(row, list) else [row])]
        for row in (values or [])
    ]


def a1_column_name(index: int) -> str:
    index = max(1, int(index or 1))
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def sheet_column_pixel_width(values: list[list[Any]], col_index: int) -> int:
    """Choose a readable fixed width after Google auto-resize.

    Google auto-resize can still leave generated dashboards cramped when there
    are merged KPI blocks, currency values, Russian labels, or formulas.  We use
    a conservative width from the actual visible content so numbers and text do
    not get clipped.
    """
    longest = 0
    has_money_or_number = False
    for row in values or []:
        if not isinstance(row, list) or col_index >= len(row):
            continue
        text = str(row[col_index] if row[col_index] is not None else "").strip()
        if not text:
            continue
        for part in text.replace("\n", " ").split():
            longest = max(longest, len(part))
        longest = max(longest, min(len(text), 42))
        if any(ch.isdigit() for ch in text) or "₽" in text or "%" in text:
            has_money_or_number = True
    if has_money_or_number:
        return max(135, min(260, 9 * longest + 46))
    return max(150, min(340, 8 * longest + 52))


def readability_dimension_requests(
    sheet_id: int, values: list[list[Any]] | None, width: int, row_count: int
) -> list[dict[str, Any]]:
    """Dimension requests that keep Albery-generated sheets readable."""
    width = max(1, min(int(width or 1), 26))
    row_count = max(1, min(int(row_count or 1), 200))
    values = values or []
    requests: list[dict[str, Any]] = [
        {
            "autoResizeDimensions": {
                "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": width}
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": row_count}
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 46},
                "fields": "pixelSize",
            }
        },
    ]
    for col in range(width):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": col, "endIndex": col + 1},
                    "properties": {"pixelSize": sheet_column_pixel_width(values, col)},
                    "fields": "pixelSize",
                }
            }
        )
    if row_count > 1:
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": row_count},
                    "properties": {"pixelSize": 32},
                    "fields": "pixelSize",
                }
            }
        )
    return requests


# Backward-compatible aliases for app.py's historical private helper names.
_formula_argument_separator_for_locale = formula_argument_separator_for_locale
_normalize_formula_for_separator = normalize_formula_for_separator
_normalize_sheet_values_for_locale = normalize_sheet_values_for_locale
_a1_column_name = a1_column_name
_sheet_column_pixel_width = sheet_column_pixel_width
_readability_dimension_requests = readability_dimension_requests
