# Albery Server Context

Этот файл фиксирует рабочий контекст проекта, чтобы в новом чате сразу было понятно, где что лежит и какими командами обслуживать сервер.

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
