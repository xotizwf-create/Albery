# Этап C — аудит качества, технических рисков и стандартизации Albery

Дата: 2026-06-02
Режим: read-only аудит локальной копии `/root/audit-work/Albery`.

В этом этапе код проекта, продакшен, секреты и живая БД не менялись. Тяжёлые сборки и миграции не запускались. Единственное фактическое выполнение — безопасный тестовый прогон без БД, который остановился на неподготовленном audit-окружении без проектных зависимостей.

Связанные артефакты:
- `audit-stage-b-mcp-boundary.md` — MCP как граница доступа ИИ.
- `audit-stage-b-action-matrix.md` — матрица backend/UI/MCP действий и рисков.

---

## 1. Краткий вывод

Albery уже не является маленьким Bitrix weekly export mini app. По факту это большая управленческая система с несколькими контурами:

- backend Flask-приложение;
- React/Vite веб-интерфейс;
- MCP-сервер для Hermes/ИИ;
- PostgreSQL-схема и миграции;
- интеграции Bitrix, Zoom, Google Drive / Apps Script;
- AI-промпты, AI-инструкции и Hermes cron/workflow;
- webhook-контур для событий Bitrix, Zoom и Google Drive;
- генерация отчётов, OCR, owner-рекомендации и отправки в Bitrix.

Главная проблема не в том, что проект “не работает”, а в том, что он вырос быстрее, чем стандарты вокруг него:

1. Огромные центральные файлы стали системными точками риска.
2. Есть дублирующиеся поверхности управления: MCP, legacy HTTP API и UI.
3. Документация разных эпох противоречит текущей архитектуре.
4. Confirmation rules частично стандартизированы, но не везде одинаковы.
5. Тесты и CI есть, но покрытие больше похоже на набор регрессий под уже найденные проблемы, а не на полный safety net для всех опасных действий.
6. Миграционная схема есть, но её стандарт нужно явно описать.
7. Импорт `mcp.context_server` хрупок из-за конфликта имени `mcp` с установленным Python-пакетом.

---

## 2. Инвентаризация структуры

Крупнейшие файлы по строкам:

- `app.py` — 21 947 строк.
- `Интерфейс/src/App.tsx` — 9 474 строки.
- `mcp/context_server.py` — 5 054 строки.
- `agent.md` — 1 862 строки.
- `database/postgres_schema_v1.sql` — 1 379 строк.
- `scripts/google_drive_company_sync_project/Code.gs` — 648 строк.
- `scripts/google_drive_company_sync.gs` — 501 строк.
- `scripts/zoom_processing_prompt_v9.md` — 384 строки.

### Основные зоны проекта

- Backend:
  - `app.py` — основной Flask backend, маршруты, интеграции, отчёты, workflow, Bitrix/Zoom/Drive logic.
  - `run_5002.py` — запуск на порту 5002.
  - `shared/db.py` — общий слой подключения к PostgreSQL.

- MCP:
  - `mcp/context_server.py` — полный MCP-сервер и FAQ subset.
  - `mcp/instructions/*.md` — инструкции для workflow.
  - `mcp/README.md` — документация MCP, сейчас устарела.

- Frontend:
  - `Интерфейс/src/App.tsx` — почти весь UI в одном файле.
  - `Интерфейс/src/api/client.ts` — API-клиент.
  - `Интерфейс/package.json` — frontend-команды.

- БД:
  - `database/postgres_schema_v1.sql` — базовая схема.
  - `database/migrations/002...025_*.sql` — миграции поверх базовой схемы.
  - `scripts/ensure_postgres.py` — применение схемы и миграций.

- Тесты:
  - `tests/unit/*` — чистая логика.
  - `tests/integration/*` — интеграционные тесты с fake PostgreSQL/HTTP mock.
  - `tests/mcp/*` — контракты MCP, инструкции, endpoint-поведение.
  - `tests/db/*` — реальные DB-тесты, должны идти только при наличии тестовой БД.

- CI:
  - `.github/workflows/tests.yml` — backend + frontend тесты.
  - `.github/workflows/security-audit.yml` — аудит зависимостей Python и frontend.

---

## 3. Метрики сложности

### Backend `app.py`

- 21 947 строк.
- 676 функций.
- 112 backend-маршрутов по декораторам.
- 74 маршрута `/api/*`.

Самые крупные функции:

- `sync_google_drive_company_documents` — 398 строк.
- `analyze_chat_work_with_ai` — 330 строк.
- `structured_chat_report_text` — 305 строк.
- `local_weekly_chat_summary` — 276 строк.
- `build_chat_overall_daily_report_payload` — 263 строки.
- `generate_ai_chat_report` — 210 строк.
- `upsert_zoom_recording_meeting` — 185 строк.
- `load_previous_chat_tail_tasks` — 185 строк.
- `process_chat_image_ocr_for_period` — 182 строки.
- `load_work_registry` — 177 строк.

Вывод: `app.py` — главный god-object проекта. Сейчас он одновременно содержит:

- HTTP-маршруты;
- auth/session logic;
- Bitrix API;
- Zoom API;
- Google Drive sync;
- OCR workflow;
- генерацию отчётов;
- owner reports;
- AI contract/prompt logic;
- отправки в Bitrix;
- webhook handlers;
- DB-запросы.

Это делает изменения опасными: маленькая правка в одном workflow может затронуть импорт, env, глобальные хелперы или общий routing.

### Frontend `Интерфейс/src/App.tsx`

- 9 474 строки.
- UI содержит много ручных действий и вызовов `/api/*`.
- По этапу B найдено 54 frontend-вызова `/api/*`.

Вывод: frontend тоже стал god-object. У UI нет чёткой модульной границы между:

- отчетами;
- задачами;
- Zoom;
- Drive;
- AI-инструкциями;
- настройками;
- legacy/admin actions.

### MCP `mcp/context_server.py`

- 5 054 строки.
- 112 функций.
- 51 инструмент по этапу B.

Самые крупные функции:

- `tool_get_context_guide` — 145 строк.
- `tool_get_report_readiness` — 121 строк.
- `tool_list_recommendations` — 99 строк.
- `tool_save_recommendation_event` — 97 строк.
- `tool_get_recommendation_feedback_context` — 96 строк.
- `_resolve_active_bitrix_users` — 94 строки.
- `tool_search_tasks` — 86 строк.
- `tool_fetch_url` — 85 строк.
- `tool_get_task_comments` — 84 строки.
- `tool_search_messages` — 80 строк.

Вывод: MCP уже не просто “тонкий read-only адаптер”. Это полноценная control plane для ИИ, где есть чтение, записи, workflow и внешние отправки.

---

## 4. Тесты и CI

### Что хорошо

В проекте есть полноценная структура тестов:

- unit-тесты форматтеров, Bitrix parsing, Zoom parsing;
- интеграционные тесты Bitrix/Drive/Zoom с fake DB и mock HTTP;
- MCP contract-тесты:
  - каждый инструмент должен иметь описание, schema и handler;
  - FAQ subset должен быть настоящим подмножеством full MCP;
  - удалённые chat-report tools не должны вернуться;
  - MCP-инструкции не должны ссылаться на несуществующие инструменты;
- DB-тесты отделены marker `db`;
- CI поднимает PostgreSQL 16 и применяет schema + migrations;
- frontend в CI проходит `npm ci`, type-check/lint и build;
- отдельный security audit делает `pip-audit` и `npm audit --omit=dev`.

### Что проверялось в audit-окружении

Был запущен безопасный тестовый прогон без DB:

- команда: `pytest -q -m "not db"`;
- результат: тесты не дошли до проверки логики из-за отсутствующих зависимостей в текущем audit-окружении;
- ошибки: отсутствовали проектные библиотеки вроде Flask и psycopg;
- это не доказательство поломки проекта, потому что CI ставит зависимости через `requirements-dev.txt`.

### Найденный переносимый риск тестов

Папка `mcp` в проекте не содержит `__init__.py`.

В текущем окружении импорт показал:

- `mcp` резолвится в установленный внешний Python-пакет `mcp`;
- `mcp.context_server` не находится;
- `app` резолвится в проектный `app.py`.

Риск: тесты и локальные импорты `import mcp.context_server` могут ломаться в окружениях, где установлен внешний пакет `mcp`. Это уже произошло в audit-окружении. CI может проходить, если там нет такого пакета, но переносимость хрупкая.

Рекомендация: добавить `mcp/__init__.py` или переименовать проектный пакет во что-то уникальное, например `albery_mcp`. Самый маленький безопасный патч — добавить пустой `mcp/__init__.py` и проверить тесты.

### Пробелы тестового покрытия

Нужны отдельные регрессии на:

1. `create_bitrix_task` требует server-side confirm или явно documented exception.
2. Все внешние Bitrix-send/delete/dispatch действия имеют единый confirm-gate.
3. Legacy HTTP API не позволяет внешние отправки без серверного подтверждения.
4. MCP tool registry содержит machine-readable риск-класс для каждого инструмента.
5. `fetch_url` ограничен политикой внешних запросов или явно помечен как external read.
6. AI-инструкции меняются через контролируемый workflow и не считаются обычным read-only действием.
7. `/api/*` действительно недоступен без `ALLOW_LEGACY_HTTP_API=1`.

---

## 5. БД и миграции

### Что хорошо

- Есть базовая схема `database/postgres_schema_v1.sql`.
- Есть миграции `002`–`025`, без дыр внутри диапазона 002–025.
- Пропуск `001` объясним: базовая схема выступает как v1, а миграции идут начиная с `002`.
- `scripts/ensure_postgres.py` умеет:
  - создать БД при необходимости;
  - применить базовую схему;
  - применить обязательные миграции по наличию таблиц/функций;
  - всегда применять горячие индексные миграции `022`, `024`, `025`.

### Риски

1. Стандарт “base schema + migrations from 002” не описан достаточно явно как контракт проекта.
2. Миграции применяются скриптом с логикой “если таблицы нет — применить”, а не полноценным журналом миграций.
3. Индексные миграции из `ALWAYS_APPLY_MIGRATIONS` исполняются каждый раз. Это может быть нормально при `IF NOT EXISTS`, но стандарт нужно зафиксировать.
4. Runtime-код и migration-код частично смешаны через общие env и DDL-скрипты.
5. Перед любыми изменениями в схеме нужен запрет на применение миграций по боевой БД без отдельного preflight и backup-check.

Рекомендация: не переписывать миграционную систему сейчас. Сначала описать текущий стандарт и добавить минимальные тесты/документацию.

---

## 6. Документационный дрейф

### Корневой `README.md`

Сейчас начинается как:

- `Bitrix24 Weekly Export (Mini App)`;
- описывает выбор недели, JSON export, старые Windows PowerShell команды.

Это уже не соответствует реальному Albery.

### `mcp/README.md`

Сейчас называет MCP `Read-only MCP server`, но этап B показал, что MCP содержит:

- чтение;
- OCR workflow;
- записи owner reports;
- изменение AI-инструкций;
- создание/удаление Bitrix tasks;
- отправки PDF/сообщений/дайджестов;
- внешние HTTP-запросы через `fetch_url`.

Это критичный документационный дрейф: оператор или агент может принять write/action tools за безопасное чтение.

### `docs/about-project.md`

Это лучший текущий кандидат на источник правды, но он сам говорит, что это “зерно” и подробно описывает только последнюю надстройку Zoom leader evaluation.

### `agent.md`

Содержит много живого operational-контекста, но файл слишком большой и смешивает:

- server context;
- recent production changes;
- branch workflow;
- playbooks;
- cron/Hermes/deploy детали;
- исторические заметки.

Риск: новые участники не понимают, что читать первым и чему верить.

---

## 7. Поверхности управления и дублирование

По этапу B:

- backend содержит 112 маршрутов, из них 74 `/api/*`;
- frontend содержит 54 вызова `/api/*`;
- `/api/*` по умолчанию закрывается как legacy через `ALLOW_LEGACY_HTTP_API`;
- активные контуры без legacy-флага: MCP/SSE, frontend shell/assets, tokenized Zoom export, вебхуки Bitrix/Zoom/Drive.

Риск: UI всё ещё выглядит как активная admin-поверхность, но backend считает `/api/*` legacy. Это создаёт drift между тем, что видит человек, и тем, что реально считается поддерживаемым контуром.

Нужен архитектурный стандарт:

- MCP-first для ИИ и Hermes;
- legacy HTTP API либо официально выключен, либо превращён в `admin API` с теми же confirm rules;
- UI либо поддерживаемый admin UI, либо помеченный legacy shell;
- каждое действие получает risk class и confirmation policy.

---

## 8. Приоритеты рисков

### Критично

1. **Неединые confirmation rules для внешних действий.**
   - `create_bitrix_task` выбивается из общего стандарта confirm-gate.
   - Legacy HTTP API содержит external action routes без очевидного server-side `confirm=true`.

2. **MCP README ложно говорит read-only.**
   - Это опасно, потому что MCP уже умеет писать и отправлять наружу.

3. **God-object `app.py`.**
   - 21 947 строк и 676 функций.
   - Любая правка сложна для review и может иметь неожиданные side effects.

4. **Хрупкий импорт `mcp.context_server`.**
   - Папка `mcp` конфликтует с внешним Python-пакетом `mcp`.
   - В audit-окружении импорт проекта через `import mcp.context_server` не сработал.

### Важно

5. **Frontend god-object `App.tsx`.**
   - 9 474 строки.
   - Смешаны десятки admin/workflow экранов.

6. **Legacy API / UI drift.**
   - UI вызывает `/api/*`, backend по умолчанию считает `/api/*` legacy.

7. **Миграционный стандарт не описан как контракт.**
   - Базовая схема + миграции 002–025 логичны, но следующий разработчик может решить, что миграция 001 потеряна.

8. **Недостаточный machine-readable risk model.**
   - Риск-классы есть в наших audit-документах, но не в коде/tool registry.

9. **Тесты не фиксируют все опасные действия.**
   - Есть хорошие contract/regression tests, но нет полного набора safety tests для external actions.

### Можно потом

10. Переименование `employee-context` в `albery`/`albery-context` в `.mcp.json` и документации.
11. Упорядочивание `agent.md` и вынос playbooks в отдельные документы.
12. Обновление Windows-only инструкций в README/database docs.
13. Разбиение frontend на модули по доменам.
14. Разбиение MCP на несколько файлов по доменам.

---

## 9. Рекомендуемый план стабилизации без поломки

### Фаза 1 — safety patches, маленькие и проверяемые

1. Добавить `mcp/__init__.py`.
   - Цель: убрать конфликт с внешним Python-пакетом `mcp`.
   - Проверка: `import mcp.context_server` резолвится в проектный файл.

2. Добавить server-side confirm для `create_bitrix_task` или формально зафиксировать исключение.
   - Рекомендуемый вариант: сделать `confirm=true` обязательным.
   - Проверка: тест на отказ без confirm и успешный mocked-call с confirm.

3. Обновить `mcp/README.md`.
   - Убрать `read-only`.
   - Описать классы: read-only, external-read, local-export, workflow-write, DB-write, external-action.

4. Обновить корневой `README.md`.
   - Сделать краткий обзор текущего Albery.
   - Старый weekly export перенести в historical/legacy notes.

### Фаза 2 — стандартизация risk model

5. В MCP tool registry добавить machine-readable поля, например:
   - `risk_class`;
   - `side_effects`;
   - `requires_confirm`;
   - `writes_db`;
   - `external_action`.

6. Добавить тесты, что:
   - все external-action tools требуют confirm;
   - все tools имеют risk metadata;
   - FAQ endpoint не содержит write/action tools;
   - legacy API выключен по умолчанию.

7. В backend сделать общий helper для confirm-gate, чтобы не копировать проверки в каждом route/tool.

### Фаза 3 — документация как источник правды

8. Расширить `docs/about-project.md` до полного overview:
   - назначение;
   - компоненты;
   - контуры данных;
   - кто что читает/пишет;
   - production boundaries;
   - как Hermes взаимодействует с MCP;
   - где живут AI-инструкции;
   - какие действия требуют согласования владельца.

9. Добавить `docs/architecture-standard.md`:
   - MCP-first control plane;
   - legacy API policy;
   - UI policy;
   - confirmation policy;
   - migration policy;
   - testing policy.

10. Разнести `agent.md`:
   - оставить короткий operational index;
   - playbooks хранить в `docs/playbooks/`;
   - deployment/server details отделить от product architecture.

### Фаза 4 — постепенный рефакторинг без big bang

11. Выделять из `app.py` только по доменам и только после тестов:
   - `integrations/bitrix.py`;
   - `integrations/zoom.py`;
   - `integrations/google_drive.py`;
   - `reports/owner.py`;
   - `reports/chat.py`;
   - `workflows/zoom_dispatch.py`;
   - `auth.py`;
   - `routes/*.py`.

12. Выделять frontend по доменам:
   - API client;
   - layout;
   - reports;
   - zoom;
   - AI instructions;
   - settings/admin.

13. Разделить MCP:
   - `tools/read.py`;
   - `tools/reports.py`;
   - `tools/bitrix_actions.py`;
   - `tools/zoom.py`;
   - `tools/instructions.py`;
   - общий registry.

---

## 10. Первый набор правок, который можно делать после аудита

Минимальный безопасный PR/патч:

1. `mcp/__init__.py` — добавить пустой файл.
2. `mcp/context_server.py` — `create_bitrix_task` требует `confirm=true`.
3. Тесты:
   - добавить тест на `create_bitrix_task` без confirm;
   - добавить тест, что external-action tools имеют confirm policy.
4. `mcp/README.md` — обновить описание MCP как не read-only.
5. `README.md` — заменить старое описание на короткое текущее overview.

Почему это первый набор:

- маленький;
- не требует прод-БД;
- не требует рефакторинга `app.py`;
- закрывает самый явный риск внешнего действия;
- улучшает переносимость тестов;
- убирает опасную ложь в документации.

---

## 11. Что нельзя делать пока

Не рекомендовано на этом этапе:

- переписывать `app.py` целиком;
- удалять legacy API без проверки реального production/UI режима;
- запускать миграции по боевой БД без отдельного preflight и backup-check;
- менять AI-инструкции в БД без версии/журнала/плана отката;
- менять Hermes cron/промпты без отдельного operational playbook;
- считать frontend “мертвым” только потому, что `/api/*` legacy — нужно проверить реальный прод-режим.

---

## 12. Итог этапа C

Этап C завершён как read-only аудит качества.

Текущий статус проекта: система рабочая по признакам структуры, тестов, CI, миграций и документации последних изменений, но архитектурно хрупкая из-за быстрого роста вокруг одного backend-файла, одного frontend-файла и одного MCP-файла.

Главная стратегия: не делать большой рефакторинг. Сначала зафиксировать safety-гейты, обновить документацию и добавить machine-readable risk model. После этого постепенно выделять домены из `app.py`, `App.tsx` и `mcp/context_server.py` под защитой тестов.
