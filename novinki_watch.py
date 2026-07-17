# -*- coding: utf-8 -*-
"""Ежедневный анализ папки «Новинки» (задача владельца 17.07.2026).

Каждый день в 13:00 МСК (cron /etc/cron.d/albery-novinki-watch):
  1. Снимок содержимого папки Drive «Новинки». Пусто — молчим и выходим.
  2. Выходные — не трогаем ничего (правило «не писать сотрудникам по выходным»),
     файлы ждут ближайшего буднего дня.
  3. Из файлов извлекается текст (Telegram-экспорт HTML, html/txt, docx/pdf/xlsx,
     Google Docs/Sheets), сообщения-кандидаты отбираются скорингом «советов»
     (те же критерии, что в ручном анализе 17.07: паттерны рекомендаций + доменные
     темы WB/Ozon + числа/списки, анти-спам).
  4. Groq синтезирует из кандидатов конкретные рекомендации (реклама WB, цены/СПП,
     выкупы/отзывы, логистика, налоги, маркировка, регуляторика, импорт, аналитика,
     Ozon, оргпроцессы). Реклама услуг и болтовня отбрасываются.
  5. Рекомендации, уже покрытые базой знаний «О компании» (FTS по чанкам), отбрасываются.
     Если нового не осталось — молчим.
  6. Иначе: Google-таблица (как ручная от 17.07: Рекомендации/Документы/Сводка) +
     задача в Bitrix на Наталью (30) от ИИ Агента (22), дедлайн +3 рабочих дня 13:00.
     Без комментариев и соисполнителей.
  7. Все обработанные файлы удаляются из папки (файлы чужих владельцев, которые Drive
     не даёт удалить, убираются из папки через removeParents).

Режимы: --dry-run (ничего не пишет/не удаляет), --cleanup-only (только очистка папки —
для уже обработанных вручную файлов). Тестовые env: NOVINKI_TEST_RESPONSIBLE (id вместо
Натальи), NOVINKI_FOLDER (id папки вместо боевой).
"""
import os
import re
import sys
import json
import time
import hashlib
import logging
import urllib.request
from datetime import datetime, timedelta, timezone

os.environ.setdefault("B24_TASK_OFFER", "0")
os.environ.setdefault("B24_TASK_CHECKIN", "0")
os.environ.setdefault("B24_SESSION_IDLE_WATCH", "0")
os.environ.setdefault("AGENT_HEALTH_WATCHDOG", "0")

sys.path.insert(0, "/var/www/albery")
from dotenv import load_dotenv
load_dotenv("/var/www/albery/.env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s novinki: %(message)s")
log = logging.getLogger("novinki")

MSK = timezone(timedelta(hours=3))
FOLDER = os.getenv("NOVINKI_FOLDER", "1gF_Ddpdrx9iPCr16aCt4xbLA216f8Ogn")
NATALIA_ID = 30
CREATOR_ID = 22
RESPONSIBLE_ID = int(os.getenv("NOVINKI_TEST_RESPONSIBLE", "0") or NATALIA_ID)
MAX_CANDIDATES = 400
GROQ_BATCH = 15
GROQ_MODELS = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile")

STRONG = re.compile(
    r"лайфхак|делюсь опыт|рекоменду|совету[юе]|инструкци|алгоритм|чек.?лист|"
    r"схема (работ|прост|так)|как (правильно|лучше|избежать|снизить|поднять|обойти|выбрать|посчитать)|"
    r"важно[:!]|обязательно (нужно|сдела|провер|указ)|не (делайте|советую|рекомендую|ведитесь)|"
    r"лучше (не )?(став|дела|бра|использ|заклад)|нужно (делать|став|проверя|заклад|счит)|"
    r"работает (так|схема)|опыт показ|проверено|фишк[аиу]|на практике|по опыту|правило[: ]", re.I)
DOMAIN = re.compile(
    r"поставк|при[её]мк|штраф|оферт|реклам|ставк|дрр|ctr|конверси|карточк|seo|ключев|отзыв|"
    r"самовыкуп|выкуп|маркиров|честн\w+ знак|налог|усн|ндс|логист|склад|коэффициент|аналитик|"
    r"сертифи|декларац|обезлич|fbs|fbo|фбс|фбо|бренд|акци[яию]|скидк|остатк|оборачиваем|"
    r"юнит|маржа|себестоим|буст|продвиж|mpstats|мпстатс|джем|кабинет|вб|wb|wildberries|озон|ozon|"
    r"пвз|габарит|упаковк|баркод|штрихкод|кгт|литраж|рейтинг|выдач|позици", re.I)
NUMLIST = re.compile(r"(?m)^\s*\d+[).]")
NUMBERS = re.compile(r"\d+ ?(%|₽|руб|тыс|млн|коп)")
NOISE = re.compile(r"подписыв|подпишись|t\.me/|розыгрыш|вакансия|ищу (сотрудник|менеджер)|курс по|наставничеств|обучение платн", re.I)

TG_MSG_SPLIT = re.compile(r'<div class="message ([^"]*)"[^>]*>')
TG_DATE = re.compile(r'title="(\d{2}\.\d{2}\.\d{4}) \d{2}:\d{2}')
TG_FROM = re.compile(r'<div class="from_name">\s*([^<]+)')
TG_TEXT = re.compile(r'<div class="text">(.*?)</div>', re.S)
TAG = re.compile(r"<[^>]+>")


def _tg_parse(content):
    """Telegram-export HTML -> [{d,a,t}]. Для обычного HTML вернёт пусто."""
    import html as htmllib
    msgs, author = [], None
    parts = TG_MSG_SPLIT.split(content)
    for i in range(1, len(parts) - 1, 2):
        cls, body = parts[i], parts[i + 1]
        if "service" in cls:
            continue
        m = TG_FROM.search(body)
        if m:
            author = htmllib.unescape(m.group(1).strip())
        d = TG_DATE.search(body)
        t = TG_TEXT.search(body)
        if not t:
            continue
        txt = htmllib.unescape(re.sub(r"\s+", " ", TAG.sub(" ", t.group(1)))).strip()
        if txt:
            msgs.append({"d": d.group(1) if d else None, "a": author, "t": txt})
    return msgs


def _extract_text(cs, drive, item):
    """(kind, text) для файла Drive; text='' если извлечь нечего."""
    fid, mime, name = item["id"], item.get("mimeType", ""), item.get("name", "")
    try:
        if mime == "application/vnd.google-apps.document":
            return "gdoc", (drive.files().export(fileId=fid, mimeType="text/plain").execute() or b"").decode("utf-8", "replace")
        if mime == "application/vnd.google-apps.spreadsheet":
            return "gsheet", (drive.files().export(fileId=fid, mimeType="text/csv").execute() or b"").decode("utf-8", "replace")
        if mime.startswith("application/vnd.google-apps"):
            return "gapp", ""
        if mime in ("text/html", "text/plain", "text/csv") or name.lower().endswith((".html", ".htm", ".txt", ".csv")):
            data = drive.files().get_media(fileId=fid).execute()
            text = data.decode("utf-8", "replace")
            return ("tg-html" if "message" in text[:4000] and "from_name" in text[:200000] else "text"), text
        ext = None
        for e in ("pdf", "docx", "xlsx"):
            if name.lower().endswith("." + e):
                ext = e
        if mime in getattr(cs, "_BINARY_DOC_CTYPES", {}):
            ext = cs._BINARY_DOC_CTYPES[mime]
        if ext:
            data = drive.files().get_media(fileId=fid).execute()
            return "doc-" + ext, cs._extract_binary_document(data, ext)
    except Exception as exc:  # noqa: BLE001
        log.warning("extract failed %s: %s", name, str(exc)[:120])
    return "binary", ""


def _score_candidates(per_file_msgs):
    cands, seen = [], set()
    for fname, msgs in per_file_msgs.items():
        file_c = []
        for m in msgs:
            t = m["t"]
            if len(t) < 160:
                continue
            strong = len(STRONG.findall(t))
            domain = len(set(x.lower() for x in DOMAIN.findall(t)))
            if strong == 0 and domain < 2:
                continue
            score = 3 * strong + min(domain, 4) + 2 * (1 if NUMLIST.search(t) else 0) \
                    + (1 if NUMBERS.search(t) else 0) + min(len(t) // 500, 3)
            if NOISE.search(t):
                score -= 4
            if score < 4:
                continue
            k = hashlib.md5(t[:100].encode("utf-8")).hexdigest()
            if k in seen:
                continue
            seen.add(k)
            file_c.append((score, fname, m.get("d"), m.get("a"), re.sub(r"\s+", " ", t)[:600]))
        file_c.sort(key=lambda c: -c[0])
        cands.extend(file_c[:15])
    cands.sort(key=lambda c: -c[0])
    return cands[:MAX_CANDIDATES]


def _groq(prompt):
    from b24bot import _b24_groq_api_key
    key = _b24_groq_api_key()
    if not key:
        raise RuntimeError("нет GROQ_API_KEY")
    last = None
    for model in GROQ_MODELS:
        payload = {"model": model, "max_tokens": 4000, "temperature": 0.2,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                     "User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            return ((d.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(3)
    raise RuntimeError(f"Groq недоступен: {last}")


SYNTH_PROMPT = """Ты аналитик компании-селлера Wildberries/Ozon. Ниже сообщения из отраслевого чата продавцов.
Выдели ТОЛЬКО реально годные ПРАКТИЧЕСКИЕ рекомендации по улучшениям: организационные улучшения, работа с WB/Ozon
(реклама, цены/акции/СПП, выкупы/отзывы, логистика/склады/штрафы), налоги и учёт селлера, маркировка «Честный знак»,
регуляторика/юрзащита, импорт из Китая, аналитика и ИИ-инструменты, оргпроцессы. То есть информацию именно об
ИЗМЕНЕНИЯХ и улучшениях, которые компания может применить.
Отбрось: рекламу услуг и сервисов, вакансии, вопросы без ответов, жалобы без выводов, мошеннические схемы (о них
можно дать рекомендацию-предупреждение), флуд.
Ответ строго JSON: {"recommendations":[{"category":"...","recommendation":"что конкретно внедрить/изменить (1-2 предложения)",
"rationale":"обоснование из сообщения, с цифрами если есть","source":"файл | дата | автор","keywords":["2-4 ключевых слова темы"]}]}
Если годного нет — {"recommendations":[]}.

СООБЩЕНИЯ:
"""


def _synthesize(cands):
    recs = []
    for i in range(0, len(cands), GROQ_BATCH):
        batch = cands[i:i + GROQ_BATCH]
        lines = [f"[{fname} | {d or '?'} | {a or '?'}] {t}" for _, fname, d, a, t in batch]
        try:
            out = _groq(SYNTH_PROMPT + "\n".join(lines))
            data = json.loads(out)
            for r in data.get("recommendations") or []:
                if r.get("recommendation") and r.get("category"):
                    recs.append(r)
        except Exception as exc:  # noqa: BLE001
            log.warning("groq batch %s failed: %s", i, str(exc)[:150])
        time.sleep(2)
    # дедуп по началу текста рекомендации
    seen, out = set(), []
    for r in recs:
        k = re.sub(r"\W+", "", str(r["recommendation"]).lower())[:70]
        if k and k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _kb_covered(rec):
    """True, если тема уже покрыта базой знаний «О компании» (FTS по чанкам)."""
    from attachments import connect
    words = [w for w in (rec.get("keywords") or []) if len(str(w)) > 2][:4]
    if not words:
        words = re.findall(r"[А-Яа-яA-Za-z]{4,}", str(rec.get("recommendation")))[:4]
    q = " ".join(str(w) for w in words)
    if not q:
        return False
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM company_knowledge_chunks "
                    "WHERE content_tsv @@ plainto_tsquery('russian', %s)", (q,))
                n = cur.fetchone()["n"]
        return n >= 2
    except Exception as exc:  # noqa: BLE001
        log.warning("kb check failed: %s", str(exc)[:120])
        return False


def _deadline_3bd(now):
    d, added = now.date(), 0
    while added < 3:
        d += timedelta(days=1)
        if d.isoweekday() <= 5:
            added += 1
    return f"{d.isoformat()}T13:00:00+03:00"


def _make_sheet(cs, drive, sheets, recs, files_info, date_label):
    header = ["№", "Категория", "Рекомендация (что внедрить/изменить)", "Обоснование",
              "Источник (файл, дата, автор)", "Есть ли в базе «О компании»", "Комментарий"]
    rows = [header] + [[str(i + 1), r.get("category", ""), r.get("recommendation", ""),
                        r.get("rationale", ""), r.get("source", ""), "Нет (проверено FTS)", ""]
                       for i, r in enumerate(recs)]
    created = cs.tool_create_google_sheet({
        "title": f"Новинки — рекомендации по изменениям от {date_label}",
        "rows": rows, "confirm": True})
    sid = created["spreadsheet_id"]
    meta = sheets.spreadsheets().get(spreadsheetId=sid, fields="sheets(properties(sheetId))").execute()
    first_id = meta["sheets"][0]["properties"]["sheetId"]
    doc_rows = [["Файл", "Тип", "Извлечено сообщений/симв.", "Инсайт-кандидатов"]] + files_info
    reqs = [
        {"updateSheetProperties": {"properties": {"sheetId": first_id, "title": "Рекомендации"}, "fields": "title"}},
        {"addSheet": {"properties": {"title": f"Документы ({len(files_info)})",
                                     "gridProperties": {"rowCount": len(doc_rows) + 5, "columnCount": 5, "frozenRowCount": 1}}}},
        {"addSheet": {"properties": {"title": "Сводка", "gridProperties": {"rowCount": 12, "columnCount": 2}}}},
    ]
    resp = sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()
    docs_id = resp["replies"][1]["addSheet"]["properties"]["sheetId"]
    sum_id = resp["replies"][2]["addSheet"]["properties"]["sheetId"]
    summary = [["Параметр", "Значение"],
               ["Дата анализа", date_label],
               ["Источник", "Google Drive, папка «Новинки» (ежедневная автоматизация novinki_watch)"],
               ["Файлов обработано", str(len(files_info))],
               ["Рекомендаций (новых для базы знаний)", str(len(recs))],
               ["Метод", "Извлечение текста → скоринг советов (критерии владельца) → синтез Groq → фильтр дублей с базой «О компании» (FTS)"],
               ["Обработанные файлы", "удалены из папки после анализа"]]
    sheets.spreadsheets().values().update(spreadsheetId=sid, range=f"'Документы ({len(files_info)})'!A1",
                                          valueInputOption="RAW", body={"values": doc_rows}).execute()
    sheets.spreadsheets().values().update(spreadsheetId=sid, range="'Сводка'!A1",
                                          valueInputOption="RAW", body={"values": summary}).execute()
    style = [{"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                             "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                             "fields": "userEnteredFormat.textFormat.bold"}} for gid in (docs_id, sum_id)]
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": style}).execute()
    return created["url"]


def _make_task(cs, url, n_files, n_recs, date_label):
    title = f"Предложение орг.изменений на основании анализа папки «Новинки» ({date_label})"
    desc = f"""[b]Предлагается внести изменения в документацию компании[/b] по итогам ежедневного анализа папки «Новинки» ({date_label}): обработано файлов — {n_files}, выделено [b]{n_recs} новых рекомендаций[/b] (дубли с базой знаний «О компании» отброшены автоматически).

[b]Что изменяется:[/b] предложения по улучшениям работы с маркетплейсами (реклама WB, цены/акции/СПП, выкупы и отзывы, логистика, налоги, маркировка, регуляторика, импорт, аналитика, Ozon, оргпроцессы) — для внесения в базу знаний «О компании».

[b]Где посмотреть:[/b] все рекомендации с обоснованием и источником — в таблице:
[URL={url}]📋 «Новинки — рекомендации по изменениям от {date_label}»[/URL]
Лист «Рекомендации»: колонка C — что внедрить, колонка D — обоснование, колонка E — источник.

[b]Что нужно:[/b] ознакомиться с таблицей, отметить приоритетные и спорные пункты и дать обратную связь в комментариях к задаче."""
    created = cs.tool_create_bitrix_task({
        "title": title, "description": desc,
        "responsible_bitrix_user_id": RESPONSIBLE_ID,
        "creator_bitrix_user_id": CREATOR_ID,
        "deadline": _deadline_3bd(datetime.now(MSK)),
        "result_criteria": "Дана обратная связь в комментариях: приоритетные и спорные рекомендации из таблицы.",
    })
    return created.get("task_id"), created.get("deadline")


def _delete_items(drive, items, dry):
    """Убрать обработанные файлы из папки. Чужие файлы Drive не даёт удалить (403 с
    ретраями), поэтому сначала быстрый removeParents (достаточно прав writer), и только
    для своих файлов — настоящий delete."""
    ok = moved = failed = 0
    for it in items:
        if dry:
            continue
        try:
            drive.files().update(fileId=it["id"], removeParents=FOLDER).execute()
            moved += 1
        except Exception:  # noqa: BLE001
            try:
                drive.files().delete(fileId=it["id"]).execute()
                ok += 1
            except Exception as exc2:  # noqa: BLE001
                failed += 1
                log.warning("cannot remove/delete %s: %s", it.get("name"), str(exc2)[:120])
        time.sleep(0.15)
    return ok, moved, failed


def main():
    dry = "--dry-run" in sys.argv
    cleanup_only = "--cleanup-only" in sys.argv
    now = datetime.now(MSK)
    if now.isoweekday() > 5 and not cleanup_only and not dry:
        log.info("выходной (%s) — пропуск, файлы ждут буднего дня", now.date())
        return

    from googleapiclient.discovery import build
    import app  # noqa: F401
    import gdrive
    from mcp import context_server as cs
    creds = gdrive._google_user_credentials()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    items, token = [], None
    while True:
        resp = drive.files().list(q=f"'{FOLDER}' in parents and trashed=false",
                                  fields="nextPageToken,files(id,name,mimeType,size)",
                                  pageSize=1000, pageToken=token).execute()
        items.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    if not items:
        log.info("папка пуста — молчим")
        return
    log.info("в папке %d объектов (снимок зафиксирован)", len(items))

    if cleanup_only:
        ok, moved, failed = _delete_items(drive, items, dry)
        log.info("cleanup-only: удалено %d, убрано из папки %d, не удалось %d", ok, moved, failed)
        return

    per_file, files_info, total_msgs = {}, [], 0
    for it in items:
        if it.get("mimeType") == "application/vnd.google-apps.folder":
            files_info.append([it["name"], "папка", "-", "-"])
            continue
        kind, text = _extract_text(cs, drive, it)
        if kind == "tg-html":
            msgs = _tg_parse(text)
        elif text.strip():
            plain = cs._strip_html_to_text(text) if kind == "text" and "<" in text[:200] else text
            msgs = [{"d": None, "a": None, "t": plain[:20000]}]
        else:
            msgs = []
        per_file[it["name"]] = msgs
        total_msgs += len(msgs)
        files_info.append([it["name"], kind, str(len(msgs) if kind == "tg-html" else len(text)), ""])

    cands = _score_candidates(per_file)
    cand_by_file = {}
    for _, fname, *_ in cands:
        cand_by_file[fname] = cand_by_file.get(fname, 0) + 1
    for row in files_info:
        if row[3] == "":
            row[3] = str(cand_by_file.get(row[0], 0))
    log.info("файлов %d, сообщений %d, кандидатов %d", len(items), total_msgs, len(cands))

    recs = _synthesize(cands) if cands else []
    fresh = [r for r in recs if not _kb_covered(r)]
    log.info("синтезировано %d, новых для базы %d", len(recs), len(fresh))

    if not fresh:
        log.info("нового для базы знаний нет — молчим; файлы удаляем как обработанные")
        ok, moved, failed = _delete_items(drive, items, dry)
        log.info("удалено %d, убрано из папки %d, не удалось %d", ok, moved, failed)
        return

    date_label = now.strftime("%d.%m.%Y")
    if dry:
        log.info("DRY-RUN: создали бы таблицу (%d рек.) и задачу на %s; удалили бы %d файлов",
                 len(fresh), RESPONSIBLE_ID, len(items))
        for r in fresh[:10]:
            log.info("  REC: [%s] %s", r.get("category"), str(r.get("recommendation"))[:100])
        return

    url = _make_sheet(cs, drive, sheets, fresh, files_info, date_label)
    log.info("таблица: %s", url)
    tid, dl = _make_task(cs, url, len(items), len(fresh), date_label)
    log.info("задача Bitrix %s создана (ответственный %s, дедлайн %s)", tid, RESPONSIBLE_ID, dl)
    ok, moved, failed = _delete_items(drive, items, dry)
    log.info("файлы обработаны: удалено %d, убрано из папки %d, не удалось %d", ok, moved, failed)


if __name__ == "__main__":
    _dry = ("--dry-run" in sys.argv) or ("--cleanup-only" in sys.argv)
    try:
        main()
    except Exception as _exc:
        if not _dry:
            from shared.automation_registry import mark_system_run
            mark_system_run("crond:albery-novinki-watch:main", "error", error=str(_exc)[:300])
        raise
    if not _dry:
        from shared.automation_registry import mark_system_run
        mark_system_run("crond:albery-novinki-watch:main", "ok")
