# Этап B — MCP как граница доступа ИИ и ручных действий

Дата: 2026-06-02
Режим: read-only аудит локальной копии репозитория. Боевые сервисы, прод-БД и секреты не трогались. Код проекта не менялся, создан только этот аудиторский артефакт.

## Где остановились

Этап B продолжен с точки: «MCP содержит 51 обработчик. Важно: часть инструментов выглядит читающей, но внутри вызывает рабочие функции, поэтому отдельно проверяются спорные инструменты — создание/удаление задач, OCR, Zoom-выгрузки и отправки — чтобы не перепутать чтение с действием».

## Проверенные источники

- `mcp/context_server.py` — 51 MCP-инструмент, 5053 строки.
- `app.py` — backend/workflow-функции, которые вызывает MCP, 21946 строк.
- `Интерфейс/src/App.tsx` — ручные UI-кнопки и HTTP-вызовы, 9473 строки.
- `database/postgres_schema_v1.sql` — схема PostgreSQL.
- `audit-report.md`, `docs/about-project.md`, `mcp/README.md` — существующая документация, сверена с текущим кодом.

## Карта MCP-инструментов по типам риска

### 1. Безопасное чтение из БД / контекста

Эти инструменты сами не пишут в БД и не отправляют во внешние системы:

- `health`
- `get_runtime_status`
- `list_available_sources`
- `get_context_guide`
- `start_here_always_read_ai_instructions`
- `get_ai_instructions`
- `get_report_contract`
- `get_company_profile`
- `list_company_files`
- `get_company_file`
- `search_company_knowledge`
- `list_periods`
- `get_period_index`
- `get_report_readiness`
- `get_org_structure`
- `search_tasks`
- `get_task_comments`
- `list_chats`
- `search_messages`
- `get_chat_transcript`
- `get_chat_ocr_status`
- `list_zoom_calls`
- `get_zoom_call_transcript`
- `search_zoom_transcripts`
- `get_owner_reports`
- `list_recommendations`
- `get_recommendation_feedback_context`
- `get_previous_owner_daily_context`
- `preview_zoom_operational_tasks`
- `list_leader_evaluations`
- `list_pending_owner_recommendations`
- `get_compact_export`

Примечание: `get_context_guide` и `start_here_always_read_ai_instructions` содержат текстовые правила про подтверждения и действия, поэтому автоматический поиск по словам даёт ложные срабатывания. По фактическому коду они читающие.

### 2. Внешнее чтение с отдельным риском

- `fetch_url` — делает внешний HTTP GET и умеет переписывать ссылки Google Docs/Sheets в экспортный формат. БД не пишет и ничего не отправляет от имени компании, но это всё равно внешняя сеть и риск SSRF/утечки URL-контекста. В safety-пакете добавлен отдельный external-read contract: ссылка должна быть помечена как пользовательская (`user_provided=true`) или явно разрешённая (`confirm_external=true`), а localhost/private/link-local/reserved hosts блокируются до сетевого запроса и после редиректа.

### 3. Локальные экспорты

- `export_zoom_call_markdown`
- `export_zoom_transcripts_markdown`

Они вызывают backend workflow и сохраняют markdown/export-файл через app-функции, возвращая ссылку/путь. Это не отправка в Bitrix, но это уже создание артефакта на сервере. В стандарте лучше отделить от чистого чтения: «локальный экспорт/файл».

### 4. OCR workflow

- `process_chat_ocr`

В MCP выглядит как обработка, не как запись, но фактически вызывает backend workflow `process_chat_image_ocr_for_period`. Это действие, которое читает вложения/файлы, прогоняет OCR и обновляет состояние OCR в БД. В safety-пакете добавлен confirm-gate: сначала надо показать пользователю диапазон дат/чат и режим `force`, затем вызывать с `confirm=true`. Риск ниже, чем у отправки в Bitrix, но это не read-only.

### 5. Записи в БД без внешней отправки

- `save_zoom_call_report` — обновляет Zoom-отчёт/аналитическую заметку.
- `delete_zoom_call_report` — не физически удаляет созвон, но сбрасывает/обновляет сохранённый отчёт.
- `save_recommendation_event` — пишет событие рекомендации и меняет статус.
- `save_owner_daily_report` — создаёт новую версию ежедневного owner-отчёта и снимает `is_current` со старой.
- `save_owner_weekly_report` — создаёт новую версию недельного owner-отчёта и снимает `is_current` со старой.
- `cancel_owner_recommendation` — меняет статус рекомендации на cancelled и пишет событие.
- `upsert_ai_instruction` — создаёт/обновляет папки и живые AI-инструкции; в safety-пакете добавлен server-side `confirm=true`, preview wording в tool contract и `expected_current_content` guard, чтобы не перезаписывать инструкцию, если она изменилась после preview.

Общий вывод: эти инструменты не отправляют наружу, но меняют источник правды. Для них нужен стандарт “какая запись считается черновиком, какая текущей версией, можно ли перезаписать без preview”. Для `upsert_ai_instruction` этот стандарт уже закреплён на MCP-границе: preview → явное подтверждение → `confirm=true`, а при перезаписи можно передать `expected_current_content` для защиты от stale-preview.

### 6. Внешние действия / Bitrix / отправки

С confirm-gate уже есть:

- `delete_bitrix_task` — требует `confirm=true`, перед удалением сверяет задачу из локального индекса и может проверять `expected_title`.
- `dispatch_zoom_operational_tasks` — требует `confirm=true`; создаёт агрегированные задачи «Итоги созвона» в Bitrix.
- `dispatch_leader_evaluations_digest` — требует `confirm=true`; создаёт задачу Евгению по своду руководителей.
- `send_owner_recommendations_to_bitrix` — требует `confirm=true`; создаёт/отправляет рекомендации в Bitrix по получателям.
- `send_owner_weekly_report_pdf` — требует `confirm=true`; отправляет PDF недельного отчёта в Bitrix.
- `send_bitrix_message` — требует `confirm=true`; отправляет личное сообщение в Bitrix.

Без confirm-gate было обнаружено:

- `create_bitrix_task` — на момент аудита сразу вызывал Bitrix `tasks.task.add`. В safety-пакете закрыто: теперь обязательны preview и `confirm=true`.

Эта главная несостыковка MCP-границы закрыта в safety-пакете.

## Связанные backend workflow из `app.py`

MCP вызывает backend-функции, которые являются фактическими исполнителями:

- Zoom:
  - `preview_zoom_operational_tasks` — собирает preview.
  - `dispatch_zoom_operational_tasks` → `dispatch_prepared_zoom_operational_tasks` — создаёт задачи в Bitrix.
  - `export_zoom_call_markdown_link`, `export_zoom_calls_markdown_link` — создают локальные export-артефакты.
  - `dispatch_leader_evaluations_digest` — создаёт задачу по своду руководителей.
- OCR:
  - `process_chat_image_ocr_for_period` — обрабатывает OCR по чатам за период.
- Owner-рекомендации/отчёты:
  - `send_owner_report_recommendations_to_bitrix` — отправляет рекомендации в Bitrix.
  - `send_owner_report_pdf_to_bitrix` — отправляет PDF.
  - `send_bitrix_personal_message` — отправляет личное сообщение.

## UI как параллельная ручная поверхность

Во фронте найдено около 39 `fetch`-вызовов. Есть ручные POST/PUT/DELETE-действия для:

- синхронизации Bitrix/команды/чатов;
- OCR изображений/файлов чатов;
- генерации дневных/недельных сводок;
- создания/переименования/удаления папок «О компании»;
- создания/редактирования/удаления AI-инструкций;
- сохранения/удаления версий промптов;
- исключения чатов;
- операций с отчётами.

Вывод: UI и MCP — две поверхности управления одной системой. Стандартизация должна описывать не только MCP-инструменты, но и соответствующие UI-кнопки: какие действия read-only, какие пишут в БД, какие отправляют наружу, где нужен preview и подтверждение.

## Несостыковки и риски этапа B

1. `create_bitrix_task` не имеет confirm-gate, хотя это внешнее действие в Bitrix. На фоне остальных отправок это выбивается из стандарта.
2. `process_chat_ocr` выглядит как служебная обработка, но фактически меняет состояние OCR/БД. В safety-пакете добавлен `confirm=true` gate; следующий шаг — описать такой же принцип для UI/автоматических OCR-триггеров, если они появятся.
3. `upsert_ai_instruction` меняет живые инструкции агента без отдельного механизма preview/version/approval в MCP. Это риск не данных, а поведения системы.
4. `fetch_url` раньше разрешал внешний HTTP GET по произвольному URL; теперь добавлены provenance-флаг/явное разрешение и блокировка private/internal hosts, включая редиректы.
5. Локальные Zoom-экспорты не являются внешней отправкой, но создают файлы/ссылки. Их стоит выделить в отдельный класс действий.
6. В документации `mcp/README.md` MCP назван read-only, но текущий MCP уже содержит записи, отправки, создание/удаление задач и обновление инструкций. Это документационный дрейф.
7. Старый `README.md` описывает проект как Bitrix24 Weekly Export mini app, что не соответствует текущему Albery как управленческой AI/MCP-платформе. Это отдельный документационный дрейф.

## Предлагаемый единый стандарт действий

Для каждого инструмента/API/UI-кнопки в проекте ввести класс:

1. `read_only` — только читает БД/контекст.
2. `external_read` — читает внешнюю сеть/документ, не пишет.
3. `local_export` — создаёт локальный файл/артефакт.
4. `db_write_draft` — пишет черновик/событие/версию в БД.
5. `db_write_current` — меняет текущую версию/статус/живую инструкцию.
6. `external_action` — отправляет сообщение, создаёт/удаляет задачу или файл во внешней системе.

Правило подтверждения:

- `read_only` — без подтверждения.
- `external_read` — без подтверждения только для пользовательских ссылок/allowlist-доменов с явным provenance-флагом; иначе с явным разрешением. Private/internal hosts блокировать независимо от подтверждения.
- `local_export` — можно без подтверждения, но надо явно сообщать, что создан файл.
- `db_write_draft` — допустимо без confirm, если запись не становится текущей и не влияет на поведение.
- `db_write_current` — нужен preview или чёткое описание изменения.
- `external_action` — всегда preview + явное подтверждение + `confirm=true`.

## Следующий шаг этапа B

1. Дособрать полную матрицу `инструмент/API/UI-действие → класс риска → таблицы/внешние системы → confirmation rule`.
2. Отдельно сверить backend API-маршруты, потому что сейчас AST видит только 4 активных `app.route`, а UI всё ещё содержит много `/api/*` вызовов. Нужно понять: они legacy/отключены, обслуживаются иначе или часть UI устарела.
3. После матрицы перейти к этапу C: качество/риски и план стандартизации без ломки системы.
