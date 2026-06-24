from __future__ import annotations
"""zoom_processing contract v14->v15 (owner 2026-06-24): (1) every operational task MUST carry a
deadline — if not voiced, the agent assigns a sane default and sets deadline_inferred=true ("срок не
указан" no longer allowed for tasks); (2) add report_text section "14. Сверка с матрицей решений и
картой процессов" as a technical audit log (per-task verdict line) so the responsibility check is
visible, with "Итоги недели" renumbered to 15. Idempotent; new version kept in history."""
import os
from dotenv import load_dotenv
load_dotenv("/var/www/albery/.env")
os.environ.setdefault("DBURL", os.getenv("DATABASE_ADMIN_URL","").strip() or os.getenv("DATABASE_URL","").strip())

import os,sys,datetime
import psycopg
from psycopg.rows import dict_row
DBURL=os.environ["DBURL"]
for p in ("postgresql+psycopg2://","postgresql+psycopg://"):
    if DBURL.startswith(p): DBURL="postgresql://"+DBURL[len(p):]
CAT="04fd610c-c782-4004-82cb-3aa53fd2da00"
MARK="Сверка с матрицей решений и картой процессов — технический лог"

R=[]  # (old, new, label)
R.append((
 '- Если срок не назван, пиши "срок не указан"; в JSON deadline = null.',
 '- У КАЖДОЙ задачи обязателен срок. Если срок не прозвучал — поставь обоснованный сам (по умолчанию дата созвона; для явно несрочного — ближайшая контрольная встреча или конец недели) и в JSON пометь deadline_inferred = true. Формулировку "срок не указан" для операционных задач НЕ используй.',
 "R1 правила задач"))
R.append((
 '4. Если срок не прозвучал, используй `deadline_text = "срок не указан"`.',
 '4. Срок ОБЯЗАТЕЛЕН у каждой задачи. Если он не прозвучал — поставь обоснованный сам (по умолчанию дата созвона ДД.ММ.ГГГГ; для несрочного — ближайшая контрольная встреча или конец недели) и добавь в JSON `deadline_inferred = true`. `deadline_text = "срок не указан"` НЕ используй.',
 "R2 строгий контракт"))
R.append((
 '13. Что контролировать на следующем созвоне\n14. Итоги недели: Наталья и Артур — добавляется ТОЛЬКО если созвон определён как контрольная встреча итогов недели (пятница); в остальных отчётах этого раздела нет.',
 '13. Что контролировать на следующем созвоне\n14. Сверка с матрицей решений и картой процессов — технический лог проверки ответственности (по блоку «Функционал и зона ответственности»). По КАЖДОЙ операционной задаче одной строкой: № — исполнитель — процесс/зона — ответственный по матрице (исполнение/контроль) — источник-документ — вердикт (совпадает / расхождение / требует уточнения) — короткое пояснение, всё ли корректно. Это аудит-след, чтобы было видно, что сверка реально проведена.\n15. Итоги недели: Наталья и Артур — добавляется ТОЛЬКО если созвон определён как контрольная встреча итогов недели (пятница); в остальных отчётах этого раздела нет.',
 "R3 структура report_text"))
R.append((
 'добавь в report_text раздел "14. Итоги недели: Наталья и Артур" и заполни',
 'добавь в report_text раздел "15. Итоги недели: Наталья и Артур" и заполни',
 "R4 итоги недели заголовок"))

APPEND=(
 "\n\n### Технический лог сверки в отчёт (раздел 14 — ОБЯЗАТЕЛЬНО)\n"
 "Результат Шага 2 ОБЯЗАТЕЛЬНО выведи в report_text отдельным разделом «14. Сверка с матрицей решений и картой процессов» как технический аудит-лог — чтобы было видно, что сверка реально проведена по каждой задаче, а не пропущена. По КАЖДОЙ операционной задаче — одна строка формата:\n"
 "`N. <Исполнитель> — <процесс/зона> — по матрице: исполнение <ФИО или —>, контроль <ФИО или —> — источник: «<документ>» — вердикт: совпадает | расхождение | требует уточнения — <короткое пояснение, всё ли корректно>.`\n"
 "Если документы матрицы/карты прочитать не удалось — так и напиши по каждой задаче: «сверка не проведена: источник недоступен», не выдумывай.\n")

conn=psycopg.connect(DBURL,row_factory=dict_row); conn.autocommit=False; cur=conn.cursor()
cur.execute("""SELECT c.id AS category_id, p.* FROM ai_prompt_categories c JOIN ai_prompts p ON p.category_id=c.id
 WHERE c.category_key=%s AND p.is_active=TRUE ORDER BY p.version DESC LIMIT 1""",(CAT_KEY:='zoom_processing',))
# category_id known constant; but keep join robust
row=cur.fetchone()
if not row: print("no active"); sys.exit(1)
text=row["prompt_text"]; ver=row["version"]
if MARK in text:
    print("already v15 (mark present), active v%s"%ver); sys.exit(0)
for old,new,label in R:
    n=text.count(old)
    if n!=1:
        print(f"ABORT {label}: count={n}"); sys.exit(2)
    text=text.replace(old,new,1)
text=text.rstrip()+APPEND
cur.execute("UPDATE ai_prompts SET is_active=FALSE WHERE id=%s",(row["id"],))
cur.execute("""INSERT INTO ai_prompts (category_id,prompt_key,title,prompt_text,version,is_active,created_by_user_id)
 VALUES (%s,%s,%s,%s,%s,TRUE,NULL) RETURNING id,version""",
 (row["category_id"], row["prompt_key"] or "zoom_processing", row["title"] or "Обработка Зумов", text, int(ver)+1))
ins=cur.fetchone(); conn.commit()
print(f"v15 applied: id {ins['id']} version {ins['version']} (len {len(row['prompt_text'])} -> {len(text)})")
