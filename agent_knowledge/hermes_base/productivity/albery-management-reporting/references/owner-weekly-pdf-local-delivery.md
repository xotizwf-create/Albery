# Owner weekly PDF — local chat delivery pattern

Session pattern captured from a weekly Albery report request where the owner asked to receive the PDF directly in Telegram after noticing that the current report did not match the previous week’s format.

## Durable lesson

- Treat “дай/пришли пдф недельного отчета сюда” as **deliver the PDF in this chat**, not as Bitrix PDF sending.
- `send_owner_weekly_report_pdf` is only for explicitly approved Bitrix personal-message delivery; it requires prior approval and sends to Bitrix recipients.
- If the owner says the weekly report format differs from last week, compare the prior weekly report text against the current saved weekly report, rebuild the current report in the prior section/table structure, save it as the current weekly report version, then generate and attach the PDF.

## Practical workflow

1. Load Albery instructions and this skill.
2. If the owner asks for an exact DB version (explicit period + `версия N`, or wording like `1 в 1 из БД`), **do not reuse any existing local PDF by filename alone**. First fetch that exact row from `owner_weekly_reports` (`period_start`, `period_end`, `version`) and render a fresh PDF from `report_text`/`raw_json.report_text`. Verify the text export character count equals the DB `length(report_text)` and the PDF begins with `%PDF-`, then deliver the freshly rendered artifact.
3. If a PDF for the latest/current weekly report has already been generated locally and the user did not request an exact DB version, first verify it exists and begins with `%PDF-`, then deliver it immediately with `MEDIA:/absolute/path/to/file.pdf`. Do **not** ask for Bitrix-send confirmation for a plain “дай PDF” request in the current chat.
4. If no current PDF exists, read the previous weekly report and the current saved weekly report.
4. If no matching current PDF artifact exists, read the previous weekly report and the current saved weekly report.
5. Identify structural differences (sections, table-heavy layout, decision blocks, address questions, summary style).
5. Rebuild current-week text in the previous-week structure when needed, preserving facts from the current week and marking missing data as `нет данных` rather than inventing.
6. Save the rebuilt weekly report with `save_owner_weekly_report(..., status="done")` so it becomes the current version.
7. Generate a PDF locally and respond with `MEDIA:/absolute/path/to/file.pdf`.

Confirmation distinction: local Telegram delivery of an already-approved/generated report is not a side-effect outside the chat and does not need an additional approval. Sending/uploading the PDF to Bitrix recipients is a separate external delivery action and still requires explicit owner approval.

## PDF generation note

When no rich Markdown-to-PDF stack is available, ReportLab with DejaVu fonts works reliably for Russian text. For table-heavy reports, landscape A4 plus small table cell fonts is usually more legible than portrait.
