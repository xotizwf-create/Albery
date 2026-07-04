# Owner weekly 2026-06-26 cron: live Bitrix + failed preview delivery

Session pattern worth reusing for Friday weekly Albery jobs.

## Durable lessons

- Friday-off calendar: period was Monday–Thursday (`2026-06-22..2026-06-25`), with Friday shown explicitly as day off. Evaluate weekly/control meeting on Thursday only.
- Bitrix freshness anomaly must be rechecked every week. In this run, unlike prior weeks, Bitrix was live: current index showed fresh tasks through `2026-06-26 16:50`, and week-created tasks existed on 24–25.06. Therefore it was correct to report current task counts instead of the Marketplace-subscription warning.
- If the Google Sheet `Операционная встреча. Албери 2.0` has no current `Задачи недели` block for the report period, do not invent per-task plan acceptance. Build section 6 as a strict fallback: table missing ↔ Zoom/Bitrix facts ↔ artifact gaps, and label current-week plan as not accepted.
- The PDF preview tool may return a rendered filename/size but fail Bitrix delivery to the preview owner with `CHAT_ID_EMPTY`, and no artifact may be present in `/root/.hermes/media_cache`. In that case, create a local preview PDF from the saved markdown/report text using ReportLab + DejaVuSans, verify `%PDF-`, and attach the resulting `MEDIA:/root/.hermes/media_cache/<name>.pdf` in the cron final response.
- Overdue reminder drafting is allowed only when Bitrix freshness is live. Use the allowlist only, and do not send until explicit owner approval.

## Minimal local PDF fallback recipe

Use this only for owner preview/local delivery when Bitrix PDF delivery failed and no server artifact exists:

```python
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
import html, re, os

pdfmetrics.registerFont(TTFont('DejaVuSans','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuSans-Bold','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
base = ParagraphStyle('Base', fontName='DejaVuSans', fontSize=9, leading=12, spaceAfter=6)
h1 = ParagraphStyle('H1', parent=base, fontName='DejaVuSans-Bold', fontSize=16, leading=20)
h2 = ParagraphStyle('H2', parent=base, fontName='DejaVuSans-Bold', fontSize=13, leading=16, spaceBefore=12)
mono = ParagraphStyle('Mono', parent=base, fontName='DejaVuSans', fontSize=6.2, leading=8)

def md_inline(s):
    s = html.escape(s)
    return re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)

# Read saved report markdown/text, convert headings/tables roughly, build SimpleDocTemplate.
# Always verify: open(path,'rb').read(5) == b'%PDF-'
```

This fallback is not a replacement for production Bitrix delivery; it is only to give the owner the preview in the cron final response when the preview-send channel fails.
