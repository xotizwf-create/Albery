# Albery Server Context

## Current Operating Rules / Актуальный Контекст

This file is the first place to read in every new chat. It contains the current server context, deployment commands, webhook endpoints, cron schedule, and operational rules.

Server access:

- Server: `root@186.246.7.32`
- Server project: `/var/www/albery`
- Local project: `G:\OneDrive\Рабочий стол\Мои проекты\Евгений. Разработка`
- The root password is stored only in local `.env` as `root_password`.
- Do not print, quote, commit, or pass `root_password` through command-line arguments.
- For automated server work from Codex, prefer Python/Paramiko: read `root_password` from local `.env` into memory and connect to `186.246.7.32` as `root`.
- If already inside the server shell, run commands directly without `ssh root@...`.
- Never commit `.env`; secrets stay only in local/server env files.

Recent production changes:

- Bitrix task comments are now exposed through the main MCP connector:
  - new tool `get_task_comments(bitrix_task_id, include_service?, order?, limit?, offset?)` reads `bitrix_tasks.raw_json -> 'comments' -> 'items'`, strips Bitrix BB-codes, resolves author names from `users`, and hides auto-generated notifications (overdue reminders, status cards, completion notices) unless `include_service=true`;
  - `search_tasks` rows now also return `comments_total_count` and `comments_human_count` so the agent knows which tasks have real discussion;
  - no DB migration; lives in `mcp/context_server.py` (MCP server version `0.3.0`); not exposed on the FAQ endpoint.
- Bitrix tasks now support near-realtime sync through outgoing Bitrix webhook:
  - endpoint: `https://mcp.m4s.ru/bitrix/events/tasks/<BITRIX_EVENT_SECRET>`
  - events: `OnTaskAdd`, `OnTaskUpdate`, `OnTaskDelete`
  - queue table: `bitrix_task_events`
  - migration: `database/migrations/018_bitrix_task_events.sql`
  - unsupported Bitrix task comment events are accepted with `200 OK` and ignored.
- Zoom recordings now use incremental sync:
  - existing Zoom calls are skipped if the set of transcript files has not changed;
  - old transcript segments are not deleted/recreated for unchanged recordings;
  - manual/API/cron sync downloads only new or changed transcript files.
- Zoom webhook support was added:
  - endpoint: `https://mcp.m4s.ru/zoom/events/<ZOOM_EVENT_SECRET>`
  - events: `recording.transcript_completed`, `recording.completed`
  - validation event: `endpoint.url_validation`
  - queue table: `zoom_recording_events`
  - migration: `database/migrations/019_zoom_recording_events.sql`
  - `ZOOM_WEBHOOK_SECRET_TOKEN` must be copied from Zoom Marketplace Event Subscriptions Secret Token.
- Big external sync cron is daily at `18:00 Europe/Moscow`, not hourly:
  - file: `/etc/cron.d/albery-daily-sync`
  - logs: `/var/log/albery/daily-sync.log`, `/var/log/albery/daily-sync.cron.log`
- Latest relevant commits:
  - `90833c5 Expose Bitrix task comments via MCP`
  - `096c85f Add Bitrix task event sync`
  - `1f8c45d Add incremental Zoom recording sync`

Important env keys:

```env
BITRIX_EVENT_SECRET=...
BITRIX_TASK_EVENT_PROCESS_INLINE=1
ZOOM_EVENT_SECRET=...
ZOOM_WEBHOOK_SECRET_TOKEN=...
ZOOM_EVENT_PROCESS_INLINE=1
```

Operational checks:

```bash
cd /var/www/albery
git status --short
git rev-parse --short HEAD
systemctl status albery --no-pager
tail -n 120 /var/log/albery/daily-sync.log
tail -n 120 /var/log/albery/daily-sync.cron.log
```

Apply migrations/restart after deploy:

```bash
cd /var/www/albery && .venv/bin/python scripts/ensure_postgres.py && systemctl restart albery
```

Check webhook endpoints without printing secrets:

```bash
cd /var/www/albery
bitrix_secret=$(awk -F= '$1=="BITRIX_EVENT_SECRET"{print $2; exit}' .env)
zoom_secret=$(awk -F= '$1=="ZOOM_EVENT_SECRET"{print $2; exit}' .env)
curl -sS "https://mcp.m4s.ru/bitrix/events/tasks/$bitrix_secret"
curl -sS "https://mcp.m4s.ru/zoom/events/$zoom_secret"
```

Current GitHub branch: `main`. Push code changes to GitHub before or after server deployment so that future `./scripts/update_server.sh` does not overwrite manual server edits.

Этот файл фиксирует рабочий контекст проекта, чтобы в новом чате сразу было понятно, где что лежит и какими командами обслуживать сервер.

## Git Branch Workflow / Правила Работы С Ветками

`main` is the stable working branch. Do not make risky or experimental changes directly in `main`.

Default workflow:

```powershell
git checkout main
git pull origin main
git checkout -b feature/my-change
```

Work in a separate branch for every task:

- `feature/...` for new features;
- `bugfix/...` for fixes;
- `codex/...` for Cloud Codex work;
- one branch should contain one logical task.

Before committing:

```powershell
git status
git diff
```

Commit and push the branch:

```powershell
git add .
git commit -m "Describe the change"
git push -u origin feature/my-change
```

Cloud Codex rule:

- Cloud Codex should create a separate branch from the latest `main`;
- Cloud Codex must not push directly to `main` unless explicitly asked;
- after finishing, Cloud Codex should push the branch and show a diff summary;
- merge into `main` only after review/confirmation.

Recommended Cloud Codex prompt:

```text
Work in a new branch feature/my-change from the latest main.
Do not push directly to main.
After changes, show a diff summary and wait for confirmation before merge.
```

To inspect a remote branch created by Cloud Codex:

```powershell
git fetch origin
git branch --track codex/some-branch origin/codex/some-branch
git diff --stat main..origin/codex/some-branch
git diff main..origin/codex/some-branch
```

To merge a checked branch into `main` locally:

```powershell
git checkout main
git pull origin main
git merge feature/my-change
git push origin main
```

If Git reports conflicts, resolve the marked files manually, then:

```powershell
git add <resolved-files>
git commit
git push origin main
```

After a branch has been merged and is no longer needed:

```powershell
git branch -d feature/my-change
git push origin --delete feature/my-change
```

Local PC auto-update rule:

- the watcher script `scripts/watch_github_updates.ps1` checks `origin/main` and fast-forwards only `main`;
- it does not automatically merge feature/codex branches into `main`;
- if a new remote branch appears, inspect it first, then merge intentionally.

## Репозиторий

- GitHub: `https://github.com/xotizwf-create/Albery.git`
- Основная ветка: `main`
- Локальный проект Windows: `G:\OneDrive\Рабочий стол\Мои проекты\Евгений. Разработка`
- Серверный проект: `/var/www/albery`

## Сервер

- IP: `186.246.7.32`
- Пользователь: `root`
- ОС: Ubuntu 22.04
- Основной домен: `m4s.ru`
- Канонический web-домен: `www.m4s.ru`
- MCP-домен: `mcp.m4s.ru`

DNS-записи:

```text
A  @    186.246.7.32
A  www  186.246.7.32
A  mcp  186.246.7.32
```

Проверка DNS:

```bash
dig +short m4s.ru
dig +short www.m4s.ru
dig +short mcp.m4s.ru
```

## Структура На Сервере

```text
/var/www/albery/                  проект
/var/www/albery/.env              production env, не хранится в git
/var/www/albery/.venv/            Python venv
/var/www/albery/run_5002.py       запуск Flask на 127.0.0.1:5002
/var/www/albery/Интерфейс/        React/Vite frontend
/var/www/albery/Интерфейс/dist/   собранный frontend
/var/www/albery/scripts/          служебные скрипты
/var/backups/albery/postgres/     бэкапы PostgreSQL
/etc/systemd/system/albery.service systemd service
/etc/nginx/sites-available/albery Nginx site config
/etc/cron.d/albery-postgres-backup cron автобэкапа БД
```

## Запуск Приложения

Backend слушает только локально:

```text
127.0.0.1:5002
```

Публичный доступ идет через Nginx reverse proxy:

```text
https://www.m4s.ru -> http://127.0.0.1:5002
https://mcp.m4s.ru -> http://127.0.0.1:5002
```

Главная страница приложения:

```text
https://www.m4s.ru/main
```

MCP endpoint:

```text
https://mcp.m4s.ru/mcp/<MCP_SHARED_SECRET>
```

FAQ MCP endpoint for external assistants with limited rights:

```text
https://mcp.m4s.ru/mcp-faq/<MCP_FAQ_SHARED_SECRET>
```

The FAQ endpoint exposes only company knowledge/regulations, Zoom calls/transcripts (including stored Zoom call reports via `get_zoom_call_transcript`), org structure, AI instructions, source list, context guide, and health check tools. It does not expose Bitrix tasks, chats, report generation, report saving/deleting, OCR processing, compact exports, or instruction editing tools.

## Systemd

Сервис:

```bash
systemctl status albery --no-pager
systemctl restart albery
journalctl -u albery -n 120 --no-pager
```

Содержимое `/etc/systemd/system/albery.service`:

```ini
[Unit]
Description=Albery Flask App
After=network.target postgresql.service

[Service]
User=root
WorkingDirectory=/var/www/albery
EnvironmentFile=/var/www/albery/.env
ExecStart=/var/www/albery/.venv/bin/python /var/www/albery/run_5002.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

После изменения:

```bash
systemctl daemon-reload
systemctl enable --now albery
systemctl restart albery
```

## Nginx

Проверка:

```bash
nginx -t
systemctl reload nginx
tail -n 120 /var/log/nginx/error.log
```

Важные настройки:

- HTTP и IP должны редиректить на `https://www.m4s.ru`
- `m4s.ru` должен редиректить на `www.m4s.ru`
- `mcp.m4s.ru` остается отдельным хостом для MCP
- Для долгих Google Drive sync нужны proxy timeout `600s`

Рекомендуемый `/etc/nginx/sites-available/albery`:

```nginx
server {
    listen 80 default_server;
    server_name _;
    return 301 https://www.m4s.ru$request_uri;
}

server {
    listen 80;
    server_name m4s.ru www.m4s.ru mcp.m4s.ru;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name m4s.ru;

    ssl_certificate /etc/letsencrypt/live/m4s.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/m4s.ru/privkey.pem;

    return 301 https://www.m4s.ru$request_uri;
}

server {
    listen 443 ssl default_server;
    server_name _;

    ssl_certificate /etc/letsencrypt/live/m4s.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/m4s.ru/privkey.pem;

    return 301 https://www.m4s.ru$request_uri;
}

server {
    listen 443 ssl;
    server_name www.m4s.ru;

    ssl_certificate /etc/letsencrypt/live/m4s.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/m4s.ru/privkey.pem;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:5002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 600s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        send_timeout 600s;
    }
}

server {
    listen 443 ssl;
    server_name mcp.m4s.ru;

    ssl_certificate /etc/letsencrypt/live/m4s.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/m4s.ru/privkey.pem;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:5002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 600s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        send_timeout 600s;
    }
}
```

Применение:

```bash
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/albery /etc/nginx/sites-enabled/albery
nginx -t && systemctl reload nginx
```

## HTTPS

Сертификат Let's Encrypt выпущен на:

```text
m4s.ru
www.m4s.ru
mcp.m4s.ru
```

Команды:

```bash
certbot certificates
certbot renew --dry-run
```

Если нужно перевыпустить:

```bash
certbot --nginx -d m4s.ru -d www.m4s.ru -d mcp.m4s.ru
```

## PostgreSQL

- БД: `albery`
- Пользователь БД: `albery_app`
- Пароль хранится только в `/var/www/albery/.env`

Проверка подключения:

```bash
cd /var/www/albery
source .venv/bin/activate
python - <<'PY'
from dotenv import load_dotenv
load_dotenv("/var/www/albery/.env")
import app
with app.pg_connect() as conn:
    with conn.cursor() as cur:
        cur.execute("select current_database(), current_user")
        print(cur.fetchone())
PY
```

Применить схему/миграции:

```bash
cd /var/www/albery
.venv/bin/python scripts/ensure_postgres.py
```

## Env

Открыть production env:

```bash
nano /var/www/albery/.env
```

Важные переменные:

```env
DATABASE_URL=postgresql://...
DATABASE_ADMIN_URL=postgresql://...
FLASK_SECRET_KEY=...
ADMIN_PASSWORD_HASH=...
AUTH_SESSION_DAYS=30
AUTH_RATE_LIMIT_ATTEMPTS=6
AUTH_RATE_LIMIT_WINDOW_SECONDS=900
CANONICAL_WEB_HOST=www.m4s.ru
MCP_HOST=mcp.m4s.ru

BITRIX_WEBHOOK_BASE=...
BITRIX_EXPORT_MODE=audit
BITRIX_REQUEST_DELAY=0.05
BITRIX_LOOKBACK_DAYS=30
AUTO_SYNC_BITRIX_LOOKBACK_DAYS=30
AUTO_SYNC_CHAT_LOOKBACK_DAYS=1
AUTO_SYNC_CHAT_GENERATE_REPORTS=0
AUTO_SYNC_ZOOM_FROM=2026-01-01
AUTO_SYNC_ZOOM_TO=
AUTO_SYNC_GOOGLE_DRIVE_ZOOM_TRANSCRIPTS=1

MCP_SHARED_SECRET=...
MCP_ALLOW_UNAUTHENTICATED=0
MCP_FAQ_SHARED_SECRET=...

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
OPENAI_API_MODE=responses
OPENAI_TIMEOUT_SECONDS=120

GOOGLE_API_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
GOOGLE_API_KEY=...
GOOGLE_MODEL=gemini-2.0-flash

GOOGLE_APPS_SCRIPT_SYNC_URL=...
GOOGLE_APPS_SCRIPT_SYNC_TOKEN=...
GOOGLE_DRIVE_COMPANY_ROOT_NAME=Google Drive
GOOGLE_DRIVE_SYNC_TIMEOUT_SECONDS=600
GOOGLE_CALLS_APPS_SCRIPT_SYNC_URL=
GOOGLE_CALLS_APPS_SCRIPT_SYNC_TOKEN=...

ZOOM_ACC2_ACCOUNT_ID=...
ZOOM_ACC2_CLIENT_ID=...
ZOOM_ACC2_CLIENT_SECRET=...
ZOOM_OAUTH_URL=https://zoom.us/oauth/token
ZOOM_API_BASE_URL=https://api.zoom.us/v2
```

Не вставлять реальные секреты в git или чат. `.env` исключен через `.gitignore`.

Сгенерировать hash пароля админки:

```bash
cd /var/www/albery
source .venv/bin/activate
python - <<'PY'
from getpass import getpass
from werkzeug.security import generate_password_hash
password = getpass("Admin password: ")
print("ADMIN_PASSWORD_HASH=" + generate_password_hash(password))
PY
```

## Автоматическая Почасовая Синхронизация

Почасовая синхронизация ставится отдельным cron-файлом:

```text
/etc/cron.d/albery-daily-sync
```

Время запуска:

```text
каждый час в 00 минут по Europe/Moscow
```

Установить или обновить cron:

```bash
cd /var/www/albery && ./scripts/install_daily_sync_cron.sh
```

Запустить вручную:

```bash
cd /var/www/albery
ALBERY_LOG_DIR=/var/log/albery ALBERY_DAILY_SYNC_LOG=/var/log/albery/daily-sync.log .venv/bin/python scripts/run_daily_sync.py
```

Что запускает `scripts/run_daily_sync.py`:

- `bitrix_team` - синхронизация сотрудников Bitrix
- `bitrix_tasks` - синхронизация Bitrix-задач за период
- `bitrix_chat_messages` - синхронизация списка чатов и сообщений
- `zoom_api_calls` - синхронизация Zoom-созвонов через Zoom API
- `google_drive_company_instructions` - подтягивание Google Drive документов/инструкций в раздел "О компании"
- `google_drive_zoom_transcripts` - подтягивание `transcript.txt` из Google Drive для Zoom, если включено

Логи:

```text
/var/log/albery/daily-sync.log       структурированный JSONL-лог каждого шага
/var/log/albery/daily-sync.cron.log  stdout/stderr cron-обертки
```

Смотреть логи:

```bash
tail -n 200 /var/log/albery/daily-sync.log
tail -n 200 /var/log/albery/daily-sync.cron.log
grep '"status": "failed"' /var/log/albery/daily-sync.log
```

Настройки в `.env`:

```env
AUTO_SYNC_BITRIX_LOOKBACK_DAYS=30
AUTO_SYNC_CHAT_LOOKBACK_DAYS=1
AUTO_SYNC_CHAT_GENERATE_REPORTS=0
AUTO_SYNC_ZOOM_FROM=2026-01-01
AUTO_SYNC_ZOOM_TO=
AUTO_SYNC_GOOGLE_DRIVE_ZOOM_TRANSCRIPTS=1
```

Если нужно, чтобы при вечерней синхронизации сразу формировались дневные отчеты по чатам:

```env
AUTO_SYNC_CHAT_GENERATE_REPORTS=1
```

## Деплой И Обновление

Основная команда обновления сервера:

```bash
cd /var/www/albery && ./scripts/update_server.sh
```

## FAQ MCP

Use this URL for the limited FAQ MCP server:

```text
https://mcp.m4s.ru/mcp-faq/<MCP_FAQ_SHARED_SECRET>
```

Set the secret in `/var/www/albery/.env`:

```env
MCP_FAQ_SHARED_SECRET=...
```

Allowed tools: `start_here_always_read_ai_instructions`, `health`, `get_context_guide`, `get_ai_instructions`, `list_available_sources`, `get_company_profile`, `list_company_files`, `get_company_file`, `search_company_knowledge`, `get_org_structure`, `list_zoom_calls`, `get_zoom_call_transcript`, `search_zoom_transcripts`.

Scope: org structure, regulations/company knowledge, AI instructions, and Zoom calls (transcripts + stored Zoom call reports exposed via `get_zoom_call_transcript.analytical_note`).

Unavailable there: Bitrix tasks, chats/messages, OCR processing, chat/owner report reading/generation/saving/deleting, compact export, Bitrix refresh, AI instruction editing, and Zoom report saving/deleting.

Что делает `scripts/update_server.sh`:

- `git fetch` и `git pull --ff-only origin main`
- создает `.venv`, если его нет
- обновляет `pip`
- ставит `requirements.txt`
- применяет PostgreSQL migrations через `scripts/ensure_postgres.py`
- находит frontend `package.json`
- делает `npm ci`
- делает `npm run build`
- перезапускает `albery`
- ждет доступности `http://127.0.0.1:5002`

Если после деплоя проблема:

```bash
systemctl status albery --no-pager
journalctl -u albery -n 120 --no-pager
curl -I http://127.0.0.1:5002
```

## Google Apps Script (Google Drive company sync)

Код Apps Script, который отдаёт документы и дерево папок из Google Drive,
лежит в репозитории и деплоится через `clasp` (а НЕ через `update_server.sh`).

- Проект: `scripts/google_drive_company_sync_project/` (`Code.gs`, `appsscript.json`)
- `.clasp.json` в корне: `scriptId = 1ga97W3bs386A00JokAHZyiQffEC1fFFjOoyzJm5GXUYkKjd3aXiRUoO9`
- Логин clasp: владелец скрипта `xotizwf@gmail.com` (`clasp show-authorized-user`)
- Прод web app deployment (это и есть `/exec` в `GOOGLE_APPS_SCRIPT_SYNC_URL`):
  - deploymentId = `AKfycbwsEL8z_HAoNmLP9utV4HCtkNDAcgbaAxnWsZ1Njs7h4L6DcrmRzcehzxB1y070CarBgA`
  - `/exec` URL = `https://script.google.com/macros/s/<deploymentId>/exec`

Обновление кода Apps Script (обновляет существующий `/exec`, URL не меняется):

```bash
clasp push --force
clasp redeploy AKfycbwsEL8z_HAoNmLP9utV4HCtkNDAcgbaAxnWsZ1Njs7h4L6DcrmRzcehzxB1y070CarBgA -d "что изменилось"
clasp deployments   # проверить активную версию
```

ВАЖНО — почему раньше `/exec` отдавал 404 после CLI-деплоя:

- Используйте `clasp redeploy <deploymentId>` (обновление на месте), а не
  `clasp deploy` (создаёт НОВЫЙ deployment с другим URL).
- В `appsscript.json` обязан быть блок `webapp`, иначе clasp создаёт версию
  без web app entry point и `/exec` отдаёт 404:

  ```json
  "webapp": { "executeAs": "USER_DEPLOYING", "access": "ANYONE_ANONYMOUS" }
  ```

Проверка, что `/exec` живой (должен вернуть JSON `{"ok": true, ...}`, не HTML):

```bash
token=$(awk -F= '$1=="GOOGLE_APPS_SCRIPT_SYNC_TOKEN"{print $2; exit}' .env)
url=$(awk -F= '$1=="GOOGLE_APPS_SCRIPT_SYNC_URL"{sub(/^[^=]*=/,""); print; exit}' .env)
curl -sS -L "$url?token=$token" | head -c 200
```

Логика синхронизации (инкрементально, без перезаписи неизменных файлов):

- сервер шлёт в Apps Script `known_files`/`known_folders` (id, имя, `updated_at`,
  `parent_folder_id`, `path`); скрипт по ним помечает неизменные файлы
  `unchanged: true` и НЕ выгружает их контент (DOC/XLS не конвертируются заново);
- сервер обновляет `company_folders` только при реальном изменении
  (сравнение `content_hash`, пути, родителя), иначе только `last_seen_at`;
- структура Google Drive зеркалится: папки -> `company_drive_folders` +
  `company_folders`, документы кладутся внутрь своих папок;
- временный сбой конвертации (`document_errors`, напр. Drive rate limit) НЕ
  удаляет уже синхронизированный документ — он подтянется на следующем прогоне.

Запустить серверную синхронизацию вручную:

```bash
cd /var/www/albery
.venv/bin/python - <<'PY'
from dotenv import load_dotenv; load_dotenv("/var/www/albery/.env")
import app, json
print(json.dumps(app.sync_google_drive_company_documents(), ensure_ascii=False, default=str)[:600])
PY
```

Авто-обновление по триггеру (near-realtime, ~1 минута):

- В Apps Script стоит time-driven триггер `checkDriveChangesAndNotify`
  (раз в минуту). Он считает лёгкую сигнатуру дерева папки (id, время правки,
  имена, родители; БЕЗ контента) и пингует сервер ТОЛЬКО при реальном изменении.
- Сервер: вебхук `POST /google-drive/events/<GOOGLE_DRIVE_EVENT_SECRET>` запускает
  ту же инкрементальную синхронизацию под Postgres advisory-lock (не пересекается
  с почасовым cron / ручной кнопкой; если занято — 409, триггер повторит).
- Секрет: `.env` `GOOGLE_DRIVE_EVENT_SECRET` == `CHANGE_NOTIFY_URL` в `Code.gs`.
- Триггер создаётся ОДИН раз вручную: открыть редактор скрипта, выбрать функцию
  `setupDriveChangeTrigger`, нажать Run, пройти авторизацию (нужны scope
  `script.scriptapp` и `drive`). Повторный запуск безопасен (старые триггеры
  удаляются). Снять триггер: запустить `removeDriveChangeTriggers`.
- Проверка вебхука: `curl https://mcp.m4s.ru/google-drive/events/<secret>` -> JSON
  `{"ok": true, ...}`; `POST` туда же запускает синхронизацию и возвращает result.
- Триггеры в Apps Script: https://script.google.com/home/triggers

## Frontend

Папка:

```bash
/var/www/albery/Интерфейс
```

Команды:

```bash
cd /var/www/albery/Интерфейс
npm ci
npm run build
```

Node.js должен быть современный. На сервере ставился Node 20 через NodeSource, потому что Ubuntu apt давал Node 12, а Vite требует Node >=18.

## Бэкапы БД

Автоматический ежедневный бэкап установлен:

```text
/etc/cron.d/albery-postgres-backup
```

Скрипты:

```text
scripts/backup_postgres.sh
scripts/restore_postgres.sh
scripts/install_backup_cron.sh
```

Папка бэкапов:

```bash
/var/backups/albery/postgres/
```

Ручной бэкап:

```bash
cd /var/www/albery && ./scripts/backup_postgres.sh
```

Установить/обновить cron:

```bash
cd /var/www/albery && ./scripts/install_backup_cron.sh
```

Восстановить custom dump:

```bash
cd /var/www/albery
./scripts/restore_postgres.sh /var/backups/albery/postgres/file.dump
systemctl restart albery
```

Восстановить plain SQL:

```bash
cd /var/www/albery
./scripts/backup_postgres.sh
DATABASE_URL=$(awk -F= '$1=="DATABASE_URL"{sub(/^[^=]*=/,""); print; exit}' .env)
psql "$DATABASE_URL" < /var/backups/albery/postgres/file.sql
.venv/bin/python scripts/ensure_postgres.py
systemctl restart albery
```

Сделать локальный SQL-бэкап на Windows:

```powershell
cd "G:\OneDrive\Рабочий стол\Мои проекты\Евгений. Разработка"
$envLine = Get-Content .env | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
$DATABASE_URL = $envLine -replace '^DATABASE_URL=', ''
& 'C:\Program Files\PostgreSQL\18\bin\pg_dump.exe' --format=plain --no-owner --no-acl --clean --if-exists --file .\backups\albery_local.sql $DATABASE_URL
```

Загрузить локальный SQL на сервер:

```powershell
scp .\backups\albery_local.sql root@186.246.7.32:/var/backups/albery/postgres/albery_local.sql
```

## Известные Исправления

- Flask отдает frontend из `Интерфейс/dist`.
- `/` редиректит на `/main`.
- `/main` защищен паролем.
- Сессия админки хранится в signed cookie Flask.
- Пароль админки хранится hash-строкой `ADMIN_PASSWORD_HASH`.
- `CANONICAL_WEB_HOST=www.m4s.ru` редиректит web-трафик на `www`.
- MCP остается доступен через `mcp.m4s.ru` и `MCP_SHARED_SECRET`.
- Google Drive sync требует увеличенных таймаутов: frontend/backend/Nginx по 600 секунд.
- PDF-отчеты Bitrix на Linux требуют шрифты:

```bash
apt install -y fonts-dejavu-core fonts-liberation
```

## Частые Команды

Обновить код и перезапустить:

```bash
cd /var/www/albery && ./scripts/update_server.sh
```

Открыть env:

```bash
nano /var/www/albery/.env
```

Проверить backend:

```bash
curl -I http://127.0.0.1:5002
```

Проверить публичные домены:

```bash
curl -I https://www.m4s.ru
curl -I https://mcp.m4s.ru
```

Проверить логи:

```bash
journalctl -u albery -n 120 --no-pager
tail -n 120 /var/log/nginx/error.log
```

Перезапустить сервисы:

```bash
systemctl restart albery
nginx -t && systemctl reload nginx
```

## VPN-шлюз: весь исходящий трафик прод-сервера через Эстонию (AmneziaWG)

Настроено 2026-05-27.

### Зачем

Прод-сервер `186.246.7.32` — российский. Некоторые иностранные сервисы режут
российские IP (например, OpenAI/Codex отдают `HTTP 403`). Чтобы такие сервисы
открывались, **весь исходящий трафик сервера заворачивается через эстонский
AmneziaWG-VPN** и выходит в интернет с эстонского IP `95.85.243.43`.
При этом **сайт `m4s.ru`/`mcp.m4s.ru` и SSH остаются на прямом российском IP** —
входящие посетители не страдают.

Проверка эффекта: с сервера `curl https://api.openai.com/v1/models` отдаёт `403`
напрямую и `401` (т.е. дошли, нужен только ключ) через VPN. Gemini/googleapis
доступен из РФ и без VPN.

### Серверы и ключи (.env)

```env
# Эстонский VPN-сервер (там стоит Amnezia/AmneziaWG)
VPN_SERVER_HOST=IP: 95.85.243.43
VPN_SERVER_USER=root
VPN_SERVER_PASSWORD=...
# Российский прод-сервер
root_password=...
```

- Эстонский сервер `95.85.243.43`: Amnezia в Docker. Профиль **1234** (UDP 1234,
  контейнер `amnezia-awg-1234`, клиент `10.8.2.2`) закреплён за прод-сервером.
  Там же есть старый профиль на UDP 47138 с личными устройствами владельца —
  **его не трогать**. Клиентский конфиг профиля 1234:
  `C:\Users\hotiz\Desktop\amnezia-estonia-1234.conf` — НЕ импортировать на ПК
  (адрес `10.8.2.2` уже занят прод-сервером, будет конфликт).
- Российский прод-сервер `186.246.7.32`: на нём стоит **клиент AmneziaWG** (`awg0`).

### Как подключаться к серверам (без SSH-ключей, пароль из .env)

Вход по паролю root через Python/Paramiko (как и для всего остального в этом проекте):

```python
import re, paramiko
env = {...}  # прочитать .env
host = re.search(r"\d+\.\d+\.\d+\.\d+", env["VPN_SERVER_HOST"]).group(0)  # 95.85.243.43
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username="root", password=env["VPN_SERVER_PASSWORD"],
          look_for_keys=False, allow_agent=False)
# для прод-сервера: host="186.246.7.32", password=env["root_password"]
```

Пароли и приватные ключи в чат/логи не выводить.

### Как это работает (архитектура)

- На прод-сервере поднят интерфейс `awg0` (AmneziaWG) с `Table = off` — туннель
  сам по себе НЕ меняет маршруты. Маршрутизацией управляет отдельный скрипт.
- **Policy-routing** (`/root/vpn_apply.sh`, запускается как `PostUp` туннеля):
  - таблица `200`: маршрут по умолчанию через `awg0` (→ Эстония);
  - `ip rule` для `sport 22/443/80 → main` и пометка connmark входящих на `eth0`
    соединений (`fwmark 0x1 → main`) — ответы сервера как сайта/SSH уходят прямо
    через `eth0` на российский IP;
  - всё остальное (исходящее, инициированное сервером) → таблица `200` → туннель;
  - endpoint VPN, локальная подсеть и DNS-апстримы (85.193.93.193/194) прибиты
    прямым маршрутом через `eth0`, чтобы не зацикливать туннель и не ломать DNS;
  - туннель только IPv4, поэтому исходящий IPv6 в интернет **заблокирован**
    (`ip6tables` REJECT для NEW на `2000::/3`, входящий на сайт по IPv6 сохранён),
    плюс `/etc/gai.conf` предпочитает IPv4. Без этого приложения (например Codex)
    уходят по российскому IPv6 в обход VPN и получают блок.
- `PreDown` (`/root/vpn_rollback.sh`) снимает всю эту маршрутизацию при остановке
  туннеля, возвращая сервер на прямой маршрут (сайт при этом продолжает работать).

### Файлы на прод-сервере

```text
/etc/amnezia/amneziawg/awg0.conf                 конфиг туннеля (Table=off, PostUp/PreDown, MTU=1280)
/root/vpn_apply.sh                               включает policy-routing (endpoint определяется сам)
/root/vpn_rollback.sh                            снимает policy-routing
/root/vpn_apply.log                              лог apply/rollback
/usr/local/sbin/vpn-healthcheck.sh               тест состояния VPN (exit 0 = OK)
/usr/local/sbin/vpn-watchdog.sh                  авто-перезапуск туннеля, если он реально упал
/usr/local/sbin/amneziawg-ensure-module.sh       пересборка модуля ядра при апгрейде ядра
/etc/systemd/system/awg-quick@awg0.service.d/override.conf   ExecStartPre=ensure-module
/etc/systemd/system/vpn-watchdog.{service,timer}
/etc/modules-load.d/amneziawg.conf
/usr/src/amneziawg-linux-kernel-module, /usr/src/amneziawg-tools   исходники
```

### Установка с нуля (что было сделано на прод-сервере)

PPA `ppa:amnezia/amneziawg` больше нет — ставится из исходников с GitHub
(GitHub с сервера доступен):

```bash
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y build-essential dkms git "linux-headers-$(uname -r)"
# модуль ядра
cd /usr/src && git clone --depth=1 https://github.com/amnezia-vpn/amneziawg-linux-kernel-module
cd amneziawg-linux-kernel-module/src && make -j"$(nproc)" && make install && depmod -a && modprobe amneziawg
# утилиты awg / awg-quick
cd /usr/src && git clone --depth=1 https://github.com/amnezia-vpn/amneziawg-tools
cd amneziawg-tools/src && make -j"$(nproc)" && make install
```

Конфиг `/etc/amnezia/amneziawg/awg0.conf` = клиентский конфиг профиля 1234, но:
добавлено `Table = off`, `MTU = 1280`, `PostUp = /root/vpn_apply.sh`,
`PreDown = /root/vpn_rollback.sh`; строка `DNS = ...` удалена (чтобы awg-quick
не трогал системный DNS). Включение:

```bash
systemctl enable --now awg-quick@awg0
```

### Автозапуск после перезагрузки сервера

«Всегда включён» обеспечивают:

- `systemctl enable awg-quick@awg0` — туннель + policy-routing (через PostUp)
  поднимаются при загрузке (после `network-online.target`);
- `ExecStartPre=/usr/local/sbin/amneziawg-ensure-module.sh` — если ядро
  обновилось и модуля для него нет, он пересобирается перед стартом туннеля;
- `/etc/modules-load.d/amneziawg.conf` — модуль грузится на раннем этапе;
- `PersistentKeepalive=25` в конфиге — туннель сам переустанавливает handshake;
- **watchdog** `vpn-watchdog.timer` (каждые ~3 мин): если handshake устарел И
  через туннель нет интернета — перезапускает `awg-quick@awg0`.

### Тест / проверка состояния

Быстрый health-тест (печатает статус, код возврата 0 = всё ок):

```bash
ssh root@186.246.7.32 /usr/local/sbin/vpn-healthcheck.sh
```

Проверяет: сервис enabled+active, свежесть handshake, выходной IP = `95.85.243.43`,
доступность OpenAI (не 403), что сайт `:5002` жив.

Проверка пути загрузки БЕЗ полной перезагрузки (удалить интерфейс и поднять заново
через systemd — имитация ребута):

```bash
ssh root@186.246.7.32 'awg-quick down awg0; systemctl restart awg-quick@awg0; sleep 5; /usr/local/sbin/vpn-healthcheck.sh'
```

Полный тест ребутом (сайт на ~1 мин недоступен во время перезагрузки; VPN должен
подняться сам):

```bash
ssh root@186.246.7.32 reboot
# подождать ~1-2 минуты, затем:
ssh root@186.246.7.32 /usr/local/sbin/vpn-healthcheck.sh   # ожидаем RESULT: OK
```

### Как поставить ДРУГОЙ VPN (заменить профиль/сервер)

Если появится новый VPN (другой эстонский/другой сервер) с AmneziaWG-конфигом:

1. Положить новый клиентский конфиг в `/etc/amnezia/amneziawg/awg0.conf`.
2. Дописать/сохранить в секции `[Interface]`:
   `Table = off`, `MTU = 1280`,
   `PostUp = /root/vpn_apply.sh`, `PreDown = /root/vpn_rollback.sh`;
   удалить строку `DNS = ...`.
3. Перезапустить и проверить:

```bash
systemctl restart awg-quick@awg0
/usr/local/sbin/vpn-healthcheck.sh
```

Менять `vpn_apply.sh` НЕ нужно — IP нового VPN-сервера (endpoint) он определяет
сам из туннеля. Если новый VPN — обычный WireGuard (без обфускации), используется
стандартный `wg`/`wg-quick` и `/etc/wireguard/awg0.conf`, остальная логика та же.

### Откат / временно отключить VPN (сервер вернётся на прямой IP, сайт продолжит работать)

```bash
ssh root@186.246.7.32 'bash /root/vpn_rollback.sh && awg-quick down awg0'
# отключить и автозапуск:
ssh root@186.246.7.32 'systemctl disable --now awg-quick@awg0 vpn-watchdog.timer'
```

### Диагностика

```bash
awg show awg0                       # handshake, трафик, endpoint
ip rule show                        # должны быть правила 900/901/902/1000/1001
ip route show table 200             # default dev awg0 + прямые маршруты endpoint/DNS
tail -n 40 /root/vpn_apply.log
journalctl -t vpn-watchdog -n 20    # срабатывания сторожа
curl -s https://ifconfig.me/ip      # должно быть 95.85.243.43
```

Кросс-проверка с эстонской стороны (endpoint пира `10.8.2.2` должен показывать
`186.246.7.32` — это и есть прод-сервер):

```bash
ssh root@95.85.243.43 "docker exec amnezia-awg-1234 wg show wg0"
```

## Codex (OpenAI) на прод-сервере

Установлен `@openai/codex` (codex-cli) глобально через npm (Node 20+ уже есть):

```bash
npm install -g @openai/codex
codex --version
```

**Вход в ChatGPT-аккаунт НА сервере напрямую сделать нельзя:** OpenAI (Cloudflare)
блокирует страницы входа и `codex login --device-auth` с серверных/дата-центровых
IP (403). При этом сам рантайм Codex с уже готовым токеном через VPN работает.
Поэтому вход переносится с ПК:

1. На ПК, где Codex залогинен в ChatGPT (`codex login` через браузер), взять файл
   `%USERPROFILE%\.codex\auth.json` (Windows) или `~/.codex/auth.json` (Linux/mac).
2. Положить его на сервер в `/root/.codex/auth.json` (chmod 600).
3. Проверить: `codex login status` → должно быть `Logged in using ChatGPT`.

**Критично:** без блокировки исходящего IPv6 (см. VPN-раздел, она в `vpn_apply.sh`)
Codex идёт в `wss://chatgpt.com/backend-api/codex/responses` по российскому IPv6 в
обход VPN и получает `403`. С блокировкой IPv6 трафик идёт через IPv4-VPN (Эстония),
и бэкенд отвечает.

Тест запроса с сервера:

```bash
cd /tmp && printf "Respond with exactly: PING-OK" | codex exec --skip-git-repo-check
```

- Реальная работа Codex зависит от тарифа ChatGPT-аккаунта: на бесплатном будет
  `You've hit your usage limit` — нужен ChatGPT Plus/Pro с квотой Codex.
- Если вход на сервере «протух» (токен истёк и не обновляется автоматически) —
  просто заново скопировать свежий `~/.codex/auth.json` с ПК.
- Логин/пароль ChatGPT в `.env` (`GPT Логин`/`GPT_Password`) для входа Codex
  бесполезны (вход только браузером) — их лучше убрать из `.env`.

## Hermes Agent (автономный ИИ-агент, мозг = ChatGPT Plus)

Настроено 2026-05-27. Поставлен локально на ПК в WSL2 для теста; следующий шаг —
перенос на прод-сервер.

### Что это и зачем

`Hermes Agent` (Nous Research, open-source) — автономный агент с **постоянной
памятью, навыками, cron-планировщиком и MCP-клиентом**. Он **model-agnostic**:
своего «мозга» у него нет, модель подключается по OAuth/API. Это **обвязка-агент**
(как Codex/Cline), а НЕ модель.

Наша задача: «цифровой сотрудник» по отчётам/действиям (а не правка кода). Albery
уже отдаёт инструменты для этого через MCP — Hermes выступает автономным
исполнителем поверх них. «Мозг» сейчас — **ChatGPT Plus (модель `gpt-5.5`)**.

### Ключевые пути и факты

```text
версия:        v0.14.0
бинарь:        ~/.local/bin/hermes
установка:     ~/.hermes/hermes-agent
конфиг:        ~/.hermes/config.yaml
токены аккаунтов: ~/.hermes/auth.json   ← для смены аккаунта и переноса на сервер
провайдер:     openai-codex   (= аккаунт ChatGPT через OAuth)
модель:        gpt-5.5        (доступна и gpt-5.4)
дашборд:       http://127.0.0.1:9119   (hermes dashboard --tui)
```

### Важные понятия (чтобы не путать)

- Провайдер **`openai-codex`** — это НЕ отдельный урезанный продукт, а технический
  **OAuth-канал к твоему аккаунту ChatGPT**. Модель `gpt-5.5` — обычная ChatGPT.
- Подписку ChatGPT в Hermes можно подключить **только** через `openai-codex`.
  Провайдер `openai` использует **API-ключ**, а не подписку.
- **Лимиты**: на подписке Plus они есть всегда, как ни подключайся. «Без потолка» —
  только **API-ключ** (платишь по токенам). Для автономного агента 24/7 на сервере
  правильнее API-ключ; подписка Plus периодически будет упираться в `usage limit`.

### Предусловие на Windows-ПК: сеть WSL2 через VPN

`AmneziaVPN` на Windows ломает сеть внутри WSL2 (исходящий TCP виснет). Лечится
**mirrored-режимом** — WSL начинает ходить через сетевой стек хоста (вместе с VPN,
выход через Эстонию `95.85.243.43`), и API OpenAI/Anthropic становятся доступны.

Создать `C:\Users\<user>\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
dnsTunneling=true
autoProxy=true
```

Затем `wsl --shutdown` (перезапуск WSL). Проверка из WSL:
`curl -s https://api.ipify.org` → должно быть `95.85.243.43`.

### Установка Hermes (Linux / WSL2 / сервер)

Требуется **Node.js 20+** (для сборки веб-дашборда на Vite) и Python 3.11
(ставит установщик сам). На прод-сервере Node 20 уже есть; в WSL ставился nvm-ом.

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
source ~/.bashrc        # или новый терминал
hermes --version
```

- Установщик ставит uv, Python 3.11, клонирует репо, создаёт venv.
- Опциональные `ripgrep`/`ffmpeg` требуют sudo — можно пропустить (агент работает
  без них; поиск идёт через grep).
- **ВАЖНО при неинтерактивном запуске** (фон, `curl | bash` без терминала):
  установщик читает `/dev/tty` и **виснет на sudo-вопросе про ripgrep/ffmpeg**.
  Запускать в сеансе без управляющего терминала (`setsid`) и с флагами
  `--skip-setup --skip-browser`, либо просто ставить в живом SSH-терминале.

Если в системе старый Node — поставить 20 через nvm (без sudo):

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
nvm install 20 && nvm alias default 20
# затем пересобрать веб-морду дашборда:
cd ~/.hermes/hermes-agent/web && rm -rf node_modules package-lock.json && npm install && npm run build
```

### Подключение аккаунта ChatGPT (openai-codex, OAuth device-code)

```bash
hermes auth add openai-codex --type oauth
```

- Покажет URL `https://auth.openai.com/codex/device` и короткий код.
- В браузере сначала **войти в аккаунт ChatGPT** (страница перекинет на `/log-in`),
  потом **снова открыть** `/codex/device`, ввести код, подтвердить (Authorize).
- Аккаунт должен быть **Plus/Pro** — на бесплатном будет `usage limit`.
- Токен сохраняется в `~/.hermes/auth.json`. Проверка: `hermes auth list`.
- **IP-нюанс**: вход с датацентрового IP Cloudflare может блокировать (`403`).
  На ПК (через VPN-Эстонию) сработало. На сервере вход может не пройти — см. перенос.

### Провайдер и модель по умолчанию

```bash
hermes config set model.provider openai-codex
hermes config set model.default gpt-5.5      # или gpt-5.4; интерактивно: hermes model
# проверка:
printf '' | hermes -z "Respond with exactly: PING-OK"
```

### Смена аккаунта / провайдера

```bash
hermes auth list                       # что подключено
hermes auth logout openai-codex        # выйти из аккаунта
hermes auth remove <index|id|label>    # удалить конкретный кредл
hermes auth add openai-codex --type oauth   # войти заново (другим аккаунтом)
```

Переключиться на **API-ключ** (без потолка лимитов, платно по токенам):

```bash
hermes auth add openai --type api-key --api-key sk-...     # или anthropic / gemini
hermes config set model.provider openai                    # anthropic | gemini
hermes config set model.default <model-id>
```

Весь токен-стор — один файл `~/.hermes/auth.json` (его можно бэкапить/копировать).

### Запуск

```bash
hermes                       # чат в терминале
hermes dashboard --tui       # веб-дашборд: чат + настройки + сессии + cron, порт 9119
#   полезные флаги: --no-open  --port N  --skip-build (отдать готовый dist без сборки)
#                   --status   --stop
hermes gateway run           # фоновый шлюз/cron (для «сотрудника»); install — как сервис
```

Дашборд слушает `127.0.0.1:9119`; при mirrored-WSL открывается в браузере Windows
по `http://localhost:9119`.

### Перенос на прод-сервер (`186.246.7.32`, Ubuntu 22.04)

1. Установить (Node 20 там уже есть):
   `curl -fsSL .../install.sh | bash` (в живом SSH-терминале — TTY-хак не нужен).
2. **Логин аккаунта** — упрётся в датацентровый IP (как было с Codex). Два пути:
   - **(а) device-login:** на сервере `hermes auth add openai-codex --type oauth`,
     а URL+код открыть/ввести в браузере на ПК (жилой IP). Если опрос токена с
     сервера получит `403` — использовать путь (б).
   - **(б) скопировать готовый `~/.hermes/auth.json` с ПК на сервер** (как делали с
     `~/.codex/auth.json` для Codex): `scp ... root@186.246.7.32:/root/.hermes/auth.json`,
     `chmod 600`. Тогда отдельный вход на сервере не нужен.
3. Прописать провайдера/модель (`hermes config set ...`, см. выше).
4. Держать запущенным: `hermes gateway install` (systemd-сервис, автозапуск) или
   дашборд как сервис.
5. VPN на сервере уже заворачивает исходящий трафик через Эстонию (см. раздел про
   VPN-шлюз) — он нужен, иначе OpenAI отдаёт `403` российскому IP.

### Что нужно для работы (чек-лист)

- **Node 20+** (дашборд) и Python 3.11 (ставит установщик).
- **Рабочий мозг**: `openai-codex` (ChatGPT Plus) или API-ключ.
- **Нероссийский исходящий IP** (VPN) — OpenAI режет РФ.
- Для роли «сотрудник по отчётам» (следующий этап): подключить **MCP Albery**
  (секция `mcp_servers` в `~/.hermes/config.yaml`, HTTP-транспорт на
  `https://mcp.m4s.ru/mcp/<MCP_SHARED_SECRET>`) и настроить **cron-задачи**
  (`hermes cron`) — тогда агент сам по расписанию читает инструкции, собирает
  данные и формирует отчёты через инструменты Albery.

### Служебное (на ПК)

- `C:\Users\<user>\.wslconfig` — mirrored-сеть WSL (не удалять, иначе WSL снова
  потеряет интернет под VPN).
- Разовые вспомогательные скрипты установки лежали в `C:\Users\<user>\*.sh` —
  их можно удалить, на работу агента не влияют.

### Прод-развёртывание Hermes (выполнено 2026-05-27)

Hermes перенесён с ПК на прод-сервер `186.246.7.32` и работает **24/7** как
systemd-служба. Мозг — ChatGPT Plus (`gpt-5.5`) через `openai-codex` OAuth.
Чтобы не сжигать 5-часовой лимит Codex, reasoning на проде снижен до `medium`:
`model.reasoning_effort=medium` и `agent.reasoning_effort=medium`.

Расположение на сервере:

```text
бинарь:   /usr/local/bin/hermes
код:      /usr/local/lib/hermes-agent
дом:      /root/.hermes/   (config.yaml, .env, cron/, sessions/, logs/, state.db)
служба:   /etc/systemd/system/hermes-gateway.service  (enabled+active, автозапуск)
```

Что сделано:

- **Вход в ChatGPT:** `/root/.hermes/auth.json` скопирован с ПК (chmod 600) — вход
  на серверном IP блокирует Cloudflare, а готовый токен работает через VPN-Эстонию.
- **Swap:** добавлен `/swapfile` 2 ГБ (`vm.swappiness=10`, в `/etc/fstab`) — страховка
  от OOM на 2 ГБ RAM. Сам агент лёгкий (~185–270 МБ), модель считается удалённо.
- **MCP:** в `/root/.hermes/config.yaml` секция `mcp_servers.albery` =
  `https://mcp.m4s.ru/mcp/<MCP_SHARED_SECRET>` (секрет из `/var/www/albery/.env`,
  auth none). 38 инструментов. TZ сервера = Europe/Moscow, поэтому cron-время — МСК.
- **Gateway-служба:** `hermes gateway install --system --run-as-user root`
  (под root, т.к. весь стек тут под root). Планировщик cron работает автоматически.
- **Telegram:** подключён к gateway через `/root/.hermes/.env`:
  `TELEGRAM_BOT_TOKEN=<из .env TG_BOT_TOKEN>` и
  `TELEGRAM_ALLOWED_USERS=<из .env TG_ID>`. Установлена зависимость
  `python-telegram-bot[webhooks]` в `/usr/local/lib/hermes-agent/venv`.
  Пользователь нажал `/start`, после этого проверочный пуш через
  `hermes send --to telegram:<TG_ID> ...` вернулся `sent`.
- **Тихий Telegram-режим:** в `/root/.hermes/config.yaml` включено
  `display.platforms.telegram.tool_progress: off` — Telegram не показывает
  технические tool-calls вида `mcp_albery_*`; и `cron.wrap_response: false` —
  cron присылает только чистый ответ агента без обёртки `Cronjob Response`,
  `job_id` и подсказки `stop reminder`.

Две автоматизации (`hermes cron list`):

- `zoom-to-tasks` — `*/30 * * * *`: находит Zoom-созвоны без отчёта
  **без расхода Codex на пустых проверках**. Реализовано как `no-agent` cron
  со скриптом `/root/.hermes/scripts/zoom_watchdog.sh`: скрипт напрямую и быстро
  проверяет PostgreSQL (`zoom_calls` за последние 2 дня, `analytical_note=''`,
  транскрипт есть). Если новых Zoom нет — stdout пустой, Telegram молчит, LLM не
  вызывается. Если есть новый Zoom — только тогда скрипт запускает `hermes -z` с
  промптом из `/root/.hermes/scripts/hermes_zoom_to_tasks_prompt.txt`
  (источник правды в git: [scripts/hermes_zoom_to_tasks_prompt.txt](scripts/hermes_zoom_to_tasks_prompt.txt)).
  Промпт подставляет `$DATE_FROM`, `$DATE_TO`, `$MISSING` через awk-substitution
  (без проблем со sed-escaping для многострочных значений).
  В скрипте есть `flock` и cooldown 7200 сек на тот же набор missing-call id,
  чтобы при ошибке не жечь лимит каждые 30 минут.

  **Что делает агент.** По промпту строго:
  1. `start_here_always_read_ai_instructions` → `get_context_guide`;
  2. для каждого созвона: `get_report_contract(zoom_processing)` (контракт — 13 KB, 12 разделов в `report_text` + JSON-схема с обязательным `operational_tasks`), `get_zoom_call_transcript(include_full_text=true)`, `get_org_structure`;
  3. формирует отчёт **строго по контракту** (12 разделов + полный JSON) и сохраняет через `save_zoom_call_report` — это кладёт `analytical_note` и `raw_json.ai_report.analysis.operational_tasks`;
  4. в Битрикс задачи НЕ создаёт (verification mode).

  **Что приходит в Telegram (формат принципиально короткий).** Никакого пересказа отчёта, никаких разделов «Краткая суть» / «Риски» / «Выводы»:
  ```
  Созвон: <ДД.ММ.ГГГГ> — <тема>

  Предлагаю поставить задачи в Битрикс:
  1. <ФИО> — <task_text>. Срок: <deadline_text>. Критерий: <result_criteria>.
  2. …

  Создаём задачи в Битрикс? Ответь «ставь» — я по каждому ответственному создам одну агрегированную задачу «Итоги созвона <ЧЧ:ММ>» с дедлайном <дата созвона> 19:00 МСК и описанием со списком всех его задач из созвона (формат как в Albery UI). «Не ставь» — пропускаю. «Правки по №<n>: <текст>» — пересоберу конкретную.
  ```
  Если задач 0 — строка «Задач не выделено.» и финальный вопрос не задаётся. Если обработано несколько созвонов — каждый отдельным блоком через разделитель «———».

  Полный отчёт по контракту (краткая суть, риски, поведенческие факторы, диагностика и т.д.) живёт **только в БД** (`analytical_note` + `raw_json.ai_report.analysis`) и доступен через UI Albery / MCP `get_zoom_call_transcript`. В Telegram он не дублируется.

  **На ответ «ставь» (Phase 2 — отправка в Битрикс).** Hermes в Telegram-сессии **НЕ создаёт по одной мелкой задаче через `create_bitrix_task`** — он использует existing UI dispatcher (тот же код, что и кнопка «Отправка задач» в Albery UI):
  1. `list_pending_zoom_operational_dispatches()` без аргументов — возвращает массив `pending` ТОЛЬКО за сегодня (Europe/Moscow). Это именно те созвоны, которые владелец видел в недавней сводке.
  2. Для каждого `pending` → `dispatch_zoom_operational_tasks(call_id, confirm=true)`. Этот инструмент сам сгруппирует `operational_tasks` по ответственному, создаст **одну агрегированную Bitrix-задачу** «Итоги созвона ЧЧ:ММ» на каждого человека с описанием через стандартный intro `Ознакомьтесь со списком выделенных из созвона задач и поставьте себе самые важные в Битрикс…` + нумерованный список этого человека.
  3. После успешной отправки бэкенд помечает `zoom_calls.raw_json.ai_report.bitrix_dispatch` (timestamp + task_ids) → следующий `list_pending` для того же дня уже не вернёт этот созвон.
  4. Hermes отвечает владельцу короткой человеческой сводкой («Создано M задач в Битрикс по созвону <тема>: — ФИО (N задач)»), без техн id, MCP, прочей кухни.

  Правило поведения на «ставь» зафиксировано отдельной AI-инструкцией в Albery («Cron автоматизации/Zoom задачи — ответ ставь», источник в git: [scripts/ai_instruction_zoom_approval.md](scripts/ai_instruction_zoom_approval.md)). Hermes читает её через `start_here_always_read_ai_instructions` в начале Telegram-сессии. Деплой инструкции — `python scripts/upsert_albery_ai_instruction.py "<path>" scripts/ai_instruction_zoom_approval.md`.

  Жёсткие правила в этой AI-инструкции (важны чтобы Hermes не сжигал токены и не делал лишнего):
  - НЕ переспрашивай «3 обязательных поля» — у `dispatch_zoom_operational_tasks` все поля собираются из БД автоматически.
  - НЕ вызывай `create_bitrix_task` для отдельных задач — это создаёт много мелких задач вместо одной агрегированной.
  - НЕ ищи `call_id` вручную через `list_zoom_calls`/`get_zoom_call_transcript` — `list_pending_zoom_operational_dispatches` уже отфильтровал нужное.
  - НЕ читай `get_org_structure`, `get_zoom_call_transcript`, `get_report_contract` — dispatch сам всё подтянет из БД.
  - На «не ставь» — ничего не создавай, отвечай «Понял, задачи не ставлю.».
  - На «правки по №N: …» — `dispatch_zoom_operational_tasks` не вызывай; режим редактирования отдельной задачи пока не автоматизирован, обсуди с владельцем устно.

  **Деплой обновлённого промпта/watchdog'а**:
  ```powershell
  python scripts/update_hermes_zoom_to_tasks.py
  # Опции:
  #   --reset-and-run <zoom_call_uuid>
  #     дополнительно: удалит сохранённый отчёт за этот созвон через MCP
  #     delete_zoom_call_report, сбросит cooldown, и запустит watchdog один раз
  #     через bash (для разработки; в проде используйте `hermes cron run f217482a8618`
  #     если нужна реальная cron-delivery в Telegram).
  ```
  Источники правды в git:
  - [scripts/hermes_zoom_to_tasks_prompt.txt](scripts/hermes_zoom_to_tasks_prompt.txt) — текст промпта (на проде: `/root/.hermes/scripts/hermes_zoom_to_tasks_prompt.txt`);
  - [scripts/hermes_zoom_watchdog.sh](scripts/hermes_zoom_watchdog.sh) — wrapper (на проде: `/root/.hermes/scripts/zoom_watchdog.sh`, права 0700);
  - [scripts/update_hermes_zoom_to_tasks.py](scripts/update_hermes_zoom_to_tasks.py) — патчер.
  - [scripts/ai_instruction_zoom_approval.md](scripts/ai_instruction_zoom_approval.md) — AI-инструкция Albery для Telegram-сессии.
  - [scripts/upsert_albery_ai_instruction.py](scripts/upsert_albery_ai_instruction.py) — общий патчер AI-инструкций через MCP `upsert_ai_instruction`.
  Бэкап предыдущего watchdog'a: `/root/.hermes/scripts/zoom_watchdog.sh.bak`.
- `owner-daily` — `0 18 * * *`: формирует и сохраняет ежедневный отчёт собственнику
  по контракту `get_report_contract(owner_daily)`. **На уровне prompt самого Hermes**
  (не MCP-инструкций — контракт `owner_daily` НЕ трогали) включён двухфазный
  режим согласования рекомендаций → отправка в Битрикс.

  **Фаза 1 — формирование (cron в 18:00 МСК).** Hermes:
  1. читает живые AI-инструкции, контракт `owner_daily`, источники за день (чаты с OCR, Zoom-отчёты, Bitrix-задачи, оргструктуру, регламенты, предыдущий owner-отчёт);
  2. вызывает `save_owner_daily_report` и **дополнительно передаёт** аргумент `manager_messages` — массив адресных сообщений ТОЛЬКО для пятёрки (Сергей Виноградов, Наталья Горюнова, Артур Степанян, Евгений Палей, Александр Никитенко). В каждом `message_text` уже лежит ПОЛНЫЙ финальный текст в формате «<Имя>, приветствую! Рекомендации: 1) … 2) …», который пойдёт в Битрикс одной репликой;
  3. для Евгения Палея (собственник) в начало `message_text` добавляется блок «Главный вывод дня — <2-3 строки executive summary>»;
  4. на стороне MCP `tool_save_owner_daily_report` после INSERT в `owner_daily_reports` вызывает `save_owner_daily_manager_messages` (в `app.py`), который парсит `manager_messages` и пишет каждое сообщение строкой в `owner_manager_recommendations` со `status='new'`;
  5. для **НЕ-разрешённых** сотрудников рекомендации в `manager_messages` НЕ передаются вообще — наблюдения по ним остаются только в `report_text/recommendations` отчёта;
  6. **если для пятёрки за день нет фактов-оснований** (нет созвонов/чатов/ответов/просрочек) — `manager_messages` пустой, в БД ничего не записывается, и Hermes возвращает **пустой stdout** → Telegram молчит вообще (правило тишины, как у `zoom-to-tasks` при отсутствии новых созвонов);
  7. если хоть один блок есть — Hermes собирает единое сообщение и присылает мне в Telegram:
     ```
     Согласуйте что мы отправляем:

     Евгений, приветствую!
     Главный вывод дня — …
     Рекомендации: 1) … 2) …

     ———

     Сергей, приветствую!
     Рекомендации: 1) …

     Отправляю в Битрикс? Ответь: отправляй / не отправляй / правки по <имя>: <текст>.
     ```

  **Фаза 2 — отправка в Битрикс (ответ владельца в Telegram).** На «отправляй»
  Hermes в новой Telegram-сессии:
  1. `list_pending_owner_recommendations(report_date=today)` — читает сохранённые черновики из `owner_manager_recommendations` (статус `new`);
  2. собирает `recipient_recommendations = {manager_bitrix_user_id: recommendation_text}` строго как есть (тексты уже финальные и одобренные);
  3. `send_owner_recommendations_to_bitrix(report_date, recipient_recommendations, confirm=true)` → backend (`send_owner_report_recommendations_to_bitrix` в `app.py`) шлёт каждому через **`im.message.add` от моего имени** (через существующий `BITRIX_WEBHOOK_BASE`, отдельного бота пока не делаем) с fallback на `im.notify.personal.add` при `ERROR_NO_ACCESS`;
  4. факт отправки пишется в `owner_recommendation_dispatches` (channel `bitrix_im` или `bitrix_notification`), статус соответствующих `owner_manager_recommendations` обновляется на `sent`, в `owner_recommendation_events` фиксируется событие `sent`;
  5. на «правки по <имя>: …» — Hermes пересобирает текст для этого человека и снова показывает на согласование (старый черновик можно пометить `cancel_owner_recommendation(recommendation_id, reason)`);
  6. на «не отправляй» — отметка статусов `cancelled`, ничего в Битрикс не уходит.

  Сценарий полностью автоматизированный — отдельного «Альбери Бота» в Bitrix пока нет, сообщения идут от моего пользователя через тот же webhook, что и `create_bitrix_task`. Когда добавим отдельного бота (Phase 2), достаточно будет переключить `send_owner_report_recommendations_to_bitrix` на новый webhook.

### Bitrix-инструменты в MCP

В MCP Albery доступны инструменты:

- `create_bitrix_task` — создаёт одну задачу в Bitrix (разовую или периодическую,
  с наблюдателями опционально). Агент обязан собрать и показать человеку перед
  созданием: ответственный, дедлайн, проверяемый результат, финальный человеческий
  текст задачи, полный список наблюдателей (если есть), и расписание (если
  периодическая). Если чего-то нет — уточнять до конца, не создавать. **Расширен
  28.05.2026** — три новых параметра:
  - `auditor_names: list[str]` / `auditor_bitrix_user_ids: list[int]` — наблюдатели
    из активной оргструктуры (`users.is_active=TRUE`). Резолвинг через новый хелпер
    `_resolve_active_bitrix_users` ([mcp/context_server.py](mcp/context_server.py)):
    fuzzy-match по `_person_names_match`, неоднозначность → отказ + список кандидатов
    с `bitrix_user_id`/`full_name`/`work_position`. Списки объединяются и
    дедуплицируются. На Bitrix уходит как `fields.AUDITORS = [int]`.
  - `periodic: {type, interval?, weekdays?, day_of_month?, daily_mode?, until?}` —
    расписание. Если присутствует, задача создаётся как `IS_REGULAR=Y` +
    `REGULAR_PARAMETERS` (через `_build_bitrix_regular_parameters`). Поддержка:
    - `type="daily"` + `daily_mode="all"|"workdays"` (default `all`) → `REPEAT_TYPE=daily`, `DAILY_MODE`;
    - `type="weekly"` + `weekdays=["MO","WE","FR"]` + `interval` → `REPEAT_TYPE=weekly`, `REPEAT_WEEKDAYS`, `REPEAT_EVERY`;
    - `type="monthly"` + `day_of_month=1-31` + `interval` → `REPEAT_TYPE=monthlydays`, `REPEAT_MONTHDAY`, `REPEAT_EVERY`;
    - опциональный `until="YYYY-MM-DD"` → `REPEAT_TILL`.
    Невалидные комбинации (нет `type`, weekly без `weekdays`, monthly без
    `day_of_month`, кривой weekday-код, `interval<1`, кривой `until`) → инструмент
    отказывает с понятным сообщением.
  Schema-валидация в `inputSchema` дополнительно ограничивает enum'ы. MCP server
  version поднят до `0.6.0`.
- `delete_bitrix_task` — удаляет одну задачу в Bitrix. Жёсткое правило:
  сначала `search_tasks(bitrix_task_id=...)`, затем показать пользователю точную
  задачу (номер, название, статус, ответственный, дедлайн) и спросить подтверждение.
  Только после явного подтверждения можно вызвать
  `delete_bitrix_task(bitrix_task_id=..., confirm=true)`. Нельзя удалять по названию,
  поисковому тексту или неоднозначной ссылке. В инструменте есть дополнительная
  защита: без `confirm=true` он откажет, а `expected_title` можно использовать как
  safety-check от удаления не той задачи.
- `list_pending_owner_recommendations(report_date)` — возвращает сохранённые
  черновики рекомендаций из `owner_manager_recommendations` за указанный день
  (только текущая версия отчёта, только статусы `new/queued`, только записи с
  `manager_bitrix_user_id IS NOT NULL`). Вместе с черновиками отдаёт поля отчёта:
  `report_summary`, `report_dynamics_summary`, `report_risks_summary`,
  `report_text`. Используется Hermes-агентом в Telegram-сессии после ответа
  владельца «отправляй», чтобы собрать `recipient_recommendations` под отправку.
- `send_owner_recommendations_to_bitrix(report_date, recipient_recommendations, confirm=true)`
  — шлёт каждому получателю один личный текст в Битрикс через `im.message.add`
  с fallback на `im.notify.personal.add`. `confirm=true` обязателен — без него
  инструмент отказывает. Параметр `recipient_recommendations` — это словарь
  `{bitrix_user_id: text}`; текст отправляется как есть, без редактирования. Под
  капотом — `send_owner_report_recommendations_to_bitrix` из `app.py`, который
  пишет каждую отправку в `owner_recommendation_dispatches` (channel `bitrix_im`
  / `bitrix_notification`), переводит соответствующие `owner_manager_recommendations`
  в `status='sent'` и пишет `sent`-событие в `owner_recommendation_events`.
- `cancel_owner_recommendation(recommendation_id, reason?)` — помечает одну
  запись `owner_manager_recommendations` как `cancelled`, фиксирует событие
  `cancelled` в `owner_recommendation_events`. Используется на «не отправляй»
  или «правки по <имя>: …» (старый черновик отменяется, новый формируется).
- `save_owner_daily_report` — **расширен 28.05.2026**: теперь принимает поле
  `manager_messages` (массив объектов со структурой
  `{manager_name, manager_bitrix_user_id, priority, message_type, subject, message_text, due, topics}`),
  и после INSERT в `owner_daily_reports` сразу вызывает
  `save_owner_daily_manager_messages` (в `app.py`) — он наполняет
  `owner_manager_recommendations` строкой на каждый объект из `manager_messages`.
  Без `manager_messages` инструмент работает как раньше (только сам отчёт).
- `list_pending_zoom_operational_dispatches(date_from?, date_to?)` — **добавлен 28.05.2026**:
  возвращает Zoom-созвоны с сохранённым `analytical_note`, по которым ещё не
  созданы агрегированные «Итоги созвона» задачи в Битрикс. **Default период —
  только сегодня (Europe/Moscow)** — это страховка от случайной отправки старых
  непереданных созвонов на ответ «ставь»; за прошлые дни нужно передать
  `date_from` явно. Hermes использует это как первый шаг Phase 2 zoom-to-tasks.
- `preview_zoom_operational_tasks(call_id)` — **добавлен 28.05.2026**: показывает
  что будет создано в Битрикс БЕЗ отправки. Возвращает `title` («Итоги созвона
  ЧЧ:ММ»), per-recipient `task_cards` (одна карточка = одна агрегированная
  задача), `deadline` (дата созвона 19:00 МСК), стандартное `description`.
  Полезен для отладки; в обычном flow Hermes этот инструмент не дёргает —
  идёт сразу к `dispatch_zoom_operational_tasks`.
- `dispatch_zoom_operational_tasks(call_id, confirm=true)` — **добавлен
  28.05.2026**: создаёт агрегированные Bitrix-задачи «Итоги созвона ЧЧ:ММ» —
  одна задача на каждого ответственного из `operational_tasks` сохранённого
  отчёта. Дедлайн = call_date 19:00 МСК. Описание = `ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO` +
  список задач этого человека. Внутри использует тот же
  `build_zoom_operational_task_cards` + `dispatch_prepared_zoom_operational_tasks`,
  что и Flask endpoint UI-кнопки «Отправка задач», поэтому формат идентичен.
  После успеха записывает `zoom_calls.raw_json.ai_report.bitrix_dispatch` (timestamp
  + созданные task_ids) — `list_pending_zoom_operational_dispatches` больше его
  не вернёт. Требует `confirm=true`. **НЕЛЬЗЯ заменять на серию
  `create_bitrix_task`** — это создаст много мелких задач вместо одной агрегированной.
- `send_bitrix_message(recipient_bitrix_user_id?, recipient_name?, message_text, confirm)` —
  **добавлен 28.05.2026**: отправляет одно личное сообщение в Bitrix любому
  активному сотруднику от моего пользователя (через `BITRIX_WEBHOOK_BASE`,
  отдельного бота пока нет). Под капотом — `send_bitrix_personal_message` в
  [app.py](app.py): `im.message.add` с fallback на `im.notify.personal.add` при
  `ERROR_NO_ACCESS`. Получатель резолвится через `_resolve_message_recipient` в
  [mcp/context_server.py](mcp/context_server.py) — приоритет у
  `recipient_bitrix_user_id` (точный integer); если передано только
  `recipient_name`, делается fuzzy-match по активным `users` (через
  `_person_names_match`), и при неоднозначности инструмент отказывает и возвращает
  кандидатов. `confirm=true` обязателен — Hermes должен сначала показать владельцу
  итоговый `message_text` + `full_name`/`work_position`/`bitrix_user_id` получателя
  и получить явное «отправляй». `message_text` уходит как есть, без редактирования
  и без подписей. Это **общий** инструмент для сценариев «напиши <имя> в Битрикс:
  …» из Telegram-чата с Hermes или любой ИИ через коннектор — НЕ путать с
  `send_owner_recommendations_to_bitrix` (тот строго привязан к
  `owner_daily_reports.manager_messages` и работает только в Phase 2 owner-daily).
  MCP server version поднят до `0.5.0` (`hermes mcp test albery` должен показывать
  45 инструментов).

Обе cron-задачи доставляют результаты в Telegram:

```text
zoom-to-tasks -> Deliver: telegram:<TG_ID>
owner-daily   -> Deliver: telegram:<TG_ID>
```

Управление и наблюдение (по SSH на сервере):

```bash
hermes cron list                       # список автоматизаций
hermes cron pause/resume/edit <id>     # пауза/правка
hermes chat                            # живой чат с агентом (есть руки Albery MCP)
hermes sessions list                   # история запусков
hermes logs gateway -f                 # лог шлюза
journalctl -u hermes-gateway -f        # системный лог службы
systemctl status hermes-gateway        # статус службы
hermes send --to telegram:<TG_ID> "..." # тестовый пуш в Telegram
```

Telegram-бот принимает команды только от `TG_ID`. Снаружи бот работает через
polling, поэтому отдельный webhook/Nginx не нужен. Для проверки логов:
`tail -f /root/.hermes/logs/gateway.log` и `journalctl -u hermes-gateway -f`.
Если в Telegram снова появятся строки `⚙️ mcp_albery_...`, вернуть тихий режим:

```bash
python3 - <<'PY'
import yaml, pathlib
p = pathlib.Path('/root/.hermes/config.yaml')
cfg = yaml.safe_load(p.read_text()) or {}
cfg.setdefault('display', {}).setdefault('platforms', {}).setdefault('telegram', {})['tool_progress'] = 'off'
cfg.setdefault('cron', {})['wrap_response'] = False
p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
PY
systemctl restart hermes-gateway
```

Чтобы Telegram-агент не сжигал лимит Codex на нерелевантных инструментах, для
платформы `telegram` отключены встроенные toolsets:
`web`, `browser`, `terminal`, `file`, `code_execution`, `vision`, `image_gen`,
`tts`, `skills`, `todo`, `session_search`, `delegation`, `cronjob`,
`computer_use`, `messaging`. Оставлены `memory`, `clarify` и MCP `albery`.
Это важно: бизнес-команды из Telegram должны идти через Albery MCP, а не через
поиск по файлам/терминал. Проверка:

```bash
hermes tools list --platform telegram
```

28.05.2026 была удалена раздутая Telegram-сессия
`20260527_232811_0b70890e`: на команде `Ещё раз попробуй` агент сделал 25 API
вызовов, разогнал контекст до 100k+ токенов и полез в `search_files/read_file`.
Если такое повторится: `systemctl restart hermes-gateway`, затем
`hermes sessions list` и `hermes sessions delete --yes <session_id>`.

Чтобы Telegram больше не тянул бесконечную историю и не сжигал 5-часовой лимит
Codex, но при этом не забывал активный рабочий диалог, на сервере включён режим
«час простоя = новый чат, активный диалог = сжатие»:

```yaml
session_reset:
  mode: both
  idle_minutes: 60
  at_hour: 4
  notify: false
agent:
  gateway_auto_continue_freshness: 300
compression:
  enabled: true
  threshold: 0.20
  target_ratio: 0.12
  protect_first_n: 2
  protect_last_n: 12
  hygiene_hard_message_limit: 50
sessions:
  auto_prune: true
  retention_days: 3
  vacuum_after_prune: true
telegram_context_guard:
  enabled: true
  token_threshold: 100000
  message_limit: 50
```

Смысл настройки:

- если в Telegram нет активности 60 минут, следующий запрос стартует как новый
  чат — это защита от случайного подтягивания огромной истории;
- пользователь может вручную начать новый чат командой `/new` или `/reset`;
- каждый день в 04:00 gateway дополнительно сбрасывает активные сессии, чтобы
  они не жили неделями;
- если контекст растёт в рамках активного диалога, Hermes рано сжимает старую часть в summary
  и оставляет свежий хвост из последних 12 сообщений;
- дополнительно на проде установлен Telegram context guard в
  `/usr/local/lib/hermes-agent/gateway/run.py` и
  `/usr/local/lib/hermes-agent/gateway/platforms/telegram.py`: если перед
  запуском модели в Telegram-сессии примерно `100000+` токенов или `50+`
  сообщений, gateway **не запускает модель**, а присылает предупреждение с
  кнопками `Да, сжать и продолжить` / `Нет, продолжить так`. При выборе
  `Да` сначала выполняется `/compress`, затем повторяется исходное сообщение.
  При выборе `Нет` исходное сообщение запускается без сжатия, но это уже
  осознанное решение владельца;
- старые transcript-сессии чистятся через 3 дня;
- прошлые сессии физически остаются в SQLite, но **не подтягиваются
  автоматически** в Telegram: авто-поиск по старым диалогам отключён, потому что
  он может найти нерелевантное и снова сжечь лимит. Если после reset нужна
  связь с прошлой темой, лучше коротко напомнить контекст или заранее сохранить
  важное через `запомни: ...`;
- постоянные предпочтения нужно хранить не в длинной переписке, а в `memory`
  или в явных инструкциях cron/Albery. Пользователь может писать боту:
  `запомни: ...`, но важные правила для отчётов лучше фиксировать в
  `Настройки → Инструкции для ИИ` или в этом `agent.md`.

Веб-дашборд слушает `127.0.0.1:9119` (наружу не открыт). Смотреть через SSH-туннель:

```bash
ssh -L 9119:127.0.0.1:9119 root@186.246.7.32
# затем на ПК открыть http://localhost:9119  (нужен запущенный `hermes dashboard`)
```

«Обучение»: поведение отчётов/задач правится в Albery (`Настройки → Инструкции
для ИИ` и `Сводная аналитика → Настройка промтов`: контракты `zoom_processing`,
`owner_daily`) — это читают и Hermes, и внешние ассистенты. Уровень Hermes —
`hermes memory` и тексты cron (`hermes cron edit <id> --prompt "..."`).

Важно: на ПК (локальный WSL) остался свой Hermes с теми же cron-задачами, но без
запущенного gateway — он не сработает. Не запускать оба планировщика против одного
Albery (иначе версионная чехарда в отчётах). На тарифе Plus возможны upstream
`usage limit` на тяжёлых прогонах — при необходимости перейти на API-ключ.

### Cron script timeout (увеличен 28.05.2026 до 900s)

По умолчанию Hermes убивает `--script` cron-задачу через **120 секунд**. Для
`zoom-to-tasks` этого мало: когда `zoom_watchdog.sh` находит новый созвон и
запускает `hermes -z` с тяжёлым промптом (полный транскрипт + AI-генерация),
сессия идёт 3-7 минут — раньше cron падал с
`Script timed out after 120s: /root/.hermes/scripts/zoom_watchdog.sh`.

Лимит вынесен в `/root/.hermes/config.yaml`:

```yaml
cron:
  wrap_response: false
  max_parallel_jobs: null
  script_timeout_seconds: 900
```

Резолвится в порядке: env `HERMES_CRON_SCRIPT_TIMEOUT` → `cron.script_timeout_seconds`
в config.yaml → `120` (default). После изменения — `systemctl restart hermes-gateway`.
Бэкап старого конфига перед правкой: `/root/.hermes/config.yaml.bak.<unix_ts>`.

### Деплой Hermes-промптов

Промпт cron-задачи `owner-daily` живёт в **двух местах**:

1. **Источник правды (в git):** [scripts/hermes_owner_daily_prompt.txt](scripts/hermes_owner_daily_prompt.txt)
2. **Применённый на проде:** поле `prompt` у джобы `owner-daily` в
   `/root/.hermes/cron/jobs.json`.

Применение/обновление промпта на проде:

```powershell
python scripts/update_hermes_owner_daily_prompt.py
```

Скрипт:
- читает `scripts/hermes_owner_daily_prompt.txt`;
- через paramiko заходит на прод (пароль из локального `.env`, не светится в логе);
- сохраняет бэкап `/root/.hermes/cron/jobs.json.bak`;
- патчит `prompt` у джобы `owner-daily` и кладёт обратно (chmod 600);
- делает `systemctl restart hermes-gateway`.

Откат — переименовать `jobs.json.bak` обратно в `jobs.json` и рестартануть gateway.

### Правило: после любых изменений в Hermes / MCP — рестартить gateway и писать дальнейшие шаги владельцу

**Обязательное правило для будущих изменений.** `update_server.sh` перезапускает только `albery.service` (там живёт MCP HTTP-сервер). Но **`hermes-gateway.service` отдельный процесс**, который кэширует список MCP-инструментов на старте + Telegram-сессии дополнительно кэшируют toolset на старте сессии. Поэтому если ничего больше не делать — Hermes продолжит видеть старый список, и активный Telegram-чат тоже.

Триггеры, после которых **обязателен** рестарт `hermes-gateway`:
- добавлен/удалён/переименован MCP-инструмент в [mcp/context_server.py](mcp/context_server.py);
- изменена `inputSchema` или `description` существующего MCP-инструмента (Hermes их кэширует);
- изменён `/root/.hermes/config.yaml` (`display`, `cron`, `session_reset`, `compression`, `telegram_context_guard`, toolsets платформы);
- изменён `/root/.hermes/cron/jobs.json` (промпт, расписание, deliver) — `update_hermes_*_prompt.py` это уже делают сами;
- изменён код Hermes в `/usr/local/lib/hermes-agent/` (патчи gateway/telegram.py и т.п.).

Команда (рестарт пары):

```bash
ssh root@186.246.7.32 'systemctl restart albery hermes-gateway && systemctl is-active albery hermes-gateway'
```

Проверка, что Hermes увидел новый toolset:

```bash
ssh root@186.246.7.32 'hermes mcp test albery 2>&1 | grep -E "Tools discovered|<new_tool_name>"'
```

**После рестарта — обязательно сообщить владельцу дальнейшие шаги в чате.** Минимум: «в Telegram-чате с Hermes напиши `/reset` (или `/new`), чтобы сессия подтянула новый список инструментов». Без `/reset` активная Telegram-сессия может продолжать видеть старый toolset до тех пор, пока её не сбросит idle-таймер (60 мин) или 04:00-ресет ([agent.md:1555-1568](agent.md#L1555-L1568)). Если изменение требует ещё чего-то от владельца (например, повторить cron-команду в чате, или одобрить рассылку — указать конкретное действие).

Признак, что toolset устарел: Hermes отказывает с описанием **другого** инструмента (видел 2026-05-28 после деплоя `send_bitrix_message` — Hermes сослался на `send_owner_recommendations_to_bitrix`, потому что в кэше сессии нового инструмента ещё не было).

### Известный баг: 120s таймаут `create_bitrix_task` / `delete_bitrix_task` (исправлено 28.05.2026)

**Симптом.** Hermes в Telegram отвечал: «Не удалось создать задачу» или «MCP call timed out after 120.0s». В журнале `journalctl -u hermes-gateway`:

```
ERROR tools.mcp_tool: MCP tool albery/create_bitrix_task call failed:
MCP call timed out after 120.0s (configured timeout: 120.0s)
```

**Корневая причина.** В Albery жили **две разные реализации Bitrix-клиента**:
- `BitrixClient` в [app.py:14246](app.py#L14246) — на `requests.Session`, кэш, retry. **Быстрый**, < 2s. Используют `send_bitrix_message`, `dispatch_zoom_operational_tasks`, `send_owner_recommendations_to_bitrix`.
- `_bitrix_call_with_fallback` в [mcp/context_server.py](mcp/context_server.py) — на голом `urllib.request.urlopen(timeout=60)`. **Зависал ровно 120с** (60 × 2 fallback URL). Использовали `create_bitrix_task`, `delete_bitrix_task`.

Hermes-side MCP-клиент имеет свой ceiling 120s → urllib доходил до 120s раньше, чем что-либо ответил → Hermes ловил `MCP call timed out`, а никакая задача в Bitrix не создавалась.

Дополнительно у `delete_bitrix_task` был **SQL-баг**: JOIN на несуществующую колонку `bitrix_tasks.responsible_user_id` (на самом деле колонка зовётся `responsible_id`) → каждый вызов падал с `psycopg.errors.UndefinedColumn` ещё до обращения в Bitrix.

**Фикс.**
1. В [app.py](app.py) добавлен workflow `bitrix_method_call(method, payload, prefer_api=True)` — тонкая обёртка над `BitrixClient.call_with_fallback`. Доступен через `app_workflow_function("bitrix_method_call")`.
2. `_bitrix_call_with_fallback` в [mcp/context_server.py](mcp/context_server.py) свёрнут до 8 строк — делегирует в `bitrix_method_call`. Голый urllib выпилен.
3. SQL в `tool_delete_bitrix_task` поправлен: `responsible_user_id` → `responsible_id`.

После фикса замеры на проде (с реальным Bitrix через VPN-Эстонию):
- Разовая задача: **1.02s**
- Задача с наблюдателями (1 человек): **0.67s**
- Периодическая weekly с `IS_REGULAR=Y`: **0.72s** (Bitrix-портал принимает `REGULAR_PARAMETERS` ровно в нашем формате)

Версия MCP поднята до `0.6.1`. Коммиты: `db0c4e0` (urllib → BitrixClient), `90109e1` (SQL fix).

**Правило на будущее.** Любой новый MCP-инструмент, дёргающий Bitrix API, обязан идти через `app_workflow_function("bitrix_method_call")` (или прямо `BitrixClient` в самом app.py). Не делать собственные urllib-обёртки — это уже один раз привело к 120с таймауту в проде.

### Известная гран. ситуация: time-зоны в title «Итоги созвона ЧЧ:ММ»

В Postgres колонка `zoom_calls.start_time_msk` фактически хранится как UTC
(`timestamptz`), несмотря на название с `_msk`. Конверсия в МСК делается на
runtime через `astimezone(MSK_TZ)`. Это работает корректно в happy-path
(`zoom_call_row_payload` precomputed-ит `time_text` как `"09:28 - 10:26"` уже
в МСК). Но **fallback-пути** (когда `time_text` пустой) когда-то брали `str(start_time_msk)` напрямую — выдавали
ISO с `+00:00` и в title вылезало UTC время (например `"Итоги созвона 06:28"`
вместо МСК `09:28`).

Зафиксировано 28.05.2026:
- backend `build_zoom_operational_tasks_dispatch` ([app.py:3601-3611](app.py#L3601-L3611)) теперь конвертит fallback через `parse_datetime(...).astimezone(MSK_TZ).strftime('%H:%M')`;
- frontend `buildLocalZoomOperationalPreview` ([Интерфейс/src/App.tsx:4110-4124](Интерфейс/src/App.tsx#L4110-L4124)) использует `Date.toLocaleTimeString({timeZone: 'Europe/Moscow'})`.

Если в будущем появится новый код, формирующий «Итоги созвона …» из ISO-строки — обязательно конвертировать в МСК. Прямой использовать `str(call.start_time_msk)` нельзя.

### Дорожная карта: внедрение Hermes в компанию + RBAC по ролям (план, не выполнено)

Обсуждено 28.05.2026. Сейчас Hermes на проде — **один инстанс, один Telegram-бот, один MCP-секрет**, `TELEGRAM_ALLOWED_USERS=<TG_ID владельца>`. Это работает для владельца, но **не масштабируется** на менеджеров/сотрудников.

**Ограничение Hermes:** `hermes tools list --platform telegram` задаёт toolset на **всю платформу**, не на отдельного TG-пользователя. Внутри одного бота сделать «владельцу всё, менеджеру половину, сотруднику только read» **нельзя нативно** — только soft-RBAC в AI-инструкции, который модель может «забыть» после `/compress`. Для Bitrix-write это недопустимо.

**Целевая архитектура (3 тира, расширение существующего FAQ MCP):**

```
Albery backend (один)
├── /mcp/<MCP_SHARED_SECRET>         → 45 tools (full)          → hermes-owner   (твой TG)
├── /mcp-manager/<MCP_MANAGER_SECRET> → ~20 tools (read + своя
│                                       зона, без im.message,
│                                       без dispatch, без delete) → hermes-manager (5 TG)
└── /mcp-faq/<MCP_FAQ_SHARED_SECRET> → 12 tools (read-only,
                                       уже работает)             → hermes-staff   (все TG)
```

Каждый Hermes — отдельный systemd-юнит в `/root/.hermes-<role>/` со своим `auth.json`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, своей `state.db`/памятью. Cron-джобы (`zoom-to-tasks`, `owner-daily`) остаются **только у owner-инстанса**.

**Цена:** RAM ~250 МБ × 3 = 750 МБ (на 2 ГБ + swap 2 ГБ — тянет). Codex-лимит делится поинстансно → лучше отдельные ChatGPT Plus подписки на инстанс с реальной нагрузкой. Иначе все три инстанса будут конкурировать за один 5-часовой лимит.

**Шаги внедрения (в этом порядке):**

1. **Расширить MCP до manager-эндпоинта.** В [mcp/context_server.py](mcp/context_server.py) добавить третью точку входа `/mcp-manager/<secret>` рядом с `/mcp/` и `/mcp-faq/` (по аналогии с тем, как сделан FAQ — см. [agent.md:608-626](agent.md#L608-L626)). Toolset для менеджера: `search_tasks`, `get_task_comments`, `search_zoom_transcripts`, `get_zoom_call_transcript`, `search_company_knowledge`, `get_company_file`, `list_chats`, `search_messages`, `get_chat_transcript`, `get_org_structure`, `get_context_guide`, `start_here_always_read_ai_instructions`, `health`, `list_available_sources`. **Не давать** менеджеру: `create_bitrix_task`, `delete_bitrix_task`, `dispatch_zoom_operational_tasks`, `send_bitrix_message`, `send_owner_recommendations_to_bitrix`, `save_*_report`, `delete_zoom_call_report`, `upsert_ai_instruction`, `process_chat_ocr`, `cancel_owner_recommendation`.
2. **Поднять `hermes-manager` инстанс.** Отдельный `/root/.hermes-manager/`, отдельный бот в BotFather, `TELEGRAM_ALLOWED_USERS` = TG-id 1-2 менеджеров для пилота. Без cron. Прицепить только `/mcp-manager/`. Свой `auth.json`, желательно отдельный ChatGPT-аккаунт.
3. **Обкатать 1-2 недели** на пилотной паре менеджеров. AI-инструкция для manager-MCP — отдельный документ в Albery «Hermes для менеджера: что можно делать», читается через `start_here_always_read_ai_instructions` каждой сессией.
4. **Если зайдёт — открыть `hermes-staff`** через уже существующий `/mcp-faq/` (12 tools, регламенты + Zoom-расшифровки + оргструктура). Третий бот, ширим на всех сотрудников.

**Чего НЕ делать:** не давать сотрудникам MCP-секрет напрямую (только через серверного Hermes-бота); не публиковать секреты в клиентском коде; не запускать второй планировщик cron-джоб против того же Albery (версионная чехарда отчётов — см. [agent.md:1621-1624](agent.md#L1621-L1624)).

**Альтернатива «попроще» (1 инстанс + soft-RBAC):** оставить один Hermes, в AI-инструкции вшить «если `sender_tg_id ∈ {X, Y}` — нельзя `dispatch_*`, `send_bitrix_message`, `delete_bitrix_task`, `send_owner_recommendations_to_bitrix`». Дёшево, но **это не безопасность** — модель может проигнорировать после сжатия контекста. Годится для UX-разделения «не показывай менеджеру owner-команды», не для защиты от злого умысла.

### Обучение Hermes (где править поведение, по убыванию силы)

| Уровень | Источник правды | Кто читает | Деплой |
|---|---|---|---|
| Контракт отчёта (структура секций + JSON-схема) | Albery UI → `Сводная аналитика → Настройка промтов` (`zoom_processing`, `owner_daily`) | Все — Hermes, внешние ассистенты | Сохранение в UI = live |
| AI-инструкции (правила поведения) | Albery UI → `Настройки → Инструкции для ИИ` + git: [scripts/ai_instruction_zoom_approval.md](scripts/ai_instruction_zoom_approval.md) | Hermes через `start_here_always_read_ai_instructions` в начале сессии | `python scripts/upsert_albery_ai_instruction.py <path> <file>` |
| Cron-промпт (что делает одна джоба) | git: [scripts/hermes_zoom_to_tasks_prompt.txt](scripts/hermes_zoom_to_tasks_prompt.txt), [scripts/hermes_owner_daily_prompt.txt](scripts/hermes_owner_daily_prompt.txt) | Только эта cron-джоба | `python scripts/update_hermes_*_prompt.py` → restart gateway |
| Память Hermes (личные предпочтения) | `/root/.hermes/state.db` через `hermes memory` или «запомни: …» в Telegram | Hermes везде, в рамках инстанса | Live |

**Правило большого пальца:** что должно быть у всех (формат отчёта, состав пятёрки, что считать срочным) → **контракт/AI-инструкции в Albery**. Что специфично для одной cron-джобы (cooldown, формат Telegram-сводки) → **cron-промпт в git**. Личные предпочтения владельца → **`hermes memory`**.

**Болевая точка** из ретро [agent.md:1545-1549](agent.md#L1545-L1549): обучать **жёсткими запретами** в AI-инструкции, а не уговорами. Пример из [scripts/ai_instruction_zoom_approval.md](scripts/ai_instruction_zoom_approval.md): «НЕ вызывай `create_bitrix_task` / НЕ читай `get_org_structure`» — это спасло Codex-лимит. Без таких запретов Hermes идёт в `search_files` и сжигает 100k токенов.
