# Bitrix ИИ-агент: доступы, UX, интеграции

Документ описывает систему ИИ-агента Albery в Bitrix24 (бот «Агент Албери») и связанную
веб-панель — всё, что собрано в работе 17.06.2026. Бот живёт в `app.py`
(`_bitrix_imbot_app_event` и `_b24_*` функции), MCP-инструменты — в `mcp/context_server.py`,
веб-интерфейс — в `Интерфейс/src/App.tsx`.

Портал Bitrix: **`b24-0xrp3s.bitrix24.ru`** (рабочий, подписки активны). Бот — local app
(`imbot.*`), отвечает на события `ONIMBOTMESSAGEADD` / `ONIMCOMMANDADD` / `ONIMBOTJOINCHAT`,
обращается к локальному Мозгу через `hermes -z ... -t <toolset> --yolo`.

---

## 1. Уровни доступа (admin / ops / faq / none)

Доступ к агенту разделён на уровни по принципу **capability-based** (безопасность через
отсутствие инструмента у коннектора, а не через запрет в промпте — устойчиво к prompt-injection).

| Уровень (UI-название) | MCP-коннектор | Что доступно |
|---|---|---|
| **admin** (Полный доступ) | `/mcp` (`MCP_SHARED_SECRET`) | всё, включая правку инструкций/настроек и удаление |
| **ops** (Доступ ко всем функциям) | `/mcp-ops` (`MCP_OPS_SHARED_SECRET`) | всё операционное, кроме админ-инструментов |
| **faq** (Доступ к базе знаний) | `/mcp-faq` (`MCP_FAQ_SHARED_SECRET`) | только чтение (знания, Zoom, оргструктура) |
| **none** (Отсутствие доступа) | — | бот НЕ отвечает (системное уведомление) |

- `OWNER_ONLY_TOOL_NAMES` (только admin, в `context_server.py`): `upsert_ai_instruction`,
  `update_ai_capabilities`, `delete_bitrix_task`, `delete_zoom_call_report`. Плюс
  defense-in-depth guard в `handle_request` (эти инструменты отказываются на любом scoped-коннекторе).
- `OPS_TOOL_NAMES = set(TOOLS) - OWNER_ONLY_TOOL_NAMES`. HTTP-эндпоинты `/mcp-ops` + `/sse-ops`
  (зеркало `/mcp-faq`).
- Hermes config (`/root/.hermes/config.yaml`): `mcp_servers.albery-ops` → `/mcp-ops/<secret>`.
- Маршрутизация в боте: `_b24_tier_for(from_user_id)` (id из доверенного Bitrix-события, в чате
  не подделать) → `hermes_brain_answer` выбирает toolset `albery` / `albery-ops` / `albery-faq`.
- **Уровни хранятся в БД** (таблица `agent_access`), читаются с кэшем 20с. Дефолт (нет строки) =
  `faq`. `none` — явный stored deny. Никто не захардкожен — даже владелец редактируется из UI.

## 2. Веб-панель «Настройки Агента»

Вкладка в SPA (раздел **Настройки**, рядом с «Инструкции для ИИ»), `App.tsx` →
`renderAgentAccessSettings`. За существующим админ-логином сайта (`ADMIN_PASSWORD_HASH` /
`require_admin_auth`).

- Эндпоинты (под `/api/agent-access`, авторизация+origin — централизованно в `require_admin_auth`;
  `/api/*` включён через `ALLOW_LEGACY_HTTP_API=1`):
  - `GET /api/agent-access` — список назначений;
  - `GET /api/agent-access/bitrix-users` — все активные пользователи **живого портала**
    (`B24_TESTBOT_WEBHOOK_BASE` → b24-0xrp3s, `user.get`); id 1:1 совпадают с тем, что видит бот;
  - `POST /api/agent-access` — выдать уровень; `DELETE /api/agent-access/<uid>` — снять (→ faq).
- Имена: у большинства аккаунтов портала пустые NAME/LAST_NAME → ФИО резолвится по **email** из
  синканного оргсправочника (таблица `users.full_name/email`), фоллбэк — сам email
  (`_b24_portal_user_directory`, кэш 10 мин).
- UI: единая таблица ВСЕХ сотрудников + поиск, у каждого выпадающий статус (4 уровня).

## 3. UX бота

- **Клавиатура** (`_b24_keyboard`) — 3 кнопки: «🆕 Новая сессия», «⚠️ Сообщить об ошибке»,
  «❓ Как пользоваться». Только под последним сообщением: `_b24_app_reply` помнит per-dialog
  (JSON-стейт `/var/www/albery/.b24_testbot_state.json`, ключ `last_kb`) держателя клавиатуры и
  снимает её с предыдущего через `imbot.message.update` (`KEYBOARD:"N"`).
- **Команды** (регистрируются лениво, флаг `cmds_registered_v3`): `new`, `report_error`, `help`,
  `onb_next`.
- **Приветствие** при первом входе (`ONIMBOTJOINCHAT`) + кнопка «🚀 Пройти обучение».
- **Обучение** — 3 шага (что умею / как формулировать запрос / примеры), листается «Далее ▶️»
  (`onb_next`, текущий шаг в стейте `onboarding[dialog]`); последний шаг возвращает обычную
  клавиатуру. `_b24_onboarding_text` / `_b24_send_onboarding`.
- **Нет доступа** (`none`): на сообщение и на команды бот отвечает системным
  «😔 К сожалению, у вас нет доступа к агенту. Пожалуйста, обратитесь к вашему руководителю или к
  Александру Никитенко 🙌» (без модели/дисклеймера/кнопок).
- **Эскалация доступа**: при нехватке прав агент по инструкции даёт чёткий отказ + предложение
  передать запрос Александру; при согласии ставит скрытый маркер `[[ESCALATE: суть]]`. Бот
  (`_b24_extract_escalation` в `_b24_app_process`) вырезает маркер и шлёт выжимку владельцу в
  Telegram (`_b24_forward_access_request`, имя из `_b24_requester_name`), лог в `access_requests`.
- **Keepalive «печатает…»**: каждые 20с в `_b24_app_process`, чтобы долгие ходы (агентный цикл
  6–60с) не выглядели зависшими.
- **Сессия**: idle-сброс **30 мин** (`B24_TESTBOT_IDLE_RESET_SECONDS`, дефолт 1800), реальный
  сброс (поднимается `history_floor_id`, как кнопка «Новая сессия»); turn-cap rotation с carried
  summary — `_b24_session_prepare`.

## 4. Отчёты об ошибках и аналитика

- Кнопка «⚠️ Сообщить об ошибке» → бот спрашивает причину → следующее сообщение БЕЗ вызова модели
  уходит в **Telegram-группу «Albery_Уведомления» (`-5283789593`)** в формате «{Имя} отправил
  отчёт об ошибке, текст: …» + лог в `bitrix_error_reports`. Доставка через Telegram Bot API
  (`@albery_ai_bot`; токен — `ALBERY_TG_BOT_TOKEN` или fallback `TELEGRAM_BOT_TOKEN` из
  `/root/.hermes/.env`, сервис под root).
- Вьюхи (миграция 033): **`error_report_context`** (жалоба + последние ≤8 реплик диалога до неё),
  **`dialog_timeline`** (хронолента диалога с жалобами инлайн, `kind='turn'|'complaint'`).
- Дайджест: **`scripts/error_report_digest.py`** — новые жалобы + контекст + краткий разбор от
  Мозга; systemd-таймер **`albery-error-digest.timer`** (Пн 10:00 МСК), дедуп по watermark,
  чат — `ALBERY_ERROR_DIGEST_TG_CHAT` (дефолт — ЛС владельца `1451982360`).

## 5. Тон и формат ответов

Заданы в head-промпте `hermes_brain_answer` (`fmt` + per-tier head) и продублированы в
«Инструкции для ИИ»:
- кратко, по делу, без вводных/резюме-воды;
- но красиво: [b]жирным[/b] главное, короткие абзацы, списки, уместные эмодзи (1–3);
- отказы — мягко; ответственный за доступ — **только Александр Никитенко**;
- Markdown не использовать (Bitrix не отображает), жирный только `[b]...[/b]`.

Инструкции для ИИ (через `upsert_ai_instruction`): «Базовое поведение / Вопросы о возможностях и
доступе», «Формат ответа / Живой тон + максимальная краткость», «Работа в системе / Известные
расхождения (роли и оргструктура)».

## 6. Google-таблицы (создание/редактирование)

- ops/admin могут создавать и редактировать Google Sheets; faq — нет.
- `write_company_sheet` — запись в существующую таблицу (через Apps Script web-app,
  `GOOGLE_APPS_SCRIPT_SYNC_URL`).
- **`create_google_sheet`** (новый, ops/admin) — создаёт новую таблицу ПРЯМЫМ Google API на
  albery-боксе: создать → доступ «по ссылке = редактор» (anyone/writer) → записать rows → вернуть
  url; `confirm=true` обязателен. Код: `_google_user_credentials` + `create_google_sheet` (app.py),
  инструмент `tool_create_google_sheet` (context_server.py).
- Google-аккаунт агента (albery) = **`a9ent.ai@gmail.com`**. OAuth-токен (scopes
  drive/spreadsheets/documents/script.projects/script.deployments) на albery-боксе в
  `/root/.hermes/secure/google_oauth_token.json` + `/root/.hermes/google_token.json` (600). В venv
  albery доставлены `google-api-python-client` и т.д. Токен получен ручным OAuth-флоу (client_id /
  secret — в локальном репо-`.env`: `Clien_ID_Albery` / `Clien_Secret_Albery`; консент Google
  требует резидентного IP — на ПК владельца).

---

## Сводка артефактов

**Миграции** (`database/migrations/`, применяются через `scripts/ensure_postgres.py`):
- `032_bitrix_error_reports.sql` — отчёты об ошибках.
- `033_error_report_context_views.sql` — вьюхи `error_report_context`, `dialog_timeline` (ALWAYS_APPLY).
- `034_agent_access.sql` — таблица уровней доступа (засеяна 16=admin/14=ops; 14 = Евгений Палей).
- `035_agent_access_none_tier.sql` — добавляет `none` в CHECK (ALWAYS_APPLY).
- `036_access_requests.sql` — запросы на расширение доступа.

**Таблицы:** `agent_access`, `bitrix_error_reports`, `access_requests`, `bitrix_bot_interactions`,
`bitrix_bot_sessions`.

**Env (`/var/www/albery/.env`):** `MCP_SHARED_SECRET`, `MCP_OPS_SHARED_SECRET`,
`MCP_FAQ_SHARED_SECRET`, `B24_TESTBOT_OWNER_USER_IDS` (дефолт 16=Александр),
`B24_TESTBOT_FULL_USER_IDS`, `B24_TESTBOT_IDLE_RESET_SECONDS` (дефолт 1800),
`B24_TESTBOT_WEBHOOK_BASE` (b24-0xrp3s, для списка юзеров), `GOOGLE_APPS_SCRIPT_SYNC_URL`,
`ALBERY_ALLOW_SHEET_WRITE=1`, `ALLOW_LEGACY_HTTP_API=1`, опц. `ALBERY_TG_BOT_TOKEN`,
`ALBERY_ERROR_REPORT_TG_CHAT`, `ALBERY_ERROR_DIGEST_TG_CHAT`, `ALBERY_ACCESS_REQUEST_TG_CHAT`.

**Cron/timers:** `albery-error-digest.timer` (systemd, Пн 10:00 МСК).

**Telegram:** бот `@albery_ai_bot`; группы `-5283789593` (Albery_Уведомления),
`-5244577964` (Albery_Собственник); владелец (Александр) ЛС `1451982360`.

**Деплой:** бэкенд (app.py / context_server.py / миграции) — `git pull` в `/var/www/albery` +
рестарт `albery`. Фронтенд — собирать ОФФ-бокс (`cd Интерфейс && npm ci && npm run build`) и
заливать `dist/` на сервер (gitignored). Hermes config — рестарт `hermes-gateway`.
