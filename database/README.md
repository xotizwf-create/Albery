# PostgreSQL schema

This folder contains the approved PostgreSQL v1 schema for the employee analytics system.

## Files

- `postgres_schema_v1.sql` - production schema with tables, indexes, triggers, views, and prompt categories.

## Apply schema

Set `DATABASE_URL` in `.env`:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/employee_analytics
```

Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Validate script input without connecting:

```powershell
.\.venv\Scripts\python.exe .\scripts\init_postgres.py --dry-run
```

Apply schema:

```powershell
.\.venv\Scripts\python.exe .\scripts\init_postgres.py --create-db
```

The Flask runtime is PostgreSQL-only. `DATABASE_URL` is required; local SQLite databases are not used.
