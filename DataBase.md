# Схема базы данных

Актуальная модель PostgreSQL для проекта `employee_analytics`.

Источник истины по DDL: `database/postgres_schema_v1.sql`  
Миграции: `database/migrations/*.sql`

## Расширения

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "btree_gist";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
```

## Основные сущности

### Команда
- `users` — пользователи Bitrix и орг-атрибуты.
- `departments` — отделы.
- `user_departments` — связи пользователь-отдел.
- `user_hierarchy` — иерархия подчинения по периодам.

### Цели
- `user_goals` — цели (company/department/manager/employee/project).
- `goal_progress_events` — события прогресса по целям из чат-отчетов.

### Задачи Bitrix
- `bitrix_tasks` — основной реестр задач.
- `bitrix_task_members` — участники задач.
- `bitrix_task_sync_runs` — логи синхронизаций.
- `bitrix_task_snapshots` — исторические снимки задач.

### Чаты
- `chats` — реестр чатов.
- `chat_members` — участники чатов.
- `chat_messages` — сообщения (partitioned by `message_day`).
- `chat_message_files` — файлы сообщений.
- `chat_file_ocr` — OCR-результаты.
- `chat_exclusions` — журнал исключений/возвратов чатов.

### AI и промпты
- `ai_prompt_categories` — категории промптов.
- `ai_prompts` — версии промптов.
- `ai_requests` — все AI-вызовы.
- `ai_request_artifacts` — связи AI-запроса с сущностями.

### Отчеты по чатам
- `chat_daily_reports` — ежедневные отчеты по чату.
- `chat_report_items` — структурированные пункты daily отчета.
- `chat_weekly_reports` — недельные отчеты по чату.
- `chat_overall_daily_reports` — ежедневные отчеты по всем чатам.
- `chat_overall_weekly_reports` — недельные отчеты по всем чатам.

### Отчеты для собственника и рассылка рекомендаций
- `owner_daily_reports` — ежедневный owner-отчет.
- `owner_weekly_reports` — недельный owner-отчет.
- `owner_manager_recommendations` — адресные рекомендации конкретным руководителям/по сотрудникам.
- `owner_recommendation_dispatches` — попытки доставки рекомендаций в Bitrix (IM/комментарий/создание задачи).

### Отчеты по сотрудникам
- `user_daily_reports` — ежедневные персональные отчеты.
- `user_period_reports` — week/month/quarter/year отчеты.
- `user_dynamics` — рассчитанная динамика метрик.
- `user_memory` — накопленная управленческая память по сотруднику.

### Аудит
- `audit_log` — журнал изменений и действий.

---

## Ключевые поля новых таблиц (owner + dispatch)

### `owner_daily_reports`
- PK: `id`
- уникальность версии: `(report_date, version)`
- текущая версия: `is_current` + индекс `idx_owdr_current`
- полезная нагрузка: `summary`, `dynamics_summary`, `risks_summary`, `recommendations`, `report_text`, `raw_json`

### `owner_weekly_reports`
- PK: `id`
- период: `period_start`, `period_end`
- уникальность версии: `(period_start, period_end, version)`
- текущая версия: `is_current` + индекс `idx_owwr_current`
- полезная нагрузка: `summary`, `dynamics_summary`, `risks_summary`, `recommendations`, `report_text`, `raw_json`

### `owner_manager_recommendations`
- PK: `id`
- источник: `source_scope` (`owner_daily|owner_weekly|owner_monthly`)
- FK на отчет-источник: `owner_daily_report_id` или `owner_weekly_report_id`
- адресация:
  - `manager_user_id`, `manager_bitrix_user_id`
  - опционально `employee_user_id`, `employee_bitrix_user_id`
- содержание:
  - `recommendation_type` (`action|followup|risk|goal|task`)
  - `priority` (`low|medium|high|critical`)
  - `subject`, `recommendation_text`, `due_date`
- трассировка:
  - `bitrix_task_id`/`bitrix_task_external_id`
  - `source_chat_id`, `source_goal_id`, `source_item_id`
  - `source_payload`
- статус жизненного цикла: `status` (`new|queued|sent|acked|done|cancelled|error`)

### `owner_recommendation_dispatches`
- PK: `id`
- FK: `recommendation_id -> owner_manager_recommendations(id)`
- канал: `channel` (`bitrix_task_comment|bitrix_im|bitrix_task_create|manual`)
- статус доставки: `status` (`queued|sent|delivered|error|cancelled`)
- поля Bitrix: `bitrix_entity_type`, `bitrix_entity_id`, `bitrix_message_id`
- payload: `payload`, `response_payload`, `error_text`, `sent_at`

---

## Дополнения в `ai_requests.request_type`

Поддерживаются, в том числе:
- `chat_overall_daily_report`
- `owner_daily_report`
- `owner_weekly_report`

(остальные типы сохранены из прежней схемы).

---

## Партиционирование сообщений

`chat_messages` разделена по месяцам (`message_day`).
Есть триггер и функция автосоздания партиции:
- `ensure_chat_messages_partition(target_day)`
- `trg_chat_messages_ensure_partition`

---

## Триггеры и актуальность версий

- Универсальный `trg_set_updated_at` для таблиц с `updated_at`.
- Переключение текущей версии:
  - `trg_udr_switch_current` (для `user_daily_reports`)
  - `trg_upr_switch_current` (для `user_period_reports`)
  - Для chat/owner отчетов текущая версия также контролируется на уровне приложения и уникальных индексов `..._current`.

---

## Миграции

Базовая схема: `database/postgres_schema_v1.sql`  
Инкрементальные миграции:
- `002_chat_message_auto_partitions.sql`
- `003_remove_unified_work_registry.sql`
- `004_chat_report_item_types.sql`
- `005_previous_day_task_report_items.sql`
- `006_goal_progress_events.sql`
- `007_chat_overall_daily_reports.sql`
- `008_chat_overall_weekly_reports.sql`
- `009_chat_weekly_reports.sql`
- `010_owner_reports_and_targeted_recommendations.sql`

