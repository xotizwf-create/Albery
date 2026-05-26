# Тесты

Покрывают то, что просили: вытягивание задач из Bitrix, созвонов Zoom,
оргструктуры (пользователи/отделы) из Bitrix, Google-документов, доступность
инструментов/инструкций в обоих MCP-серверах (полный `/mcp` + `/mcp-faq`), и
живость БД.

## Как запускать

```bash
pip install -r requirements-dev.txt
pytest                       # всё; БД-тесты пропускаются без DATABASE_URL
pytest tests/unit            # только чистая логика
pytest tests/mcp             # контракт/консистентность MCP
pytest tests/integration     # синки с моками HTTP + фейковой БД
pytest -m "not db"           # явно без БД
```

БД-тесты (`-m db`) идут только при заданном `DATABASE_URL` (его даёт CI —
сервисный PostgreSQL + `scripts/ensure_postgres.py`). Они используют `SELECT 1`,
MCP `health`, проверку наличия таблиц и CRUD во **временной** таблице
(`CREATE TEMP TABLE`) — безопасно даже против реальной БД.

## Слои

| Каталог | Что проверяет | Нужна БД/сеть |
|---|---|---|
| `tests/test_smoke.py` | `import app` (роуты), импорт MCP (инструменты), `pyflakes` 0 undefined | нет |
| `tests/unit/` | форматтеры/даты, парсинг Bitrix-задач, нормализация Zoom-задач | нет |
| `tests/mcp/` | реестр инструментов, FAQ ⊆ полного, схемы/handler'ы, `/mcp` vs `/mcp-faq`, ссылки в инструкциях | нет |
| `tests/integration/` | сборка Bitrix-задачи, синк задач/удаление, list_users, Zoom-синк, Google Drive | моки (без сети) |
| `tests/db/` | коннект, `health`, наличие таблиц, CRUD-roundtrip | да (CI / `DATABASE_URL`) |

## Чем мокаются интеграции

- **Bitrix**: фейковый `BitrixClient` (duck-typed) в `tests/integration/test_bitrix_sync.py`
  и патч `pg_connect` через фикстуру `fake_pg` (см. `conftest.py`).
- **Zoom**: патч `zoom_list_users`/`zoom_list_recordings`/`upsert_zoom_recording_meeting` + `fake_pg`.
- **Google Drive**: патч `requests.get` и `google_drive_company_sync_config`.

## CI

`.github/workflows/tests.yml` — две джобы: `backend` (поднимает PostgreSQL 16,
применяет схему, гоняет весь `pytest` включая БД-тесты) и `frontend`
(`npm ci` + `npm run lint` + `npm run build`).

## Дневные/недельные отчёты по чатам — удалены

Workflow дневных/недельных отчётов по чатам отключён: инструменты
`get/save_chat_daily_report` и `get/save_chat_weekly_report` убраны из реестра,
а инструкции `daily_chat_report.md` / `weekly_chat_report.md` удалены.
`tests/mcp/test_instructions.py` теперь строго проверяет, что эти инструменты не
вернутся и не упоминаются ни в одной инструкции.
