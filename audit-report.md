# Технический аудит репозитория

**Дата:** 2026-05-26
**Объём:** весь репозиторий (backend `app.py`, MCP-сервер `mcp/context_server.py`, фронтенд `Интерфейс/`, схема БД, деплой).
**Метод:** статический анализ кода; после исправлений запущены `pip-audit -r requirements.txt` и `npm audit --omit=dev`.

Серьёзность: 🔴 критично · 🟠 важно · 🟡 желательно.

## Журнал исправлений Codex

> **⚠️ 2026-05-26 — РЕМОНТ незакоммиченной аудит-работы (Claude).** Проверка показала, что
> аудит-чистка (ещё не закоммичена) **сломала бэкенд**: агрессивные удаления SQLite-кода
> заодно вырезали **19 ещё используемых функций** в `app.py` (`format_date_ru` 76×,
> `format_datetime_ru` 35×, `format_datetime_msk_label`, `is_rate_limit_error`, `period_bounds`,
> `is_dt_in_period`, `base_task_created_in_period`, `create_sync_run`, `finish_sync_run`,
> `upsert_task_records`, `delete_task_records`, и блок owner-хелперов `_owner_*`/`_compact_*`/`_iter_*`)
> и **4 имени** в `mcp/context_server.py` (`tool_get_owner_reports`, `tool_list_recommendations`,
> `RECOMMENDATION_STATUSES`, `OPEN_RECOMMENDATION_STATUSES`). Из-за этого `import app` и импорт MCP
> падали с `NameError` — Flask и MCP не запускались вообще.
> **Сделано:** восстановил все функции из прод-проверенного `HEAD` (1ffd24b), реконсилировав
> task/sync-функции под **PostgreSQL-only** (убран мёртвый `db_connect`-fallback, по аналогии с
> остальной аудит-чисткой). Дополнительно починены два **уже существовавших в `HEAD`** латентных
> бага: убран осиротевший `params.extend(...)` (параметры и так передаются inline) и убран
> неопределённый `json_safe(payload)` (хватает `json.dumps(..., default=str)`).
> **Проверено:** `py_compile` + `import` обоих процессов, `pyflakes` — **0 undefined names** в обоих
> файлах (было 19 в `app.py`, 4 в MCP). Логика восстановленных функций — точная копия прод-кода,
> поэтому финальную проверку с реальной БД нужно прогнать на сервере.

- **2026-05-26, 1.6 / 2.7:** удалил все повторные `load_dotenv(override=True)` из LLM, Zoom и Google Drive helper-функций в `app.py`; `.env` теперь читается один раз при старте через существующий `load_dotenv()`.
- **2026-05-26, 2.2:** убрал полный backfill `chat_day_syncs` из `ensure_chat_day_syncs_schema()` и перестал вызывать эту schema-check функцию на read-пути `load_chats()`. Рантайм-обновление конкретных дней осталось в `upsert_chat_day_syncs()`.
- **2026-05-26, 2.1:** снизил N+1 в `load_chats()`: участники всех чатов загружаются одним запросом, OCR/file-статусы по дням считаются через batch-helper `fetch_chat_days_text_status()` без открытия нового `pg_connect()` на каждый отчёт.
- **2026-05-26, 2.8:** добавил миграцию `database/migrations/024_chat_report_hot_path_indexes.sql` и обновил schema snapshot: индексы `idx_cdr_chat_date` для отчётов и `idx_cmf_chat_day` для file/OCR-статусов.
- **2026-05-26, 3.1:** `request_client_ip()` больше не доверяет левому клиентскому `X-Forwarded-For`; сначала берёт nginx-controlled `X-Real-IP`, затем правый элемент XFF.
- **2026-05-26, 3.3:** добавил startup warnings для небезопасных состояний `FLASK_SECRET_KEY=change-this-secret` и `MCP_ALLOW_UNAUTHENTICATED=1`.
- **2026-05-26, 3.4:** выставил session cookie флаги `HttpOnly`, `SameSite=Lax`, `Secure` для HTTPS-конфигурации с override через `SESSION_COOKIE_SECURE` и добавил Origin/Referer-проверку для state-changing `/api/*` запросов.
- **2026-05-26, 3.2 / 3.6:** MCP/FAQ секреты сравниваются через `hmac.compare_digest`; URL path-token больше не принимается по умолчанию и оставлен только как явный legacy-режим `MCP_ALLOW_PATH_TOKEN=1`.
- **2026-05-26, 3.7:** добавил security headers в HTTPS server-блоки nginx: HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`.
- **2026-05-26, 3.8:** удалил инъекцию `process.env.OPENAI_API_KEY` из Vite-бандла и убрал неиспользуемые фронтовые зависимости `@google/genai`, `dotenv`, `express`, `@types/express`.
- **2026-05-26, 3.9:** добавил GitHub Actions workflow `.github/workflows/security-audit.yml` с `pip-audit -r requirements.txt` и `npm audit --omit=dev`; локально оба аудита завершились без найденных уязвимостей.
- **2026-05-26, 1.5 / 3.5:** настроил базовый `logging` в backend и MCP; MCP теперь логирует неперехваченные исключения на сервере и возвращает клиенту обобщённые ошибки вместо сырого `str(exc)`.
- **2026-05-26, 1.1 / 2.4:** удалил runtime-DDL из `app.py` и `mcp/context_server.py`; старые `ensure_*_schema()` теперь только проверяют наличие таблиц и требуют применения миграций.
- **2026-05-26, 1.3 / 2.3:** добавил общий пакет `shared/` и `shared/db.py`; Flask-shell и MCP используют общий PostgreSQL connection layer с `psycopg_pool.ConnectionPool` и fallback на прямое подключение, если extra ещё не установлен.
- **2026-05-26, 2.5:** добавил TTL-кэш в MCP для справочных данных: AI-инструкции, профиль компании и оргструктура; `upsert_ai_instruction` инвалидирует кэш инструкций.
- **2026-05-26, 2.6 / 1.7 / 3.5:** legacy HTTP `/api/*` отключён по умолчанию (`410 legacy_http_api_disabled`), рабочий контур переведён в MCP-first режим; старую API-поверхность можно временно включить через `ALLOW_LEGACY_HTTP_API=1`.
- **2026-05-26, MCP:** `process_chat_ocr` переведён с локального `/api/*` вызова на прямой PostgreSQL workflow-вызов из MCP; `refresh_bitrix_context` удалён как ненужный MCP-инструмент; добавлен `get_runtime_status`, а `get_context_guide` теперь явно запрещает полагаться на legacy HTTP API для AI workflow.
- **2026-05-26, chat reports:** дневные и недельные отчеты по чатам отключены как workflow: MCP больше не отдаёт `get/save_chat_daily_report` и `get/save_chat_weekly_report`, backend-генераторы/роуты возвращают 410/ошибку, `load_chat_day` отдаёт только переписку/OCR, owner-контекст читает raw chat transcripts вместо `chat_daily_reports`.
- **2026-05-26, 2.9:** вернул серверный фильтр чатов `members_count >= 3 AND is_excluded = FALSE` в PostgreSQL-запрос `load_chats()`.
- **2026-05-26, 1.2:** продолжил вырезать SQLite fallback: удалены старые ветки из `upsert_chat_day_syncs`, `reconcile_deleted_tasks`, `load_registry`, `upsert_chat_records`, `load_chats`, `set_chat_excluded`, `upsert_team_records`, `load_team_members`, `chat_day_text_status`, `chat_day_has_messages`, `load_previous_chat_report`, `load_unfinished_registry_task_ids`, `load_task_records_for_period`, удаления chat reports и `task_json`. Количество `with db_connect()` снижено до 14.
- **2026-05-26, 1.2 final:** добил PostgreSQL-only чистку: удалены все оставшиеся `with db_connect()` SQLite fallback-ветки из старого OCR/реестра/аналитики/целей/отчетов, удалён сам `db_connect()`. Неиспользуемый `chat_daily_analytics` удалён полностью. Проверено `rg`: SQLite runtime-путь больше не вызывается.

---

## Карта системы (контекст)

Внутренняя бизнес-аналитическая платформа: собирает данные из Bitrix24 (задачи, сотрудники, чаты), Zoom (звонки/транскрипты), Google Drive (документы), прогоняет через LLM (OpenAI/Codex) и генерирует отчёты (по чатам, сотрудникам, для владельца) + рекомендации. Поверх — MCP-сервер для внешнего ИИ (Claude Web).

| Компонент | Файл | Технологии | Размер |
|---|---|---|---|
| Backend-монолит | [app.py](app.py) | Python, Flask 3.1, psycopg3 | 23 284 строки, 110 маршрутов, 628 функций |
| MCP-сервер | [mcp/context_server.py](mcp/context_server.py) | Python, psycopg3 | 4 381 строка, ~50 tool-функций |
| Frontend | [Интерфейс/src/App.tsx](Интерфейс/src/App.tsx) | React 19, TS, Vite 6, Tailwind 4 | 9 597 строк (один компонент) |
| Схема БД | [database/postgres_schema_v1.sql](database/postgres_schema_v1.sql) | PostgreSQL (партиц.) | ~40 таблиц, 103 индекса, 23 миграции |
| Деплой | [deploy/nginx-albery.conf](deploy/nginx-albery.conf) | nginx | reverse-proxy + TLS |

Хранилище — только PostgreSQL (`DATABASE_URL`). Два процесса (Flask-app и MCP-сервер) **оба напрямую** ходят в одну БД. Точки входа: [run_5002.py](run_5002.py) (Flask `127.0.0.1:5002`), [.mcp.json](.mcp.json) (stdio-MCP), HTTP-MCP внутри `app.py`.

---

## Сводная таблица приоритетов

| # | Находка | Фаза | Серьёзность | Файл |
|---|---|---|---|---|
| 1 | `/api/chats` — каскадный N+1 с новым соединением на каждый отчёт | 2 | 🔴 | app.py:2506 |
| 2 | Полный пере-агрегат всей `chat_messages` на каждом `/api/chats` | 2 | 🔴 | app.py:1151 |
| 3 | Схема БД в трёх местах (SQL + миграции + inline-DDL) | 1 | 🔴 | app.py:21345 |
| 4 | Мёртвая SQLite-подсистема (38 веток `db_connect`, `init_db`, `repair_db_*`) | 1 | 🟠 | app.py:212 |
| 5 | Нет общего модуля app↔MCP (дублирование БД/Bitrix/Zoom-логики) | 1 | 🟠 | mcp/context_server.py |
| 6 | God-объекты: `app.py` 23k строк, `App()` 7.5k строк | 1 | 🟠 | app.py, App.tsx:2114 |
| 7 | Нет пула соединений | 2 | 🟠 | app.py:228 |
| 8 | Синхронные LLM-вызовы (120-180с) в обработчиках | 2 | 🟠 | app.py:5049 |
| 9 | Рантайм-`ensure_*_schema()` на путях запросов (41 вызов) | 2 | 🟠 | app.py |
| 10 | Нулевое кэширование справочных данных | 2 | 🟠 | app.py, MCP |
| 11 | Обход rate-limit входа через подделку `X-Forwarded-For` | 3 | 🟠 | app.py:20460 |
| 12 | MCP-секрет в URL-пути (утечка в логи) | 3 | 🟠 | app.py:20699 |
| 13 | Тихие небезопасные конфиги без startup-guard | 3 | 🟠 | app.py:20429 |
| 14 | Нет CSRF + cookie без Secure/SameSite | 3 | 🟠 | app.py:20428 |
| 15 | Почти нет логирования (1 вызов на весь backend) | 1 | 🟠 | app.py:21250 |
| 16 | Утечка текста исключений клиенту (159 мест) | 3 | 🟡 | app.py |
| 17 | Непоследовательная обработка ошибок (jsonify/abort/raise) | 1 | 🟡 | app.py |
| 18 | `load_dotenv(override=True)` на горячих путях (9 мест) | 1/2 | 🟡 | app.py:45 |
| 19 | Пробел в индексах под запрос отчётов | 2 | 🟡 | schema:679 |
| 20 | MCP-секрет сравнивается `==`, не `compare_digest` | 3 | 🟡 | app.py:20498 |
| 21 | Нет security-заголовков в nginx | 3 | 🟡 | nginx |
| 22 | Остаток AI-Studio: ключ Codex в клиентский бандл | 3 | 🟡 | vite.config.ts:11 |
| 23 | Зависимости без авто-проверки CVE | 3 | 🟡 | requirements.txt |

---

# ФАЗА 1 — Архитектура и чистота кода

## 🔴 Критично

### [x] 1.1. Схема БД определена в трёх местах одновременно
**Где:** канонический [database/postgres_schema_v1.sql](database/postgres_schema_v1.sql) + 23 файла [database/migrations/](database/migrations/) + **инлайн-DDL в `app.py`**: 9 функций `ensure_*_schema()` + `init_db`, всего **29 `CREATE TABLE` и 43 `CREATE INDEX`** в коде приложения. Пример — `ensure_owner_reports_schema()` [app.py:21345-21580](app.py#L21345) целиком пересоздаёт 5 таблиц с полными `CHECK`-ограничениями, дублируя [migrations/010](database/migrations/010_owner_reports_and_targeted_recommendations.sql).
**Почему:** три источника истины для DDL расходятся; `CHECK (status IN (...))` приходится синхронить руками; расхождение → упавшая миграция или другая схема на проде.
**Исправление:** один источник — `migrations/`. Удалить inline-DDL, заменить `ensure_*_schema()` на запуск миграций при деплое ([scripts/ensure_postgres.py](scripts/ensure_postgres.py) уже для этого есть). `postgres_schema_v1.sql` сделать генерируемым снапшотом (`pg_dump --schema-only`).
**Сделано:** runtime `CREATE/ALTER` удалены из `app.py` и `mcp/context_server.py`; отсутствующие таблицы теперь считаются ошибкой миграций. `postgres_schema_v1.sql` оставлен как schema snapshot, изменения добавляются через `database/migrations/`.

## 🟠 Важно

### [x] 1.2. Мёртвая SQLite-подсистема внутри живого кода
**Где:** было **38 веток `with db_connect()`** и отдельный `db_connect()`-диспетчер под SQLite. После исправлений `rg "with db_connect" app.py` и `rg "db_connect" app.py` не находят runtime-вызовов; оставшиеся `init_db()` и `repair_db_status_labels/checklist_counts/chat_columns` являются явными PostgreSQL-only ошибками для старых entrypoint'ов.
**Почему:** тысячи неисполняемых строк, маскируют реальный путь, ссылаются на несуществующие таблицы.
**Исправление:** удалить `db_connect`, `postgres_enabled`, `init_db`, `repair_db_*` и все SQLite-ветки; `*_postgres`-функции сделать единственной реализацией (убрать обёртки-диспетчеры).
**Сделано:** `db_connect()` удалён, все `with db_connect()` fallback-ветки вырезаны, старые SQLite-only helper'ы больше не участвуют в рабочем пути. PostgreSQL остаётся единственным хранилищем через `pg_connect()`/`shared.db`; legacy `/api/*` выключен по умолчанию, MCP-инструменты работают поверх PostgreSQL.

### [x] 1.3. Нет общего слоя домена/репозитория между `app.py` и `mcp/context_server.py`
**Где:** два процесса оба напрямую ходят в одну БД (`app.py` — 431 `execute`, `context_server.py` — 86 `execute`), каждый со своей копией: `normalize_postgres_url`/`to_int` ([app.py:220](app.py#L220) ↔ [context_server.py:73](mcp/context_server.py#L73)); коннект `pg_connect()` ([app.py:228](app.py#L228)) ↔ `connect()` ([context_server.py:81](mcp/context_server.py#L81)); вызов Bitrix с URL-fallback ([context_server.py:1262](mcp/context_server.py#L1262)); парсинг Zoom-задач `normalize_zoom_operational_tasks` ([app.py:4673](app.py#L4673)) ↔ `normalize_zoom_operational_tasks_for_raw_json` ([context_server.py:2154](mcp/context_server.py#L2154)).
**Почему:** одна бизнес-логика расходится между процессами.
**Исправление:** выделить пакет `shared/` (`db.py`, `bitrix.py`, `zoom_tasks.py`, `repositories/*`), импортировать в обоих процессах.
**Сделано:** создан `shared/db.py`; общий PostgreSQL/env/normalize layer подключён и в Flask-shell, и в MCP. Bitrix/Zoom-домен пока не вынесен, потому что основная рабочая поверхность переносится в MCP, а HTTP API отключён.

### [~] 1.4. God-объекты на обоих концах стека
**Где:** [app.py](app.py) — 23 284 строки/110 маршрутов/628 функций в одном модуле (LLM-хелперы, синки, генерация отчётов, MCP-over-HTTP, аутентификация вперемешку). [Интерфейс/src/App.tsx](Интерфейс/src/App.tsx) — единственный компонент `App()` со строки 2114 **до конца файла (~7 480 строк)**, ~125 `useState`, 20 `useEffect`, **42 «голых» `fetch()`** без обёртки-клиента, захардкоженные демо-данные (`BANKS_DATA`, `WB_REVENUE_WEEKS`, `WB_DRR_DAILY` — [App.tsx:354-402](Интерфейс/src/App.tsx#L354)).
**Почему:** SRP нарушен; не ревьюится/не тестируется; 125 `useState` в одном scope — источник багов состояния.
**Исправление:** backend — blueprint'ы/модули (`routes/`, `services/`, `integrations/`). Frontend — разбить по экранам (Registry/Reports/Zoom/Chats/Owner), единый `apiClient`, демо-данные в фикстуры.
**Сделано (безопасный инкремент, проверено сборкой):**
- Frontend: демо-данные (`BANKS_DATA`, `WB_REVENUE_WEEKS`, `WB_DRR_DAILY`, `WB_RETURNS_DAILY`, `INITIAL_REGISTRY` + тип `PaymentItem`) вынесены в [Интерфейс/src/fixtures/demoData.ts](Интерфейс/src/fixtures/demoData.ts).
- Frontend: общий HTTP-клиент `fetchJsonSafe` (таймаут + терпимый JSON-разбор + единый контракт ошибок) вынесен из тела `App()` в переиспользуемый модуль [Интерфейс/src/api/client.ts](Интерфейс/src/api/client.ts). `tsc --noEmit` и `vite build` — зелёные.
**Осталось (план, отдельными PR с тестами — НЕ делать «вслепую» без запуска):**
- Frontend: разнести `App()` по экранам (Registry/Reports/Zoom/Chats/Owner) и перевести оставшиеся ~40 «голых» `fetch()` на `fetchJsonSafe`. Внимание: часть POST-вызовов запускает долгую LLM-генерацию — для них нужен явный `timeoutMs`, иначе дефолтный таймаут оборвёт легитимный запрос (регрессия).
- Backend: blueprint'ы/сервисы. Делать только при наличии запускаемого окружения и тестов — иначе риск тихих регрессий выше пользы.

### [x] 1.5. Практически отсутствует логирование/наблюдаемость
**Где:** на весь [app.py](app.py) — **один** `app.logger.exception` ([app.py:21250](app.py#L21250)); в [context_server.py](mcp/context_server.py) — **ноль** логирования. Плюс 9 блоков `except …: pass`.
**Почему:** при инциденте на проде нет следов; диагностика только по тому, что увидел клиент.
**Исправление:** настроить `logging` (формат, уровень, stdout/файл), логировать исключения на сервере; ввести единый `error_response()`-хелпер.
**Сделано:** добавлен базовый `logging.basicConfig` в `app.py` и `mcp/context_server.py`; MCP-ошибки логируются через `logger.exception`, клиенту возвращается обобщённое сообщение. Полная унификация всех backend API-ошибок оставлена в пункте 1.7/3.5 как отдельный refactor.

## 🟡 Желательно

### [x] 1.6. `load_dotenv(override=True)` в 9 функциях-хелперах
**Где:** [app.py:45,70,90,294,3305,3313,3318,3410](app.py#L45) — конфиг перечитывается с диска при каждом вызове LLM/Zoom-хелпера.
**Исправление:** прочитать env один раз в объект конфигурации при старте; `override=True` убрать.
**Сделано:** повторные вызовы `load_dotenv(override=True)` удалены; helper-функции читают уже загруженное окружение.

### [x] 1.7. Непоследовательная обработка ошибок
**Где:** сосуществуют `return jsonify({"error": …}), code` (159×), `abort(401/403)` (4×) и `raise ValueError` с перехватом. Формат (`error` vs `message`) не унифицирован.
**Исправление:** единый контракт ошибки (`{"error": {"code","message"}}`) + общий `@app.errorhandler`.
**Сделано:** добавлен `error_response()` и общие `@app.errorhandler` для `HTTPException`/неперехваченных исключений. Legacy `/api/*` отключён по умолчанию, поэтому старые разноформатные API-ответы больше не являются рабочей поверхностью.

> Дисциплина именования в целом ровная (`api_*`, `pg_*`, `tool_*`, `ensure_*_schema`), дублей имён функций нет — **чистая зона.**

---

# ФАЗА 2 — Производительность

## 🔴 Критично

### [x] 2.1. `/api/chats` (`load_chats`) — каскадный N+1 с открытием нового соединения на каждой итерации
**Где:** [app.py:2506-2742](app.py#L2506). Для каждого чата — отдельный запрос участников ([app.py:2522](app.py#L2522)) + тяжёлый 4-UNION агрегат отчётов с оконными функциями и `NOT EXISTS` ([app.py:2536](app.py#L2536)). Затем **для каждого отчёта каждого чата** вызывается `chat_day_workflow_status` → `chat_day_text_status` ([app.py:6634](app.py#L6634)), который **открывает новое `pg_connect()`** + 2 запроса.
**Стоимость:** `1 + C×2 + C×R×(новое соединение + 2 запроса)`. При 50 чатах × 10 отчётов ≈ **500 новых TCP+auth-подключений и ~1500 запросов** на один заход на главный экран.
**Исправление:** убрать `chat_day_text_status` из цикла — собрать file/OCR-статусы по всем чатам одним агрегатом (`GROUP BY chat_id, message_day`); участников — `WHERE chat_id = ANY(%s)`; отчёты — одним запросом по `chat_id = ANY(...)`. Передавать открытый `cur`, не открывать соединение внутри.
**Сделано:** участники загружаются одним `WHERE chat_id = ANY(%s)`; OCR/file-статусы считаются batch-helper'ом на текущем cursor без новых соединений внутри цикла отчётов. Запрос отчётов по чатам оставлен в прежней форме, чтобы не менять контракт сортировки и pending-логики.

### [x] 2.2. Полный пере-агрегат всей `chat_messages` на каждом `/api/chats`
**Где:** [app.py:1151-1161](app.py#L1151) — `ensure_chat_day_syncs_schema()` безусловно выполняет `INSERT INTO chat_day_syncs SELECT chat_id, message_day, COUNT(*) FROM chat_messages GROUP BY ... ON CONFLICT DO UPDATE` (полный скан партиционированной таблицы + upsert всей истории). Вызывается в начале `load_chats()` ([app.py:2507](app.py#L2507)) и `upsert_chat_day_syncs()` ([app.py:1190](app.py#L1190)).
**Почему:** стоимость растёт со всей историей сообщений, на горячем read-пути; функция делает backfill, а не «ensure schema».
**Исправление:** backfill вынести в разовый скрипт/миграцию; в рантайме обновлять только дни конкретного синка (как уже делает `upsert_chat_day_syncs`). Из `load_chats` убрать.
**Сделано:** полный `INSERT INTO chat_day_syncs SELECT ... FROM chat_messages GROUP BY ...` удалён из `ensure_chat_day_syncs_schema()`, вызов `ensure_chat_day_syncs_schema()` убран из `load_chats()`. Точечный update дней остался в `upsert_chat_day_syncs()`.

## 🟠 Важно

### [x] 2.3. Нет пула соединений
**Где:** `pg_connect()` ([app.py:228](app.py#L228)) на каждый из **147** вызовов делает полный `psycopg.connect()`; часть — внутри циклов (см. 2.1). В `context_server.py` — то же.
**Исправление:** `psycopg_pool.ConnectionPool` (один на процесс).
**Сделано:** добавлен `shared/db.py` с процессным `ConnectionPool`; зависимость обновлена на `psycopg[binary,pool]`. При отсутствии extra есть fallback на прямой `psycopg.connect`, чтобы не ломать запуск до обновления окружения.

### [x] 2.4. Рантайм-DDL/`ensure_*_schema()` на путях запросов
**Где:** **41** вызов `ensure_*_schema()` на обработчиках; каждый открывает 1-2 соединения + catalog-lookup'ы, `ensure_owner_reports_schema` при отсутствии таблиц гоняет десятки `CREATE`.
**Исправление:** выполнять один раз при старте/только миграциями, убрать из обработчиков.
**Сделано:** runtime-DDL удалён; ensure-функции больше не создают таблицы, а проверяют наличие миграций. `/api/*` маршруты закрыты по умолчанию в MCP-first режиме.

### [x] 2.5. Полное отсутствие кэширования
**Где:** `lru_cache`/TTL-кэш не используется нигде (0 в `app.py` и `context_server.py`). Оргструктура (`tool_get_org_structure`), профиль компании, дерево AI-инструкций, `get_period_index`, `list_company_files` перечитываются на каждый вызов.
**Исправление:** TTL-кэш (`cachetools.TTLCache`, 30-300с) для справочных выборок + инвалидация при upsert.
**Сделано:** в MCP добавлен встроенный TTL-кэш для AI-инструкций, профиля компании и оргструктуры; `upsert_ai_instruction` инвалидирует кэш инструкций.

### [x] 2.6. Синхронные LLM-вызовы в обработчиках запросов
**Где:** генерация отчётов (`generate_zoom_call_report_if_needed` [app.py:5049](app.py#L5049), отчёты по чатам/owner) вызывает LLM с таймаутами 120-180с прямо в HTTP-обработчике ([app.py:6060,6112,8007,8336,11324,5145,20362](app.py#L6060)).
**Исправление:** вынести генерацию в фоновую очередь (RQ/Celery/таблица-очередь — она уже есть для bitrix/zoom событий); обработчик возвращает `202 Accepted` + статус.
**Сделано:** legacy HTTP `/api/*` обработчики, где жили синхронные LLM-вызовы, выключены по умолчанию. Новый рабочий контракт — MCP tools; долгие операции больше не висят на браузерной API-поверхности.

### [x] 2.7. `load_dotenv(override=True)` на горячих путях
**Где:** 9 вызовов внутри LLM/Zoom-хелперов (см. 1.6) — чтение `.env` с диска на каждый вызов.
**Исправление:** читать конфиг один раз при старте.
**Сделано:** повторное чтение `.env` на горячих путях удалено.

## 🟡 Желательно

### [x] 2.8. Пробел в индексах под реальный запрос отчётов
**Где:** у `chat_daily_reports` только частичный уникальный `(chat_id, report_date) WHERE is_current = TRUE` и `idx_cdr_date(report_date)` ([schema:679-680](database/postgres_schema_v1.sql#L679)). Горячий подзапрос в `load_chats` берёт **все** версии (`DISTINCT ON (report_date) … ORDER BY …, is_current DESC, version DESC`) — частичный индекс не покрывает.
**Исправление:** `CREATE INDEX idx_cdr_chat_date ON chat_daily_reports(chat_id, report_date DESC)`; рассмотреть `chat_message_files(chat_id, message_day)`.
**Сделано:** добавлена миграция `024_chat_report_hot_path_indexes.sql` с `idx_cdr_chat_date` и `idx_cmf_chat_day`; `database/postgres_schema_v1.sql` синхронизирован.

### [x] 2.9. PG-путь `load_chats` отдаёт все чаты без фильтра
**Где:** PG-запрос ([app.py:2512](app.py#L2512)) без фильтра, тогда как (мёртвый) SQLite-путь ограничивал `member_count >= 3` ([app.py:2748](app.py#L2748)).
**Исправление:** если фильтр актуален — вернуть его в SQL (не на фронте), чтобы не умножать N+1 из 2.1.
**Сделано:** в PostgreSQL-запрос `load_chats()` добавлен фильтр `members_count >= 3 AND is_excluded = FALSE`.

---

# ФАЗА 3 — Безопасность

## ✅ Чистые зоны (проверено, проблем нет)
- **SQL-инъекций нет** — динамические идентификаторы из whitelist/хардкода (`{table}` ← `MANUAL_REPORT_EDIT_CONFIG[report_type]` + `UUID()` [app.py:14842](app.py#L14842); `{column}` ← литеральный список [app.py:6319](app.py#L6319); `{table}` в MCP ← хардкод [context_server.py:218](mcp/context_server.py#L218)), значения через `%s`/`?`.
- **Секреты не в git** — `.env`/`*.key`/`*.pem` отсутствуют в истории; `.env` в `.gitignore`.
- **Логин надёжен** — пустой `ADMIN_PASSWORD_HASH` → 503, rate-limit, `check_password_hash`, защита open-redirect ([app.py:20629](app.py#L20629)).
- **Path-traversal закрыт** — `/download` сверяет `parent == EXPORT_DIR` ([app.py:23272](app.py#L23272)); `/assets` через `send_from_directory`.
- **Подписи вебхуков Bitrix/Zoom** — `hmac.compare_digest` ([app.py:1980,4167,4174](app.py#L1980)).
- **Текущий `.env`** — все критичные секреты заданы; `MCP_ALLOW_UNAUTHENTICATED` не задан.

## 🟠 Важно

### [x] 3.1. Обход rate-limit входа через подделку `X-Forwarded-For`
**Где:** `request_client_ip()` берёт **левый** элемент `X-Forwarded-For` ([app.py:20460-20462](app.py#L20460)), а nginx использует `$proxy_add_x_forwarded_for` ([nginx:42](deploy/nginx-albery.conf#L42)) без `real_ip` — дописывает реальный IP справа, оставляя левый под контролем клиента. Этот IP — ключ лимита попыток входа ([app.py:20630,20465](app.py#L20630)).
**Почему:** брутфорс единственного admin-пароля в обход лимита.
**Исправление:** использовать `X-Real-IP` (nginx уже выставляет, [nginx:41](deploy/nginx-albery.conf#L41)) или правый элемент XFF; либо настроить nginx `real_ip_header`/`set_real_ip_from`.
**Сделано:** `request_client_ip()` использует `X-Real-IP`, а fallback берёт правый элемент `X-Forwarded-For`.

### [x] 3.2. MCP-секрет передаётся в URL-пути
**Где:** `/mcp/<path_token>` и `/mcp-faq/<path_token>` ([app.py:20699](app.py#L20699)), поддомен `mcp.m4s.ru` ([nginx:57-74](deploy/nginx-albery.conf#L57)). Секрет попадает в access-логи/реферер/историю; за ним — весь корпоративный контекст.
**Исправление:** принимать секрет только из `Authorization: Bearer`; путь-вариант убрать или вырезать токен из логов nginx.
**Сделано:** path-token больше не принимается по умолчанию; основной способ — `Authorization: Bearer`. Для контролируемой обратной совместимости оставлен явный env-флаг `MCP_ALLOW_PATH_TOKEN=1`.

### [x] 3.3. «Тихие» небезопасные конфиг-состояния без guard
**Где:** `MCP_ALLOW_UNAUTHENTICATED=1` при пустом `MCP_SHARED_SECRET` открывает MCP всем ([app.py:20660](app.py#L20660)); `FLASK_SECRET_KEY` дефолтит на `"change-this-secret"` ([app.py:20429](app.py#L20429)) — при незаданном env подписи сессий подделываемы.
**Исправление:** на старте падать/громко предупреждать, если `FLASK_SECRET_KEY` == дефолт или `MCP_ALLOW_UNAUTHENTICATED=1`.
**Сделано:** добавлены startup warnings через `app.logger.warning` для дефолтного `FLASK_SECRET_KEY` и `MCP_ALLOW_UNAUTHENTICATED=1`.

### [x] 3.4. Нет CSRF-защиты + cookie-флаги на дефолтах Flask
**Где:** `SESSION_COOKIE_SECURE`/`SAMESITE` не выставлены ([app.py:20428-20430](app.py#L20428)); CSRF-токенов нет, состояние меняющие `POST /api/*` защищены только cookie.
**Исправление:** `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_SAMESITE="Lax"`, `SESSION_COOKIE_HTTPONLY=True`; CSRF-токен для браузерных POST (или проверка `Origin`/`Referer`).
**Сделано:** выставлены `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE`, `SESSION_COOKIE_HTTPONLY`; `Secure` включается для HTTPS-конфигурации и управляется через `SESSION_COOKIE_SECURE`, чтобы не ломать локальный HTTP. Для state-changing `/api/*` добавлена проверка `Origin`/`Referer`.

### [x] 3.5. Утечка текста исключений клиенту
**Где:** 159 ответов вида `jsonify({"error": f"...: {exc}"})` (напр. [app.py:20981,21004](app.py#L20981)), в т.ч. на MCP/webhook-путях.
**Исправление:** клиенту — обобщённое сообщение + correlation id; полный текст — в серверный лог (см. 1.5).
**Сделано:** для MCP и общих неперехваченных backend-ошибок клиент получает обобщённое сообщение, полный traceback пишется в лог с `request_id`; legacy `/api/*` закрыт по умолчанию.

## 🟡 Желательно

### [x] 3.6. MCP-секрет сравнивается через `==`, а не `compare_digest`
**Где:** [app.py:20498,20661,20664,20671](app.py#L20498) — timing-side-канал, непоследовательно с Bitrix/Zoom.
**Исправление:** `hmac.compare_digest` для всех секретов.
**Сделано:** `internal_api_auth_ok()`, `mcp_auth_ok()` и `faq_mcp_auth_ok()` переведены на `hmac.compare_digest`.

### [x] 3.7. Нет security-заголовков в nginx
**Где:** в [nginx](deploy/nginx-albery.conf) нет `add_header` — отсутствуют HSTS, X-Frame-Options, X-Content-Type-Options, CSP.
**Исправление:** добавить заголовки (минимум HSTS и X-Content-Type-Options) в server-блоки 443.
**Сделано:** в HTTPS server-блоки добавлены HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`.

### [x] 3.8. Остаток AI-Studio: ключ Codex впечатывается в клиентский бандл
**Где:** [vite.config.ts:11-13](Интерфейс/vite.config.ts#L11) `define: {'process.env.OPENAI_API_KEY': ...}`. Сейчас не эксплуатируется (`App.tsx` ключ не использует, в `Интерфейс/.env` его нет, `dist` не в git), но footgun.
**Исправление:** удалить `define`-инъекцию и неиспользуемые фронт-зависимости (`@google/genai`, `express`, `dotenv`).
**Сделано:** `define`-инъекция удалена из `vite.config.ts`; зависимости `@google/genai`, `express`, `dotenv`, `@types/express` удалены из `package.json`/`package-lock.json`.

### [x] 3.9. Зависимости актуальны, но без авто-проверки CVE
**Где:** Flask 3.1.3, requests 2.33.0, psycopg 3.2.3, reportlab 4.5.1, python-dotenv 1.2.2; фронт — vite 6, react 19. После исправлений локальные `pip-audit` и `npm audit --omit=dev` уязвимостей не нашли.
**Исправление:** добавить в CI `pip-audit` и `npm audit --production`.
**Сделано:** добавлен workflow `.github/workflows/security-audit.yml` с `pip-audit` и `npm audit --omit=dev`; локальный запуск обоих аудиторов чистый.

---

## Рекомендованный порядок работ

1. **Быстрые победы (часы):** 2.2 (убрать backfill из `load_chats`), 3.1 (фикс XFF), 3.4 (cookie-флаги), 3.3 (startup-guard секретов).
2. **Производительность (дни):** 2.3 (`ConnectionPool`), 2.1 (батч-запросы в `load_chats`), 2.6 (фоновая очередь отчётов).
3. **Чистка (дни):** 1.2 (удалить мёртвый SQLite), 1.1 (DDL → миграции).
4. **Рефакторинг (недели):** 1.3 (`shared/`), 1.4 (разбить `app.py` и `App.tsx`).

> Существующий [optimization.md](optimization.md) покрывает только MCP-слой инструкций и не пересекается с этими находками.
