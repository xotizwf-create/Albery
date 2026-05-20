# Bitrix24 Weekly Export (Mini App)

Local web app that:
- lets you pick a week in UI (`Monday -> Sunday`);
- pulls Bitrix24 tasks created in this week;
- builds weekly analytics (`total`, `completed`, `overdue`);
- saves full result into JSON and lets you download it.

## PostgreSQL database

The approved PostgreSQL v1 schema is in:

```text
database/postgres_schema_v1.sql
```

Set `DATABASE_URL` in `.env`, then run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\scripts\init_postgres.py --dry-run
.\.venv\Scripts\python.exe .\scripts\init_postgres.py --create-db
```

The Flask runtime is PostgreSQL-only. `DATABASE_URL` is required; local SQLite databases are not used.

## 1) Bitrix webhook
Create incoming webhook in Bitrix24 and grant scopes:
- `task`
- `user`
- `im`
- `disk`
- `department` (optional but recommended)

Put webhook base into `.env`:

```env
BITRIX_WEBHOOK_BASE=https://yourcompany.bitrix24.ru/rest/1/xxxxxxxxxxxx
FLASK_SECRET_KEY=replace-with-random-string
BITRIX_EXPORT_MODE=audit
BITRIX_REQUEST_DELAY=0.05
BITRIX_LOOKBACK_DAYS=30
```

## 2) Run app (Windows PowerShell)

```powershell
cd "G:\OneDrive\Рабочий стол\Мои проекты\Евгений. Разработка"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# edit .env and paste your real webhook URL
python app.py
```

Open browser:

```text
http://127.0.0.1:5000
```

## 3) How to use
1. Pick week in the UI.
2. Click `Build JSON + Analytics`.
3. Download JSON with `Download JSON`.

Exported files are stored in `exports/`.

## Notes
- Filtering is by `CREATED_DATE` in the selected week.
- `BITRIX_EXPORT_MODE=fast` builds the report quickly from task list data and user profiles.
- `BITRIX_EXPORT_MODE=audit` loads full task details, comments, checklist and available history for tasks in the selected period. Use this for AI analysis.
- `BITRIX_EXPORT_MODE=full` is reserved for the same deep export behavior and can take longer on large periods.
- Audit mode uses Bitrix `batch` requests (up to 50 commands per request) to reduce API calls and avoid rate limits.
- `BITRIX_LOOKBACK_DAYS=30` limits how far back the scanner goes before the requested week. Increase it if you need very old open tasks in fresh weekly reports.
- The app tries both endpoint styles:
  - `/rest/{user}/{token}/method`
  - `/rest/api/{user}/{token}/method`
- The app auto-detects webhook scope:
  - `tasks` (REST v3) — uses `tasks.task.*` via `/rest/api/`
  - `task` (REST v2/legacy) — uses legacy-compatible methods
- If `tasks.task.list` is unavailable on your portal, the app auto-falls back to legacy list methods (`task.item.list` / `task.ctasks.getlist`).
- If some method is unavailable for your Bitrix plan or rights, JSON still includes task data and stores method error for that section.

## One-command startup

Windows PowerShell:

```powershell
.\start.ps1
```

What it does:
- creates `.venv` if needed;
- installs Python dependencies;
- creates and initializes PostgreSQL if needed;
- installs frontend dependencies if needed;
- builds `Интерфейс/dist`;
- starts Flask on `http://127.0.0.1:5001`.

Fast restart after dependencies are installed:

```powershell
.\start.ps1 -SkipInstall
```

Custom backend port:

```powershell
.\start.ps1 -Port 5000
```
