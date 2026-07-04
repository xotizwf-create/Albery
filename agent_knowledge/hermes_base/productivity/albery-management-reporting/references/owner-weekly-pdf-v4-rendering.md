# Owner weekly PDF v4 local rendering

Session pattern: after rebuilding/saving a current weekly owner report in Albery, the already-delivered PDF may still be an older local version. If the owner needs the PDF for the current saved version, generate a new local PDF from the saved `report_text` and deliver it with `MEDIA:/absolute/path.pdf`.

## Durable workflow

1. Confirm the current saved weekly report version via `get_owner_reports(report_kind="weekly", limit=1)` or the equivalent live source.
2. Use the current report's `report_text` as the source of truth. Do not reuse an older `/tmp/*.pdf` if a newer current version was saved after that PDF was created.
3. Render a fresh local PDF and name it with period + version, e.g. `/tmp/albery_weekly_report_YYYY-MM-DD_YYYY-MM-DD_new_rules_v4.pdf`.
4. Verify before delivery:
   - file exists;
   - size is non-zero;
   - first bytes are `%PDF-`;
   - `file` identifies it as a PDF.
5. Final response should be short and include only the relevant context plus `MEDIA:/path/to/file.pdf`.

## Practical rendering note

A robust local fallback is Python `reportlab` with DejaVu fonts for Cyrillic:

- page: landscape A4;
- fonts: `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` and `DejaVuSans-Bold.ttf`;
- parse Markdown tables into `Table(..., repeatRows=1)` with small font;
- render numbered section headings as bold paragraph styles.

This is a fallback when no built-in PDF export tool is available or when a local Telegram attachment is needed. The important rule is version freshness: PDF must match the latest current weekly report, not merely the latest PDF file found in `/tmp`.
