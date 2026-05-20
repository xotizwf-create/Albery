from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SERVER_NAME = "employee-analytics-context"
SERVER_VERSION = "0.2.1"
PROTOCOL_VERSION = "2024-11-05"
MAX_LIMIT = 500
TOOL_USAGE_CONTRACT = (
    "MANDATORY: before using this tool for company work, call "
    "start_here_always_read_ai_instructions. If the user request is vague, "
    "ambiguous, or missing date/period/chat/person/report type/source/output "
    "scope, ask one concise clarifying question first instead of guessing. "
)


class McpError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if value:
        return normalize_postgres_url(value)

    if ENV_PATH.exists():
        for raw_line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            if key.strip() == "DATABASE_URL":
                return normalize_postgres_url(raw_value.strip().strip('"').strip("'"))

    raise McpError(-32000, "DATABASE_URL is not set in environment or .env")


def normalize_postgres_url(database_url: str) -> str:
    normalized = database_url.strip()
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if normalized.startswith(prefix):
            return "postgresql://" + normalized[len(prefix):]
    return normalized


def connect() -> psycopg.Connection:
    return psycopg.connect(load_database_url(), row_factory=dict_row)


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def text_response(payload: Any) -> dict[str, Any]:
    structured = json_safe(payload)
    return {
        "structuredContent": structured,
        "content": [
            {
                "type": "text",
                "text": json.dumps(structured, ensure_ascii=False, default=json_default, indent=2),
            }
        ]
    }


def local_app_base_url() -> str:
    value = os.getenv("MCP_LOCAL_APP_BASE_URL", "").strip() or os.getenv("APP_BASE_URL", "").strip()
    return value.rstrip("/") if value else "http://127.0.0.1:5002"


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    return value


def parse_date_arg(args: dict[str, Any], name: str, required: bool = True) -> date | None:
    raw = args.get(name)
    if raw in (None, ""):
        if required:
            raise McpError(-32602, f"Missing required argument: {name}")
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise McpError(-32602, f"{name} must use YYYY-MM-DD format") from exc


def parse_limit(args: dict[str, Any], default: int = 100) -> int:
    raw = args.get("limit", default)
    try:
        limit = int(raw)
    except (TypeError, ValueError) as exc:
        raise McpError(-32602, "limit must be an integer") from exc
    return max(1, min(limit, MAX_LIMIT))


def parse_offset(args: dict[str, Any]) -> int:
    raw = args.get("offset", 0)
    try:
        offset = int(raw)
    except (TypeError, ValueError) as exc:
        raise McpError(-32602, "offset must be an integer") from exc
    return max(0, offset)


def is_ocr_supported_file(file_name: Any, file_type: Any, mime_type: Any) -> bool:
    markers = [file_type, mime_type, file_name]
    for marker in markers:
        value = str(marker or "").lower().strip()
        if not value:
            continue
        if value == "image" or value.startswith("image/"):
            return True
        if value == "pdf" or value == "application/pdf" or value.endswith(".pdf"):
            return True
        if value.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")):
            return True
    return False


def safe_table_exists(cur: Any, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (f"public.{table_name}",))
    return bool(cur.fetchone()["exists"])


def tool_health(_: dict[str, Any]) -> dict[str, Any]:
    url = load_database_url()
    parsed = urlparse(url)
    database_name = parsed.path.lstrip("/")
    safe_url = urlunparse((parsed.scheme, parsed.netloc.split("@")[-1], parsed.path, "", "", ""))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT now() AS server_time, current_database() AS database")
            row = cur.fetchone()
    return {
        "status": "ok",
        "server": SERVER_NAME,
        "database": row["database"] or database_name,
        "database_url": safe_url,
        "server_time": row["server_time"],
    }


def tool_list_available_sources(_: dict[str, Any]) -> dict[str, Any]:
    table_names = [
        "users",
        "departments",
        "user_departments",
        "bitrix_tasks",
        "chats",
        "chat_messages",
        "chat_message_files",
        "chat_file_ocr",
        "chat_daily_reports",
        "chat_weekly_reports",
        "owner_daily_reports",
        "owner_weekly_reports",
        "company_profile",
        "company_folders",
        "company_drive_sources",
        "ai_instruction_folders",
        "zoom_calls",
        "zoom_call_participants",
        "zoom_call_transcript_segments",
    ]
    with connect() as conn:
        with conn.cursor() as cur:
            sources: dict[str, dict[str, Any]] = {}
            for table in table_names:
                exists = safe_table_exists(cur, table)
                count = None
                if exists:
                    cur.execute(f"SELECT count(*) AS count FROM {table}")
                    count = cur.fetchone()["count"]
                sources[table] = {"exists": exists, "rows": count}
    return {"sources": sources}


def load_ai_instructions() -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            if not safe_table_exists(cur, "ai_instruction_folders"):
                return []
            cur.execute(
                """
                WITH RECURSIVE folder_tree AS (
                    SELECT id, parent_id, name, content, sort_order, ARRAY[name]::text[] AS path, updated_at
                    FROM ai_instruction_folders
                    WHERE parent_id IS NULL
                    UNION ALL
                    SELECT child.id, child.parent_id, child.name, child.content, child.sort_order,
                           folder_tree.path || child.name, child.updated_at
                    FROM ai_instruction_folders child
                    JOIN folder_tree ON folder_tree.id = child.parent_id
                )
                SELECT id, parent_id, name, array_to_string(path, ' / ') AS path, content, updated_at
                FROM folder_tree
                ORDER BY path
                """
            )
            return cur.fetchall()


def tool_get_context_guide(_: dict[str, Any]) -> dict[str, Any]:
    ai_instructions = load_ai_instructions()
    return {
        "purpose": "Navigation guide for using this MCP server systematically instead of guessing where data lives.",
        "live_ai_instructions": ai_instructions,
        "operating_rules": [
            "Before any substantive answer or data work, call start_here_always_read_ai_instructions and follow its live instructions exactly.",
            "For unfamiliar questions, call get_context_guide after start_here_always_read_ai_instructions, then list_available_sources if source freshness or row counts matter.",
            "Act as an internal company AI agent: answers must be based on company context, regulations, reports, Bitrix tasks, chats, Zoom, and live AI instructions, not generic advice.",
            "For company rules, regulations, document mirrors, and persistent business knowledge, use search_company_knowledge first.",
            "For recommendations, management advice, or owner-facing conclusions, read recent owner daily/weekly reports and relevant chat daily/weekly reports before concluding what is done, open, overdue, or repeated.",
            "If the request is vague, ambiguous, underspecified, or can be interpreted in several ways, stop and ask a short clarifying question before using data tools or answering.",
            "Ask what exact date/period, chat, person, task, source, output format, target decision, or save/write action is needed. Do not guess missing scope.",
            "Always use concrete names and task titles. Do not write only 'task 318099' or 'Natalia task'; write 'task 318099: Сформировать реестр платежей' with responsible person, status, deadline, and source when available.",
            "For employee identity, managers, departments, and Bitrix user ids, use get_org_structure before person-specific filters.",
            "For a date period, call get_period_index before reading messages/tasks; it shows where data exists and which chats are active.",
            "For task status, ownership, deadlines, and Bitrix work items, use search_tasks.",
            "For discussion evidence, commitments, blockers, decisions, and OCR from chat images, use list_chats then search_messages or get_chat_transcript.",
            "For meeting evidence, use list_zoom_calls first, then search_zoom_transcripts with topic keywords, then get_zoom_call_transcript for matching calls, and get_org_structure before generating a Zoom report.",
            "For cross-source reports over a bounded date range, use get_compact_export, then deepen with source-specific tools.",
            "Prefer narrow queries with date ranges, dialog_id, responsible_bitrix_user_id, or search text. Page with offset instead of requesting everything.",
            "Do not treat absence of one search result as proof until the relevant source index/count was checked.",
            "When evidence is incomplete, separate confirmed facts from assumptions and ask the user for the missing input needed to continue.",
        ],
        "source_map": {
            "company_knowledge": {
                "tools": ["search_company_knowledge", "list_company_files", "get_company_file", "get_company_profile"],
                "tables": ["company_folders", "company_drive_sources", "company_profile"],
                "use_for": ["company rules", "regulations", "Google Drive mirrored documents", "static process knowledge"],
            },
            "organization": {
                "tools": ["get_org_structure"],
                "tables": ["users", "departments", "user_departments"],
                "use_for": ["people", "roles", "managers", "departments", "Bitrix user ids"],
            },
            "bitrix_tasks": {
                "tools": ["search_tasks"],
                "tables": ["bitrix_tasks", "bitrix_task_members", "bitrix_task_snapshots"],
                "use_for": ["task ownership", "deadlines", "statuses", "overdue work", "responsibility"],
            },
            "bitrix_chats": {
                "tools": ["list_chats", "search_messages", "get_chat_transcript", "get_chat_ocr_status", "process_chat_ocr", "get_chat_daily_report", "save_chat_daily_report", "get_chat_weekly_report", "save_chat_weekly_report"],
                "tables": ["chats", "chat_messages", "chat_message_files", "chat_file_ocr", "chat_daily_reports", "chat_weekly_reports", "chat_report_items"],
                "use_for": ["conversation evidence", "commitments", "decisions", "questions", "OCR from screenshots", "daily and weekly chat report storage"],
            },
            "owner_reports": {
                "tools": ["get_previous_owner_daily_context", "get_owner_reports", "save_owner_daily_report", "save_owner_weekly_report"],
                "tables": ["owner_daily_reports", "owner_weekly_reports"],
                "use_for": ["recent owner context", "general daily reports for owner", "general weekly reports for owner", "owner-level report storage", "recommendation continuity"],
            },
            "zoom_calls": {
                "tools": ["list_zoom_calls", "get_zoom_call_transcript", "search_zoom_transcripts", "save_zoom_call_report", "delete_zoom_call_report"],
                "tables": ["zoom_calls", "zoom_call_participants", "zoom_call_transcript_segments"],
                "use_for": ["meeting transcripts", "call participants from Zoom API", "mentioned people in transcript", "spoken decisions", "facts of task execution", "standalone Zoom report storage"],
                "rules": [
                    "For standalone Zoom reports, always include factual participants, mentioned people, a strict task block with owner/deadline/success-criteria gaps, and behavioral factors.",
                    "For daily chat reports, Zoom relevance is based on transcript content, participants, and topics from chat/OCR/tasks, not only call title.",
                    "If the report date has exactly one Zoom call, read its transcript before saying there are no relevant Zoom facts.",
                    "Search transcript keywords extracted from OCR/tasks/risks, for example payment calendar, motivation, margins, project names, overdue work, Bitrix, and owner names.",
                ],
            },
        },
        "recommended_workflows": {
            "company_rule_question": ["search_company_knowledge(query)", "list_company_files() to inspect all documents", "get_company_file(folder_id/google_file_id) to read full content", "get_company_profile() if full tree is needed"],
            "employee_period_question": [
                "get_org_structure(include_inactive=false)",
                "get_owner_reports(report_kind='daily', limit=7) and get_owner_reports(report_kind='weekly', limit=4) if the answer includes recommendations or management conclusions",
                "get_period_index(date_from,date_to)",
                "search_tasks(date_from,date_to,responsible_bitrix_user_id)",
                "search_messages(date_from,date_to,query/person name)",
                "search_zoom_transcripts(date_from,date_to,query/person name)",
            ],
            "chat_event_question": ["list_chats(date_from,date_to,query)", "get_chat_transcript(dialog_id,date_from,date_to)"],
            "recommendation_answer": [
                "get_ai_instructions()",
                "search_company_knowledge(query) for company rules and regulations",
                "get_owner_reports(report_kind='daily', limit=7)",
                "get_owner_reports(report_kind='weekly', limit=4)",
                "get_period_index(date_from,date_to) for the requested period",
                "search_tasks/search_messages/search_zoom_transcripts for concrete evidence",
                "answer with specific task titles, owners, statuses, deadlines, and sources; ask clarifying questions when evidence is missing",
            ],
            "daily_chat_report_creation": [
                "get_report_contract(category_key='chat_analysis') and use the active report contract exactly",
                "get_ai_instructions() and read report-creation instructions",
                "list_chats(date_from=report_date,date_to=report_date)",
                "get_chat_ocr_status(dialog_id,report_date); if OCR is missing for images/PDF, call process_chat_ocr(dialog_id,date_from=report_date,date_to=report_date)",
                "get_chat_transcript(dialog_id,report_date,report_date,include_ocr=true)",
                "list_zoom_calls(date_from=report_date,date_to=report_date)",
                "ensure every relevant or same-day Zoom call has a standalone Zoom report first; if analytical_note is empty, create/save the Zoom report before chat_analysis",
                "get_chat_daily_report(dialog_id, previous_date)",
                "save_chat_daily_report(dialog_id, report_date, analysis) after the MCP agent has generated the report itself using the active chat_analysis report contract and source data",
            ],
            "chat_weekly_report_creation": [
                "get_report_contract(category_key='chat_weekly_report') and use the active report contract exactly",
                "get_ai_instructions() and read weekly chat report instructions",
                "get_chat_daily_report(dialog_id, each report date) or generate missing daily reports first",
                "get_chat_weekly_report(dialog_id, period_start, period_end) to check for an existing current weekly report",
                "save_chat_weekly_report(dialog_id, period_start, period_end, analysis) after the MCP agent has generated the weekly report itself using verified daily reports",
            ],
            "owner_daily_report_creation": [
                "get_ai_instructions() and read Формирование отчетов / Ежедневный отчет по компании first",
                "open the active AI prompt in Сводная аналитика / Настройка промтов / ежедневный общий отчет для собственника and follow it as the report contract",
                "check all active chats for current chat_daily_reports on report_date",
                "if any active chat daily report is missing, run the daily_chat_report_creation workflow for that chat before continuing",
                "list_zoom_calls(date_from=report_date,date_to=report_date)",
                "for every same-day Zoom call, check analytical_note only; if missing, use zoom_call_report instructions and save_zoom_call_report before continuing",
                "get_previous_owner_daily_context(report_date) to read the previous calendar day's current owner daily report; if missing, stop or create the missing previous day first",
                "only after every chat daily report, every Zoom analytical report, and previous owner_daily_report are ready, create owner_daily_report",
                "if any required source is missing or failed, stop and return the missing chat/Zoom/OCR source list instead of writing owner_daily_report",
            ],
            "owner_weekly_report_creation": [
                "for each day in the week, run owner_daily_report_creation until the daily chain is complete",
                "generate missing chat weekly reports from verified daily chat reports",
                "create or refresh chat_overall_weekly_report",
                "only then create owner_weekly_report",
                "if any daily source chain is incomplete, stop and return the incomplete days and missing sources",
            ],
        },
    }

def tool_start_here_always_read_ai_instructions(_: dict[str, Any]) -> dict[str, Any]:
    instructions = load_ai_instructions()
    return {
        "mandatory_status": "READ_FIRST_AND_OBEY_EXACTLY",
        "purpose": "This is the mandatory entry tool for this MCP server. The assistant must read these live settings before any analysis, report, recommendation, database lookup plan, or final answer.",
        "source": "Настройки -> Инструкции для ИИ (table ai_instruction_folders)",
        "live_ai_instructions": instructions,
        "execution_contract": [
            "Treat every non-empty instruction as binding for the current user request.",
            "Do exactly what the relevant instruction says: source order, report format, required checks, save/read workflow, and stopping conditions.",
            "If a specific report contract is required by the instructions, call get_report_contract for that category before generating the report.",
            "If instructions require missing source checks, OCR, Zoom analysis, previous reports, or Bitrix refresh, complete those checks before conclusions.",
            "If the request conflicts with these instructions, explain the conflict and ask the user to update Настройки -> Инструкции для ИИ or confirm a one-off exception.",
            "If the user request is vague, ambiguous, or missing the needed scope, ask one concise clarifying question first. Do not infer dates, chats, people, report type, or whether to save/write unless the user said it clearly.",
            "When instructions are incomplete for the task, continue with get_context_guide and the relevant source tools instead of guessing.",
        ],
        "next_tool_guidance": [
            "Use get_context_guide for route selection and source rules.",
            "Use get_report_contract when generating configured reports.",
            "Use list_available_sources when freshness, availability, or row counts matter.",
        ],
    }

def tool_get_ai_instructions(_: dict[str, Any]) -> dict[str, Any]:
    return {
        "instructions": load_ai_instructions(),
        "note": "These instructions are loaded live from ai_instruction_folders. Edit them in the UI: Settings -> AI instructions.",
    }


def tool_get_report_contract(args: dict[str, Any]) -> dict[str, Any]:
    category_key = str(args.get("category_key") or "").strip()
    if not category_key:
        raise McpError(-32602, "Missing required argument: category_key")

    with connect() as conn:
        with conn.cursor() as cur:
            if not safe_table_exists(cur, "ai_prompt_categories") or not safe_table_exists(cur, "ai_prompts"):
                return {
                    "category_key": category_key,
                    "contract": None,
                    "message": "Report contract tables do not exist yet.",
                }
            cur.execute(
                """
                SELECT
                    c.category_key,
                    c.title AS category_title,
                    c.description AS category_description,
                    p.id AS contract_id,
                    p.prompt_key AS contract_key,
                    p.title AS contract_title,
                    p.prompt_text AS contract_text,
                    p.version,
                    p.created_at
                FROM ai_prompt_categories c
                JOIN ai_prompts p ON p.category_id = c.id
                WHERE c.is_active = TRUE
                  AND p.is_active = TRUE
                  AND c.category_key = %s
                ORDER BY p.version DESC, p.created_at DESC
                LIMIT 1
                """,
                (category_key,),
            )
            row = cur.fetchone()

    return {
        "category_key": category_key,
        "contract": row,
        "note": (
            "Use contract_text as the exact report-generation contract. "
            "For daily chat reports request category_key='chat_analysis'."
        ),
    }


def tool_get_company_profile(_: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            folders: list[dict[str, Any]] = []
            if safe_table_exists(cur, "company_folders"):
                cur.execute(
                    """
                    SELECT id, parent_id, name, content, sort_order, updated_at
                    FROM company_folders
                    ORDER BY parent_id NULLS FIRST, sort_order, lower(name), created_at
                    """
                )
                folders = cur.fetchall()
            if not safe_table_exists(cur, "company_profile"):
                return {
                    "title": "О компании",
                    "content": "",
                    "folders": folders,
                    "updated_at": None,
                    "message": "company_profile table does not exist yet.",
                }
            cur.execute(
                """
                SELECT title, content, updated_at
                FROM company_profile
                WHERE profile_key = 'main'
                """
            )
            row = cur.fetchone()
    if not row:
        return {"title": "О компании", "content": "", "folders": folders, "updated_at": None}
    return {
        "title": row["title"] or "О компании",
        "content": row["content"] or "",
        "folders": folders,
        "updated_at": row["updated_at"],
    }


def tool_list_company_files(args: dict[str, Any]) -> dict[str, Any]:
    include_empty = bool(args.get("include_empty", False))
    limit = parse_limit(args, 500)
    offset = parse_offset(args)

    filters = []
    if not include_empty:
        filters.append("COALESCE(f.content, '') <> ''")
    where_sql = "WHERE " + " AND ".join(filters) if filters else ""

    with connect() as conn:
        with conn.cursor() as cur:
            if not safe_table_exists(cur, "company_folders"):
                return {"items": [], "limit": limit, "offset": offset, "message": "company_folders table does not exist yet."}

            drive_join = ""
            drive_select = """
                NULL::text AS google_file_id,
                NULL::text AS source_url,
                NULL::text AS mime_type,
                NULL::timestamptz AS google_updated_at
            """
            if safe_table_exists(cur, "company_drive_sources"):
                drive_join = "LEFT JOIN company_drive_sources ds ON ds.folder_id = f.id"
                drive_select = """
                    ds.google_file_id,
                    ds.source_url,
                    ds.mime_type,
                    ds.google_updated_at
                """

            cur.execute(
                f"""
                WITH RECURSIVE folder_tree AS (
                    SELECT id, parent_id, name, ARRAY[name]::text[] AS path
                    FROM company_folders
                    WHERE parent_id IS NULL
                    UNION ALL
                    SELECT child.id, child.parent_id, child.name, folder_tree.path || child.name
                    FROM company_folders child
                    JOIN folder_tree ON folder_tree.id = child.parent_id
                )
                SELECT
                    f.id AS folder_id,
                    f.parent_id,
                    f.name,
                    array_to_string(ft.path, ' / ') AS path,
                    char_length(COALESCE(f.content, '')) AS content_length,
                    COALESCE(f.content, '') <> '' AS has_content,
                    f.updated_at,
                    {drive_select}
                FROM company_folders f
                JOIN folder_tree ft ON ft.id = f.id
                {drive_join}
                {where_sql}
                ORDER BY ft.path
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = cur.fetchall()

    return {
        "items": rows,
        "limit": limit,
        "offset": offset,
        "note": "Use get_company_file with folder_id or google_file_id to read full content.",
    }


def tool_get_company_file(args: dict[str, Any]) -> dict[str, Any]:
    folder_id = str(args.get("folder_id") or "").strip()
    google_file_id = str(args.get("google_file_id") or "").strip()
    if not folder_id and not google_file_id:
        raise McpError(-32602, "Provide folder_id or google_file_id")

    with connect() as conn:
        with conn.cursor() as cur:
            if not safe_table_exists(cur, "company_folders"):
                raise McpError(-32004, "company_folders table does not exist yet.")

            has_drive_sources = safe_table_exists(cur, "company_drive_sources")
            drive_join = ""
            drive_select = """
                NULL::text AS google_file_id,
                NULL::text AS source_url,
                NULL::text AS mime_type,
                NULL::timestamptz AS google_updated_at,
                '{}'::jsonb AS drive_raw_json
            """
            if has_drive_sources:
                drive_join = "LEFT JOIN company_drive_sources ds ON ds.folder_id = f.id"
                drive_select = """
                    ds.google_file_id,
                    ds.source_url,
                    ds.mime_type,
                    ds.google_updated_at,
                    ds.raw_json AS drive_raw_json
                """

            if google_file_id:
                if not has_drive_sources:
                    raise McpError(-32004, "company_drive_sources table does not exist yet.")
                filter_sql = "ds.google_file_id = %s"
                params: list[Any] = [google_file_id]
            else:
                filter_sql = "f.id = %s"
                params = [folder_id]

            cur.execute(
                f"""
                WITH RECURSIVE folder_tree AS (
                    SELECT id, parent_id, name, ARRAY[name]::text[] AS path
                    FROM company_folders
                    WHERE parent_id IS NULL
                    UNION ALL
                    SELECT child.id, child.parent_id, child.name, folder_tree.path || child.name
                    FROM company_folders child
                    JOIN folder_tree ON folder_tree.id = child.parent_id
                )
                SELECT
                    f.id AS folder_id,
                    f.parent_id,
                    f.name,
                    array_to_string(ft.path, ' / ') AS path,
                    f.content,
                    char_length(COALESCE(f.content, '')) AS content_length,
                    f.updated_at,
                    {drive_select}
                FROM company_folders f
                JOIN folder_tree ft ON ft.id = f.id
                {drive_join}
                WHERE {filter_sql}
                LIMIT 1
                """,
                params,
            )
            row = cur.fetchone()

    if not row:
        raise McpError(-32004, "Company file not found.")
    return row


def tool_search_company_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    limit = parse_limit(args, 50)
    offset = parse_offset(args)

    filters = []
    params: list[Any] = []
    if query:
        like = f"%{query}%"
        filters.append("(f.name ILIKE %s OR COALESCE(f.content, '') ILIKE %s)")
        params.extend([like, like])
    where_sql = "WHERE " + " AND ".join(filters) if filters else ""

    with connect() as conn:
        with conn.cursor() as cur:
            if not safe_table_exists(cur, "company_folders"):
                return {"items": [], "limit": limit, "offset": offset, "message": "company_folders table does not exist yet."}

            drive_join = ""
            drive_select = """
                NULL::text AS google_file_id,
                NULL::text AS source_url,
                NULL::text AS mime_type,
                NULL::timestamptz AS google_updated_at,
                '{}'::jsonb AS drive_raw_json
            """
            if safe_table_exists(cur, "company_drive_sources"):
                drive_join = "LEFT JOIN company_drive_sources ds ON ds.folder_id = f.id"
                drive_select = """
                    ds.google_file_id,
                    ds.source_url,
                    ds.mime_type,
                    ds.google_updated_at,
                    ds.raw_json AS drive_raw_json
                """

            cur.execute(
                f"""
                WITH RECURSIVE folder_tree AS (
                    SELECT id, parent_id, name, ARRAY[name]::text[] AS path
                    FROM company_folders
                    WHERE parent_id IS NULL
                    UNION ALL
                    SELECT child.id, child.parent_id, child.name, folder_tree.path || child.name
                    FROM company_folders child
                    JOIN folder_tree ON folder_tree.id = child.parent_id
                )
                SELECT
                    f.id,
                    f.parent_id,
                    f.name,
                    array_to_string(ft.path, ' / ') AS path,
                    f.content,
                    f.updated_at,
                    {drive_select}
                FROM company_folders f
                JOIN folder_tree ft ON ft.id = f.id
                {drive_join}
                {where_sql}
                ORDER BY
                    CASE WHEN %s = '' THEN 0 WHEN f.name ILIKE %s THEN 0 ELSE 1 END,
                    f.updated_at DESC NULLS LAST,
                    lower(f.name)
                LIMIT %s OFFSET %s
                """,
                [*params, query, f"%{query}%", limit, offset],
            )
            rows = cur.fetchall()

    return {"items": rows, "limit": limit, "offset": offset}


def tool_list_periods(args: dict[str, Any]) -> dict[str, Any]:
    limit = parse_limit(args, 90)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT message_day AS period, count(*) AS messages_count
                FROM chat_messages
                GROUP BY message_day
                ORDER BY message_day DESC
                LIMIT %s
                """,
                (limit,),
            )
            chat_periods = cur.fetchall()
            cur.execute(
                """
                SELECT report_date AS period, count(*) AS reports_count
                FROM chat_daily_reports
                WHERE is_current = TRUE
                GROUP BY report_date
                ORDER BY report_date DESC
                LIMIT %s
                """,
                (limit,),
            )
            report_periods = cur.fetchall()
            zoom_periods: list[dict[str, Any]] = []
            if safe_table_exists(cur, "zoom_calls"):
                cur.execute(
                    """
                    SELECT call_date AS period, count(*) AS zoom_calls_count
                    FROM zoom_calls
                    GROUP BY call_date
                    ORDER BY call_date DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                zoom_periods = cur.fetchall()
    return {
        "chat_message_periods": chat_periods,
        "chat_report_periods": report_periods,
        "zoom_call_periods": zoom_periods,
    }


def tool_get_period_index(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from")
    date_to = parse_date_arg(args, "date_to")
    if date_to < date_from:
        raise McpError(-32602, "date_to must be greater than or equal to date_from")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS messages_count, count(DISTINCT chat_id) AS chats_count
                FROM chat_messages
                WHERE message_day BETWEEN %s AND %s
                """,
                (date_from, date_to),
            )
            messages = cur.fetchone()
            cur.execute(
                """
                SELECT count(*) AS tasks_count
                FROM bitrix_tasks
                WHERE COALESCE(updated_at_bitrix, created_at_bitrix, deadline_at, created_at)::date
                      BETWEEN %s AND %s
                """,
                (date_from, date_to),
            )
            tasks = cur.fetchone()
            zoom_counts = {"zoom_calls_count": 0, "zoom_transcript_segments_count": 0}
            if safe_table_exists(cur, "zoom_calls"):
                cur.execute(
                    """
                    SELECT
                        count(*) AS zoom_calls_count,
                        COALESCE(sum(segment_counts.segments_count), 0)::bigint AS zoom_transcript_segments_count
                    FROM zoom_calls zc
                    LEFT JOIN (
                        SELECT call_id, count(*) AS segments_count
                        FROM zoom_call_transcript_segments
                        GROUP BY call_id
                    ) segment_counts ON segment_counts.call_id = zc.id
                    WHERE zc.call_date BETWEEN %s AND %s
                    """,
                    (date_from, date_to),
                )
                zoom_counts = cur.fetchone()
            cur.execute(
                """
                SELECT c.dialog_id, c.chat_title, count(m.id) AS messages_count
                FROM chat_messages m
                JOIN chats c ON c.id = m.chat_id
                WHERE m.message_day BETWEEN %s AND %s
                GROUP BY c.dialog_id, c.chat_title
                ORDER BY messages_count DESC, c.chat_title NULLS LAST
                LIMIT 100
                """,
                (date_from, date_to),
            )
            chats = cur.fetchall()
    return {
        "period": {"date_from": date_from, "date_to": date_to},
        "counts": {**messages, **tasks, **zoom_counts},
        "top_chats": chats,
        "available_tools": [
            "get_org_structure",
            "search_tasks",
            "list_chats",
            "search_messages",
            "get_chat_transcript",
            "list_zoom_calls",
            "get_zoom_call_transcript",
            "search_zoom_transcripts",
            "get_company_profile",
            "get_compact_export",
        ],
    }


def tool_get_org_structure(args: dict[str, Any]) -> dict[str, Any]:
    include_inactive = bool(args.get("include_inactive", False))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    d.id, d.bitrix_department_id, d.name,
                    pd.bitrix_department_id AS parent_bitrix_department_id,
                    pd.name AS parent_name,
                    hu.bitrix_user_id AS head_bitrix_user_id,
                    hu.full_name AS head_name
                FROM departments d
                LEFT JOIN departments pd ON pd.id = d.parent_id
                LEFT JOIN users hu ON hu.id = d.head_id
                ORDER BY COALESCE(pd.name, ''), d.name
                """
            )
            departments = cur.fetchall()
            cur.execute(
                """
                SELECT
                    u.id, u.bitrix_user_id, u.full_name, u.email, u.work_position,
                    u.is_active,
                    mu.bitrix_user_id AS manager_bitrix_user_id,
                    mu.full_name AS manager_name,
                    array_remove(array_agg(DISTINCT d.name), NULL) AS departments
                FROM users u
                LEFT JOIN users mu ON mu.id = u.manager_id
                LEFT JOIN user_departments ud ON ud.user_id = u.id
                LEFT JOIN departments d ON d.id = ud.department_id
                WHERE (%s = TRUE OR u.is_active = TRUE)
                GROUP BY u.id, mu.bitrix_user_id, mu.full_name
                ORDER BY u.full_name NULLS LAST, u.bitrix_user_id
                """,
                (include_inactive,),
            )
            users = cur.fetchall()
    return {"departments": departments, "users": users}


def tool_search_tasks(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from", required=False)
    date_to = parse_date_arg(args, "date_to", required=False)
    query = str(args.get("query") or "").strip()
    responsible_bitrix_user_id = args.get("responsible_bitrix_user_id")
    limit = parse_limit(args)
    offset = parse_offset(args)

    filters = []
    params: list[Any] = []
    if date_from:
        filters.append("COALESCE(t.updated_at_bitrix, t.created_at_bitrix, t.deadline_at, t.created_at)::date >= %s")
        params.append(date_from)
    if date_to:
        filters.append("COALESCE(t.updated_at_bitrix, t.created_at_bitrix, t.deadline_at, t.created_at)::date <= %s")
        params.append(date_to)
    if query:
        filters.append("(t.title ILIKE %s OR COALESCE(t.description, '') ILIKE %s)")
        like = f"%{query}%"
        params.extend([like, like])
    if responsible_bitrix_user_id not in (None, ""):
        filters.append("t.responsible_bitrix_user_id = %s")
        params.append(int(responsible_bitrix_user_id))

    where_sql = "WHERE " + " AND ".join(filters) if filters else ""
    params.extend([limit, offset])
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    t.bitrix_task_id, t.title, t.description, t.status, t.status_name,
                    t.priority, t.created_at_bitrix, t.updated_at_bitrix, t.deadline_at,
                    t.closed_at_bitrix,
                    cu.bitrix_user_id AS creator_bitrix_user_id,
                    cu.full_name AS creator_name,
                    ru.bitrix_user_id AS responsible_bitrix_user_id,
                    ru.full_name AS responsible_name
                FROM bitrix_tasks t
                LEFT JOIN users cu ON cu.id = t.creator_id
                LEFT JOIN users ru ON ru.id = t.responsible_id
                {where_sql}
                ORDER BY COALESCE(t.updated_at_bitrix, t.created_at_bitrix, t.deadline_at, t.created_at) DESC
                LIMIT %s OFFSET %s
                """,
                params,
            )
            rows = cur.fetchall()
    return {"items": rows, "limit": limit, "offset": offset}


def tool_list_chats(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from", required=False)
    date_to = parse_date_arg(args, "date_to", required=False)
    query = str(args.get("query") or "").strip()
    limit = parse_limit(args)
    offset = parse_offset(args)

    params: list[Any] = []
    filters = ["c.is_excluded = FALSE"]
    if query:
        filters.append("(c.chat_title ILIKE %s OR c.dialog_id ILIKE %s)")
        like = f"%{query}%"
        params.extend([like, like])
    message_join = ""
    message_select = "0 AS period_messages_count"
    if date_from and date_to:
        message_join = """
            LEFT JOIN chat_messages m
                ON m.chat_id = c.id AND m.message_day BETWEEN %s AND %s
        """
        params.extend([date_from, date_to])
        message_select = "count(m.id) AS period_messages_count"

    params.extend([limit, offset])
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    c.dialog_id, c.bitrix_chat_id, c.chat_title, c.chat_type,
                    c.members_count, c.last_message_at,
                    {message_select}
                FROM chats c
                {message_join}
                WHERE {' AND '.join(filters)}
                GROUP BY c.id
                ORDER BY period_messages_count DESC, c.last_message_at DESC NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params,
            )
            rows = cur.fetchall()
    return {"items": rows, "limit": limit, "offset": offset}


def tool_search_messages(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from")
    date_to = parse_date_arg(args, "date_to")
    query = str(args.get("query") or "").strip()
    dialog_id = str(args.get("dialog_id") or "").strip()
    include_ocr = bool(args.get("include_ocr", True))
    limit = parse_limit(args)
    offset = parse_offset(args)

    filters = ["m.message_day BETWEEN %s AND %s"]
    params: list[Any] = [date_from, date_to]
    if query:
        filters.append(
            """
            (
                m.message_text ILIKE %s
                OR EXISTS (
                    SELECT 1
                    FROM chat_message_files f
                    JOIN chat_file_ocr o ON o.file_id = f.id AND o.ocr_status = 'success'
                    WHERE f.message_id = m.id
                      AND o.ocr_text ILIKE %s
                )
            )
            """
        )
        like = f"%{query}%"
        params.extend([like, like])
    if dialog_id:
        filters.append("c.dialog_id = %s")
        params.append(dialog_id)
    params.extend([limit, offset])

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    c.dialog_id, c.chat_title,
                    m.bitrix_message_id, m.message_date, m.message_day,
                    u.bitrix_user_id AS author_bitrix_user_id,
                    u.full_name AS author_name,
                    m.message_text,
                    m.has_files,
                    COALESCE(
                        jsonb_agg(
                            DISTINCT jsonb_build_object(
                                'file_id', f.bitrix_file_id,
                                'file_name', f.file_name,
                                'file_type', f.file_type,
                                'mime_type', f.mime_type,
                                'ocr_status', o.ocr_status,
                                'ocr_text', CASE
                                    WHEN %s THEN o.ocr_text
                                    ELSE NULL
                                END
                            )
                        ) FILTER (WHERE f.id IS NOT NULL),
                        '[]'::jsonb
                    ) AS files
                FROM chat_messages m
                JOIN chats c ON c.id = m.chat_id
                LEFT JOIN users u ON u.id = m.author_id
                LEFT JOIN chat_message_files f ON f.message_id = m.id
                LEFT JOIN LATERAL (
                    SELECT ocr_provider, ocr_text, ocr_status
                    FROM chat_file_ocr
                    WHERE file_id = f.id
                    ORDER BY CASE ocr_provider WHEN 'manual' THEN 0 WHEN 'openai' THEN 1 ELSE 2 END
                    LIMIT 1
                ) o ON TRUE
                WHERE {' AND '.join(filters)}
                GROUP BY c.dialog_id, c.chat_title, m.id, m.message_day, u.bitrix_user_id, u.full_name
                ORDER BY m.message_date ASC, m.bitrix_message_id ASC
                LIMIT %s OFFSET %s
                """,
                [include_ocr, *params],
            )
            rows = cur.fetchall()
    return {"items": rows, "limit": limit, "offset": offset}


def tool_get_chat_transcript(args: dict[str, Any]) -> dict[str, Any]:
    dialog_id = str(args.get("dialog_id") or "").strip()
    if not dialog_id:
        raise McpError(-32602, "Missing required argument: dialog_id")
    date_from = parse_date_arg(args, "date_from")
    date_to = parse_date_arg(args, "date_to")
    include_ocr = bool(args.get("include_ocr", True))
    limit = parse_limit(args, 200)
    offset = parse_offset(args)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, dialog_id, bitrix_chat_id, chat_title, chat_type, members_count
                FROM chats
                WHERE dialog_id = %s
                """,
                (dialog_id,),
            )
            chat = cur.fetchone()
            if not chat:
                raise McpError(-32602, f"Unknown dialog_id: {dialog_id}")
            cur.execute(
                """
                SELECT
                    m.bitrix_message_id, m.message_date,
                    u.bitrix_user_id AS author_bitrix_user_id,
                    u.full_name AS author_name,
                    m.message_text,
                    m.has_files,
                    COALESCE(
                        jsonb_agg(
                            jsonb_build_object(
                                'file_id', f.bitrix_file_id,
                                'file_name', f.file_name,
                                'file_type', f.file_type,
                                'mime_type', f.mime_type,
                                'ocr_status', o.ocr_status,
                                'ocr_text', CASE
                                    WHEN %s THEN o.ocr_text
                                    ELSE NULL
                                END
                            )
                            ORDER BY f.bitrix_file_id
                        ) FILTER (WHERE f.id IS NOT NULL),
                        '[]'::jsonb
                    ) AS files
                FROM chat_messages m
                LEFT JOIN users u ON u.id = m.author_id
                LEFT JOIN chat_message_files f ON f.message_id = m.id
                LEFT JOIN LATERAL (
                    SELECT ocr_provider, ocr_text, ocr_status
                    FROM chat_file_ocr
                    WHERE file_id = f.id
                    ORDER BY CASE ocr_provider WHEN 'manual' THEN 0 WHEN 'openai' THEN 1 ELSE 2 END
                    LIMIT 1
                ) o ON TRUE
                WHERE m.chat_id = %s AND m.message_day BETWEEN %s AND %s
                GROUP BY m.id, m.message_day, u.bitrix_user_id, u.full_name
                ORDER BY m.message_date ASC, m.bitrix_message_id ASC
                LIMIT %s OFFSET %s
                """,
                (include_ocr, chat["id"], date_from, date_to, limit, offset),
            )
            messages = cur.fetchall()
    return {"chat": chat, "messages": messages, "limit": limit, "offset": offset}


def tool_get_chat_ocr_status(args: dict[str, Any]) -> dict[str, Any]:
    dialog_id = str(args.get("dialog_id") or "").strip()
    if not dialog_id:
        raise McpError(-32602, "Missing required argument: dialog_id")
    report_date = parse_date_arg(args, "report_date")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, dialog_id, bitrix_chat_id, chat_title, chat_type, members_count
                FROM chats
                WHERE dialog_id = %s
                """,
                (dialog_id,),
            )
            chat = cur.fetchone()
            if not chat:
                raise McpError(-32602, f"Unknown dialog_id: {dialog_id}")
            cur.execute(
                """
                SELECT
                    f.id,
                    f.bitrix_file_id,
                    f.file_name,
                    f.file_type,
                    f.mime_type,
                    m.bitrix_message_id,
                    COALESCE(o.ocr_status, 'missing') AS ocr_status,
                    o.ocr_text IS NOT NULL AND length(o.ocr_text) > 0 AS has_ocr_text
                FROM chat_messages m
                JOIN chat_message_files f ON f.message_id = m.id
                LEFT JOIN LATERAL (
                    SELECT ocr_status, ocr_text
                    FROM chat_file_ocr
                    WHERE file_id = f.id
                    ORDER BY CASE ocr_provider WHEN 'manual' THEN 0 WHEN 'openai' THEN 1 ELSE 2 END
                    LIMIT 1
                ) o ON TRUE
                WHERE m.chat_id = %s AND m.message_day = %s
                ORDER BY m.message_date ASC, m.bitrix_message_id ASC, f.bitrix_file_id ASC
                """,
                (chat["id"], report_date),
            )
            raw_files = cur.fetchall()

    files = [
        row for row in raw_files
        if is_ocr_supported_file(row.get("file_name"), row.get("file_type"), row.get("mime_type"))
    ]
    success = [row for row in files if row.get("ocr_status") == "success" and row.get("has_ocr_text")]
    errors = [row for row in files if row.get("ocr_status") == "error"]
    pending = [row for row in files if row not in success and row not in errors]
    status = "processed" if files and len(success) == len(files) else "no_files" if not files else "not_processed"

    return {
        "chat": chat,
        "report_date": report_date,
        "status": status,
        "status_text": "Готов к отчету" if status == "processed" else "Нет OCR-вложений" if status == "no_files" else "Нужна OCR-обработка",
        "ocr_supported_files": len(files),
        "ocr_success": len(success),
        "ocr_pending": len(pending),
        "ocr_errors": len(errors),
        "files": files,
    }


def tool_process_chat_ocr(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from")
    date_to = parse_date_arg(args, "date_to", required=False) or date_from
    dialog_id = str(args.get("dialog_id") or "").strip()
    force = bool(args.get("force", False))

    payload: dict[str, Any] = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "force": force,
    }
    if dialog_id:
        payload["dialog_id"] = dialog_id

    url = f"{local_app_base_url()}/api/chats/images/process"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(os.getenv("MCP_OCR_TIMEOUT", "900"))) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {"status": "success"}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        raise McpError(-32011, payload.get("error") or f"OCR processing failed with HTTP {exc.code}") from exc
    except Exception as exc:
        raise McpError(-32011, f"OCR processing failed: {exc}") from exc


def tool_list_zoom_calls(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from", required=False)
    date_to = parse_date_arg(args, "date_to", required=False)
    query = str(args.get("query") or "").strip()
    limit = parse_limit(args)
    offset = parse_offset(args)

    filters: list[str] = []
    params: list[Any] = []
    if date_from:
        filters.append("zc.call_date >= %s")
        params.append(date_from)
    if date_to:
        filters.append("zc.call_date <= %s")
        params.append(date_to)
    if query:
        filters.append("(zc.topic ILIKE %s OR zc.technical_topic ILIKE %s OR COALESCE(zc.transcript_text, '') ILIKE %s)")
        like = f"%{query}%"
        params.extend([like, like, like])
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.extend([limit, offset])

    with connect() as conn:
        with conn.cursor() as cur:
            if not safe_table_exists(cur, "zoom_calls"):
                return {"items": [], "limit": limit, "offset": offset, "note": "zoom_calls table does not exist"}
            cur.execute(
                f"""
                SELECT
                    zc.id, zc.zoom_account_key, zc.zoom_user_email, zc.zoom_meeting_id,
                    zc.zoom_uuid, zc.topic, zc.technical_topic, zc.call_date,
                    zc.start_time_msk, zc.end_time_msk, zc.duration_min,
                    zc.analytical_note,
                    count(DISTINCT zcp.id) AS participants_count,
                    count(DISTINCT zcts.id) AS transcript_segments_count,
                    array_remove(array_agg(DISTINCT COALESCE(zcp.participant_name, zcp.participant_email)), NULL) AS participants
                FROM zoom_calls zc
                LEFT JOIN zoom_call_participants zcp ON zcp.call_id = zc.id
                LEFT JOIN zoom_call_transcript_segments zcts ON zcts.call_id = zc.id
                {where_sql}
                GROUP BY zc.id
                ORDER BY zc.call_date DESC, zc.start_time_msk DESC
                LIMIT %s OFFSET %s
                """,
                params,
            )
            rows = cur.fetchall()
    return {"items": rows, "limit": limit, "offset": offset}


def tool_get_zoom_call_transcript(args: dict[str, Any]) -> dict[str, Any]:
    call_id = str(args.get("call_id") or "").strip()
    zoom_uuid = str(args.get("zoom_uuid") or "").strip()
    if not call_id and not zoom_uuid:
        raise McpError(-32602, "Missing required argument: call_id or zoom_uuid")
    include_full_text = bool(args.get("include_full_text", True))
    limit = parse_limit(args, 500)
    offset = parse_offset(args)

    with connect() as conn:
        with conn.cursor() as cur:
            if not safe_table_exists(cur, "zoom_calls"):
                return {"call": None, "segments": [], "note": "zoom_calls table does not exist"}
            if call_id:
                cur.execute("SELECT * FROM zoom_calls WHERE id = %s", (call_id,))
            else:
                cur.execute("SELECT * FROM zoom_calls WHERE zoom_uuid = %s", (zoom_uuid,))
            call = cur.fetchone()
            if not call:
                raise McpError(-32602, "Zoom call not found")
            cur.execute(
                """
                SELECT participant_name, participant_email, join_time, leave_time, duration_seconds
                FROM zoom_call_participants
                WHERE call_id = %s
                ORDER BY participant_name NULLS LAST, participant_email NULLS LAST, join_time NULLS LAST
                """,
                (call["id"],),
            )
            participants = cur.fetchall()
            cur.execute(
                """
                SELECT segment_index, cue_index, start_offset, end_offset, speaker, text
                FROM zoom_call_transcript_segments
                WHERE call_id = %s
                ORDER BY cue_index
                LIMIT %s OFFSET %s
                """,
                (call["id"], limit, offset),
            )
            segments = cur.fetchall()
            cur.execute(
                "SELECT count(*) AS total_segments FROM zoom_call_transcript_segments WHERE call_id = %s",
                (call["id"],),
            )
            total_segments = cur.fetchone()["total_segments"]

    call_payload = dict(call)
    call_payload.pop("raw_json", None)
    if not include_full_text:
        call_payload.pop("transcript_text", None)
    return {
        "call": call_payload,
        "participants": participants,
        "segments": segments,
        "total_segments": total_segments,
        "limit": limit,
        "offset": offset,
    }


def tool_save_zoom_call_report(args: dict[str, Any]) -> dict[str, Any]:
    call_id = str(args.get("call_id") or "").strip()
    zoom_uuid = str(args.get("zoom_uuid") or "").strip()
    if not call_id and not zoom_uuid:
        raise McpError(-32602, "Missing required argument: call_id or zoom_uuid")

    report_text = str(args.get("report_text") or "").strip()
    summary = str(args.get("summary") or "").strip()
    if not report_text and summary:
        report_text = summary
    if not report_text:
        raise McpError(-32602, "Missing required argument: report_text")

    analysis = args.get("analysis") if isinstance(args.get("analysis"), dict) else {}
    raw_input = args.get("raw_input") if isinstance(args.get("raw_input"), dict) else {}
    model = str(args.get("model") or "").strip()
    status = str(args.get("status") or "done").strip() or "done"
    if status not in {"done", "error"}:
        raise McpError(-32602, "status must be done or error")

    report_payload = {
        "source": "mcp_save_zoom_call_report",
        "summary": summary,
        "report_text": report_text,
        "analysis": analysis,
        "raw_input": raw_input,
        "model": model or None,
        "status": status,
        "saved_at": datetime.now().isoformat(),
    }

    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                if not safe_table_exists(cur, "zoom_calls"):
                    raise McpError(-32602, "zoom_calls table does not exist")
                if call_id:
                    cur.execute("SELECT id, zoom_uuid, call_date, topic, technical_topic FROM zoom_calls WHERE id = %s", (call_id,))
                else:
                    cur.execute("SELECT id, zoom_uuid, call_date, topic, technical_topic FROM zoom_calls WHERE zoom_uuid = %s", (zoom_uuid,))
                call = cur.fetchone()
                if not call:
                    raise McpError(-32602, "Zoom call not found")
                cur.execute(
                    """
                    UPDATE zoom_calls
                    SET analytical_note = %s,
                        raw_json = COALESCE(raw_json, '{}'::jsonb) || jsonb_build_object('ai_report', %s::jsonb),
                        updated_at = now()
                    WHERE id = %s
                    RETURNING id, zoom_uuid, call_date, topic, technical_topic, updated_at
                    """,
                    (report_text, jsonb_arg(report_payload), call["id"]),
                )
                updated = cur.fetchone()
    return {"saved": True, "call": updated, "status": status}


def tool_delete_zoom_call_report(args: dict[str, Any]) -> dict[str, Any]:
    call_id = str(args.get("call_id") or "").strip()
    zoom_uuid = str(args.get("zoom_uuid") or "").strip()
    if not call_id and not zoom_uuid:
        raise McpError(-32602, "Missing required argument: call_id or zoom_uuid")

    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                if not safe_table_exists(cur, "zoom_calls"):
                    raise McpError(-32602, "zoom_calls table does not exist")
                if call_id:
                    cur.execute("SELECT id FROM zoom_calls WHERE id = %s", (call_id,))
                else:
                    cur.execute("SELECT id FROM zoom_calls WHERE zoom_uuid = %s", (zoom_uuid,))
                call = cur.fetchone()
                if not call:
                    raise McpError(-32602, "Zoom call not found")
                cur.execute(
                    """
                    UPDATE zoom_calls
                    SET analytical_note = '',
                        raw_json = COALESCE(raw_json, '{}'::jsonb) - 'ai_report',
                        updated_at = now()
                    WHERE id = %s
                    RETURNING id, zoom_uuid, call_date, topic, technical_topic, updated_at
                    """,
                    (call["id"],),
                )
                updated = cur.fetchone()
    return {"deleted": True, "call": updated}


def tool_search_zoom_transcripts(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise McpError(-32602, "Missing required argument: query")
    date_from = parse_date_arg(args, "date_from", required=False)
    date_to = parse_date_arg(args, "date_to", required=False)
    limit = parse_limit(args)
    offset = parse_offset(args)

    filters = ["zcts.text ILIKE %s"]
    params: list[Any] = [f"%{query}%"]
    if date_from:
        filters.append("zc.call_date >= %s")
        params.append(date_from)
    if date_to:
        filters.append("zc.call_date <= %s")
        params.append(date_to)
    params.extend([limit, offset])

    with connect() as conn:
        with conn.cursor() as cur:
            if not safe_table_exists(cur, "zoom_call_transcript_segments"):
                return {"items": [], "limit": limit, "offset": offset, "note": "zoom transcript tables do not exist"}
            cur.execute(
                f"""
                SELECT
                    zc.id AS call_id,
                    zc.zoom_uuid,
                    zc.call_date,
                    zc.start_time_msk,
                    zc.topic,
                    zc.technical_topic,
                    zcts.segment_index,
                    zcts.cue_index,
                    zcts.start_offset,
                    zcts.end_offset,
                    zcts.speaker,
                    zcts.text
                FROM zoom_call_transcript_segments zcts
                JOIN zoom_calls zc ON zc.id = zcts.call_id
                WHERE {' AND '.join(filters)}
                ORDER BY zc.call_date DESC, zc.start_time_msk DESC, zcts.cue_index ASC
                LIMIT %s OFFSET %s
                """,
                params,
            )
            rows = cur.fetchall()
    return {"items": rows, "limit": limit, "offset": offset}


CHAT_REPORT_ITEM_KEYS = {
    "previous_day_tasks": "previous_day_task",
    "goals": "goal",
    "commitments": "commitment",
    "actions": "action",
    "results": "result",
    "questions": "question",
    "unanswered_questions": "unanswered_question",
    "decisions": "decision",
    "risks": "risk",
    "blockers": "blocker",
    "bitrix_references": "bitrix_reference",
    "next_steps": "next_step",
    "notes": "note",
}


def jsonb_arg(value: Any) -> Jsonb:
    return Jsonb(json_safe(value if value is not None else {}))


def analysis_items(analysis: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for key, item_type in CHAT_REPORT_ITEM_KEYS.items():
        raw_items = analysis.get(key) if isinstance(analysis, dict) else None
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if isinstance(item, dict):
                items.append((item_type, item))
            elif item not in (None, ""):
                items.append((item_type, {"text": str(item)}))
    return items


def count_analysis_items(analysis: dict[str, Any], keys: tuple[str, ...]) -> int:
    total = 0
    for key in keys:
        value = analysis.get(key)
        if isinstance(value, list):
            total += len([item for item in value if item])
    return total


def item_text(item: dict[str, Any]) -> str:
    for key in ("text", "title", "summary", "question", "decision", "reason"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return json.dumps(json_safe(item), ensure_ascii=False)[:1000]


def evidence_message_ids(item: dict[str, Any]) -> list[int]:
    values = item.get("evidence_message_ids") or []
    if not isinstance(values, list):
        return []
    result: list[int] = []
    for value in values:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def silence_days_label(days: int) -> str:
    if days <= 0:
        days = 1
    last_two = days % 100
    last = days % 10
    if 11 <= last_two <= 14:
        unit = "дней"
    elif last == 1:
        unit = "день"
    elif 2 <= last <= 4:
        unit = "дня"
    else:
        unit = "дней"
    return f"{days} {unit}"


def chat_tail_current_status(item: dict[str, Any]) -> str:
    for key in ("current_status", "status", "answer_status"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def normalize_tail_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^\s*\[[^\]]+\]\s*", "", text)
    text = re.sub(r"^\s*[^—:-]{1,80}\s*[—:-]\s*", "", text)
    text = re.sub(r"\s+срок\s+\d{1,2}\.\d{1,2}(?:\.\d{4})?\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .;:")
    return text


def tail_key(item: dict[str, Any]) -> tuple[str, str]:
    person = str(item.get("person_name") or item.get("addressed_to") or item.get("asked_by") or "").strip().lower()
    return (person, normalize_tail_text(item_text(item)))


def previous_tail_days_by_key(cur: Any, chat_id: Any, report_date: date) -> dict[tuple[str, str], int]:
    previous_date = report_date - timedelta(days=1)
    cur.execute(
        """
        SELECT raw_ai_json
        FROM chat_daily_reports
        WHERE chat_id = %s AND report_date = %s
        ORDER BY is_current DESC, version DESC, generated_at DESC
        LIMIT 1
        """,
        (chat_id, previous_date),
    )
    row = cur.fetchone()
    if not row:
        return {}
    raw = row.get("raw_ai_json") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    analysis = raw.get("analysis") if isinstance(raw, dict) else {}
    if not isinstance(analysis, dict):
        return {}
    result: dict[tuple[str, str], int] = {}
    for item in analysis.get("previous_day_tasks") or []:
        if not isinstance(item, dict):
            continue
        status = chat_tail_current_status(item)
        if status in {"done", "cancelled", "answered"}:
            continue
        result[tail_key(item)] = max(to_int(item.get("days_open")) or 0, 0) + 1
    return result


def apply_silence_days_to_chat_report(
    cur: Any,
    chat_id: Any,
    report_date: date,
    analysis: dict[str, Any],
    report_text: str,
) -> str:
    previous_days = previous_tail_days_by_key(cur, chat_id, report_date)
    silence_days: list[int] = []
    for item in analysis.get("previous_day_tasks") or []:
        if not isinstance(item, dict):
            continue
        status = chat_tail_current_status(item)
        if status != "no_info":
            continue
        key = tail_key(item)
        days = to_int(item.get("days_open")) or previous_days.get(key) or 1
        item["days_open"] = days
        silence_days.append(days)

    if not report_text or not silence_days:
        return report_text

    iterator = iter(silence_days)

    def replace_silence(match: Any) -> str:
        try:
            days = next(iterator)
        except StopIteration:
            days = 1
        return f"[тишина {silence_days_label(days)}]"

    return re.sub(r"\[тишина\](?!\s*\d)", replace_silence, report_text)


def user_id_by_name(cur: Any, name: str | None) -> Any:
    if not name:
        return None
    cur.execute(
        """
        SELECT id
        FROM users
        WHERE lower(full_name) = lower(%s)
        ORDER BY is_active DESC, updated_at DESC NULLS LAST
        LIMIT 1
        """,
        (name,),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def tool_get_chat_daily_report(args: dict[str, Any]) -> dict[str, Any]:
    dialog_id = str(args.get("dialog_id") or "").strip()
    if not dialog_id:
        raise McpError(-32602, "Missing required argument: dialog_id")
    report_date = parse_date_arg(args, "report_date")
    include_items = bool(args.get("include_items", True))

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, c.dialog_id, c.chat_title
                FROM chat_daily_reports r
                JOIN chats c ON c.id = r.chat_id
                WHERE c.dialog_id = %s AND r.report_date = %s
                ORDER BY r.is_current DESC, r.version DESC, r.generated_at DESC
                LIMIT 1
                """,
                (dialog_id, report_date),
            )
            report = cur.fetchone()
            if not report:
                return {"report": None, "items": [], "message": "Chat daily report not found."}
            items: list[dict[str, Any]] = []
            if include_items:
                cur.execute(
                    """
                    SELECT i.item_type, i.item_text, u.full_name AS person_name,
                           i.confidence, i.evidence_message_ids, i.raw_json, i.created_at
                    FROM chat_report_items i
                    LEFT JOIN users u ON u.id = i.user_id
                    WHERE i.chat_daily_report_id = %s
                    ORDER BY i.created_at, i.item_type
                    """,
                    (report["id"],),
                )
                items = cur.fetchall()
    return {"report": report, "items": items}


def tool_get_owner_reports(args: dict[str, Any]) -> dict[str, Any]:
    report_kind = str(args.get("report_kind") or "daily").strip().lower()
    limit = parse_limit(args, 20)
    if report_kind not in {"daily", "weekly"}:
        raise McpError(-32602, "report_kind must be 'daily' or 'weekly'")

    with connect() as conn:
        with conn.cursor() as cur:
            if report_kind == "daily":
                cur.execute(
                    """
                    SELECT id, report_date, version, is_current, generated_at,
                           summary, dynamics_summary, risks_summary, recommendations, report_text, raw_json
                    FROM owner_daily_reports
                    WHERE is_current = TRUE
                    ORDER BY report_date DESC, version DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, period_start, period_end, version, is_current, generated_at,
                           summary, dynamics_summary, risks_summary, recommendations, report_text, raw_json
                    FROM owner_weekly_reports
                    WHERE is_current = TRUE
                    ORDER BY period_start DESC, period_end DESC, version DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            reports = cur.fetchall()
    return {
        "report_kind": report_kind,
        "reports": reports,
        "rule": "Use these reports as continuity context before making recommendations or judging what is done/open/repeated.",
    }


def tool_get_previous_owner_daily_context(args: dict[str, Any]) -> dict[str, Any]:
    report_date = parse_date_arg(args, "report_date")
    if report_date is None:
        raise McpError(-32602, "Missing required argument: report_date")
    previous_date = report_date - timedelta(days=1)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, report_date, version, is_current, generated_at,
                       summary, dynamics_summary, risks_summary, recommendations,
                       report_text, raw_json
                FROM owner_daily_reports
                WHERE report_date = %s AND is_current = TRUE
                ORDER BY version DESC
                LIMIT 1
                """,
                (previous_date,),
            )
            report = cur.fetchone()

    if not report:
        return {
            "report_date": report_date,
            "previous_report_date": previous_date,
            "previous_owner_daily": None,
            "message": "Previous owner daily report was not found for the previous calendar day.",
            "rule": "Do not invent the previous owner report. Use other verified sources and state that previous owner continuity is unavailable.",
        }

    raw_json = report.get("raw_json") if isinstance(report, dict) else None
    analysis = raw_json.get("analysis") if isinstance(raw_json, dict) and isinstance(raw_json.get("analysis"), dict) else None
    return {
        "report_date": report_date,
        "previous_report_date": previous_date,
        "previous_owner_daily": {
            "id": report.get("id"),
            "report_date": report.get("report_date"),
            "version": report.get("version"),
            "generated_at": report.get("generated_at"),
            "summary": report.get("summary"),
            "dynamics_summary": report.get("dynamics_summary"),
            "risks_summary": report.get("risks_summary"),
            "recommendations": report.get("recommendations"),
            "report_text": report.get("report_text"),
            "analysis": analysis,
        },
        "rule": "Use this previous-day owner daily context only for continuity: open questions, repeated risks, completed items, and recommendations. Do not treat it as evidence for the new day without current sources.",
    }


def owner_report_raw_json(args: dict[str, Any], report_text: str, model: str, status: str) -> dict[str, Any]:
    raw_json = args.get("raw_json") if isinstance(args.get("raw_json"), dict) else {}
    analysis = args.get("analysis") if isinstance(args.get("analysis"), dict) else {}
    raw = dict(raw_json or {})
    raw.setdefault("source", "mcp_save_owner_report")
    raw["model"] = model
    raw["status"] = status
    owner_payload_keys = (
        "manager_recommendations",
        "manager_messages",
        "open_tasks",
        "overdue_tasks",
        "no_response",
        "goal_dynamics",
        "weekly_dynamics",
        "daily_owner_reports_manifest",
    )
    owner_payload = {key: args.get(key) for key in owner_payload_keys if key in args}
    if owner_payload:
        raw["owner_payload"] = owner_payload
    if analysis:
        raw["analysis"] = analysis
    if args.get("raw_input") and isinstance(args.get("raw_input"), dict):
        raw["raw_input"] = args.get("raw_input")
    if report_text:
        raw["report_text"] = report_text
    return raw


def tool_save_owner_daily_report(args: dict[str, Any]) -> dict[str, Any]:
    report_date = parse_date_arg(args, "report_date")
    report_text = str(args.get("report_text") or "").strip()
    summary = str(args.get("summary") or "").strip() or report_text
    if not report_text and not summary:
        raise McpError(-32602, "Missing required argument: report_text or summary")
    status = str(args.get("status") or "done").strip()
    if status not in {"done", "no_data", "error"}:
        raise McpError(-32602, "status must be one of: done, no_data, error")
    model = str(args.get("model") or "mcp-manual").strip()
    raw_json = owner_report_raw_json(args, report_text, model, status)
    dynamics_summary = str(args.get("dynamics_summary") or "").strip() or None
    risks_summary = str(args.get("risks_summary") or "").strip() or None
    recommendations = str(args.get("recommendations") or "").strip() or None

    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM owner_daily_reports WHERE report_date = %s",
                    (report_date,),
                )
                version = int(cur.fetchone()["version"])
                cur.execute(
                    "UPDATE owner_daily_reports SET is_current = FALSE WHERE report_date = %s AND is_current = TRUE",
                    (report_date,),
                )
                cur.execute(
                    """
                    INSERT INTO owner_daily_reports (
                        report_date, version, is_current, ai_request_id, prompt_id, generated_at,
                        summary, dynamics_summary, risks_summary, recommendations, report_text, raw_json
                    )
                    VALUES (%s, %s, TRUE, NULL, NULL, now(), %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        report_date,
                        version,
                        summary,
                        dynamics_summary,
                        risks_summary,
                        recommendations,
                        report_text,
                        jsonb_arg(raw_json),
                    ),
                )
                report_id = cur.fetchone()["id"]
    return {
        "report_id": str(report_id),
        "report_date": report_date.isoformat(),
        "version": version,
        "status": status,
        "is_current": True,
    }


def tool_save_owner_weekly_report(args: dict[str, Any]) -> dict[str, Any]:
    period_start = parse_date_arg(args, "period_start")
    period_end = parse_date_arg(args, "period_end")
    if period_end < period_start:
        raise McpError(-32602, "period_end must be greater than or equal to period_start")
    report_text = str(args.get("report_text") or "").strip()
    summary = str(args.get("summary") or "").strip() or report_text
    if not report_text and not summary:
        raise McpError(-32602, "Missing required argument: report_text or summary")
    status = str(args.get("status") or "done").strip()
    if status not in {"done", "no_data", "error"}:
        raise McpError(-32602, "status must be one of: done, no_data, error")
    model = str(args.get("model") or "mcp-manual").strip()
    raw_json = owner_report_raw_json(args, report_text, model, status)
    dynamics_summary = str(args.get("dynamics_summary") or "").strip() or None
    risks_summary = str(args.get("risks_summary") or "").strip() or None
    recommendations = str(args.get("recommendations") or "").strip() or None

    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1 AS version
                    FROM owner_weekly_reports
                    WHERE period_start = %s AND period_end = %s
                    """,
                    (period_start, period_end),
                )
                version = int(cur.fetchone()["version"])
                cur.execute(
                    """
                    UPDATE owner_weekly_reports
                    SET is_current = FALSE
                    WHERE period_start = %s AND period_end = %s AND is_current = TRUE
                    """,
                    (period_start, period_end),
                )
                cur.execute(
                    """
                    INSERT INTO owner_weekly_reports (
                        period_start, period_end, version, is_current, ai_request_id, prompt_id, generated_at,
                        summary, dynamics_summary, risks_summary, recommendations, report_text, raw_json
                    )
                    VALUES (%s, %s, %s, TRUE, NULL, NULL, now(), %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        period_start,
                        period_end,
                        version,
                        summary,
                        dynamics_summary,
                        risks_summary,
                        recommendations,
                        report_text,
                        jsonb_arg(raw_json),
                    ),
                )
                report_id = cur.fetchone()["id"]
    return {
        "report_id": str(report_id),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "version": version,
        "status": status,
        "is_current": True,
    }


def tool_save_chat_daily_report(args: dict[str, Any]) -> dict[str, Any]:
    dialog_id = str(args.get("dialog_id") or "").strip()
    if not dialog_id:
        raise McpError(-32602, "Missing required argument: dialog_id")
    report_date = parse_date_arg(args, "report_date")
    report_text = str(args.get("report_text") or "").strip()
    original_report_text = report_text
    summary = str(args.get("summary") or report_text or "").strip()
    analysis = args.get("analysis") if isinstance(args.get("analysis"), dict) else {}
    if not summary and isinstance(analysis, dict):
        summary = str(analysis.get("summary") or "").strip()
    if not summary:
        raise McpError(-32602, "Missing required argument: summary or analysis.summary")
    status = str(args.get("status") or "done").strip()
    if status == "done" and not analysis:
        raise McpError(
            -32602,
            "analysis is required for done daily reports. Call get_report_contract(category_key='chat_analysis') "
            "and save the full structured JSON, including previous_day_tasks, commitments, next_steps, risks, etc.",
        )
    model = str(args.get("model") or "mcp-manual").strip()
    raw_ai_json = {
        "source": "mcp_save_chat_daily_report",
        "model": model,
        "analysis": analysis,
        "raw_input": args.get("raw_input") if isinstance(args.get("raw_input"), dict) else {},
    }
    if report_text:
        raw_ai_json["report_text"] = report_text
    risks_summary = str(args.get("risks_summary") or "").strip() or None
    decisions_summary = str(args.get("decisions_summary") or "").strip() or None
    if status not in {"done", "no_data", "error"}:
        raise McpError(-32602, "status must be one of: done, no_data, error")

    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM chats WHERE dialog_id = %s", (dialog_id,))
                chat = cur.fetchone()
                if not chat:
                    raise McpError(-32602, f"Unknown dialog_id: {dialog_id}")
                chat_id = chat["id"]
                report_text = apply_silence_days_to_chat_report(cur, chat_id, report_date, analysis, report_text)
                if report_text:
                    raw_ai_json["report_text"] = report_text
                if summary == original_report_text:
                    summary = report_text
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM chat_daily_reports WHERE chat_id = %s AND report_date = %s",
                    (chat_id, report_date),
                )
                version = cur.fetchone()["version"]
                cur.execute(
                    """
                    UPDATE chat_daily_reports
                    SET is_current = FALSE, updated_at = now()
                    WHERE chat_id = %s AND report_date = %s AND is_current = TRUE
                    """,
                    (chat_id, report_date),
                )
                cur.execute(
                    """
                    INSERT INTO chat_daily_reports (
                        chat_id, report_date, version, is_current, generated_at,
                        messages_count, files_count, ocr_files_count,
                        extracted_tasks_count, extracted_goals_count, extracted_facts_count,
                        summary, risks_summary, decisions_summary, raw_ai_json, status
                    )
                    SELECT %s, %s, %s, TRUE, now(),
                           COUNT(DISTINCT m.id),
                           COUNT(DISTINCT f.id),
                           COUNT(DISTINCT o.id),
                           %s, %s, %s,
                           %s, %s, %s, %s, %s
                    FROM chats c
                    LEFT JOIN chat_messages m ON m.chat_id = c.id AND m.message_day = %s
                    LEFT JOIN chat_message_files f ON f.chat_id = c.id AND f.message_day = %s
                    LEFT JOIN chat_file_ocr o ON o.file_id = f.id AND o.ocr_status = 'success'
                    WHERE c.id = %s
                    GROUP BY c.id
                    RETURNING id
                    """,
                    (
                        chat_id,
                        report_date,
                        version,
                        count_analysis_items(analysis, ("commitments", "next_steps", "previous_day_tasks")),
                        count_analysis_items(analysis, ("goals",)),
                        count_analysis_items(analysis, ("results", "decisions")),
                        summary,
                        risks_summary,
                        decisions_summary,
                        jsonb_arg(raw_ai_json),
                        status,
                        report_date,
                        report_date,
                        chat_id,
                    ),
                )
                report_id = cur.fetchone()["id"]
                saved_items = 0
                for report_item_type, item in analysis_items(analysis):
                    text = item_text(item)
                    if not text:
                        continue
                    cur.execute(
                        """
                        INSERT INTO chat_report_items (
                            chat_daily_report_id, chat_id, report_date, item_type,
                            item_text, user_id, confidence, evidence_message_ids, raw_json
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            report_id,
                            chat_id,
                            report_date,
                            report_item_type,
                            text,
                            user_id_by_name(cur, str(item.get("person_name") or "").strip() or None),
                            item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else None,
                            evidence_message_ids(item),
                            jsonb_arg(item),
                        ),
                    )
                    saved_items += 1
    return {"report_id": report_id, "version": version, "items_saved": saved_items, "status": status}


def tool_get_chat_weekly_report(args: dict[str, Any]) -> dict[str, Any]:
    dialog_id = str(args.get("dialog_id") or "").strip()
    if not dialog_id:
        raise McpError(-32602, "Missing required argument: dialog_id")
    period_start = parse_date_arg(args, "period_start")
    period_end = parse_date_arg(args, "period_end")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, c.dialog_id, c.chat_title
                FROM chat_weekly_reports r
                JOIN chats c ON c.id = r.chat_id
                WHERE c.dialog_id = %s
                  AND r.period_start = %s
                  AND r.period_end = %s
                  AND r.is_current = TRUE
                ORDER BY r.version DESC, r.generated_at DESC
                LIMIT 1
                """,
                (dialog_id, period_start, period_end),
            )
            report = cur.fetchone()
    return {"report": report, "message": None if report else "Chat weekly report not found."}


def tool_save_chat_weekly_report(args: dict[str, Any]) -> dict[str, Any]:
    dialog_id = str(args.get("dialog_id") or "").strip()
    if not dialog_id:
        raise McpError(-32602, "Missing required argument: dialog_id")
    period_start = parse_date_arg(args, "period_start")
    period_end = parse_date_arg(args, "period_end")
    if period_end < period_start:
        raise McpError(-32602, "period_end must be greater than or equal to period_start")

    analysis = args.get("analysis") if isinstance(args.get("analysis"), dict) else {}
    report_text = str(args.get("report_text") or analysis.get("report_text") or "").strip()
    summary = str(args.get("summary") or analysis.get("summary") or report_text or "").strip()
    if not summary:
        raise McpError(-32602, "Missing required argument: summary or analysis.summary")
    model = str(args.get("model") or "mcp-manual").strip()
    raw_json = {
        "source": "mcp_save_chat_weekly_report",
        "model": model,
        "analysis": analysis,
        "raw_input": args.get("raw_input") if isinstance(args.get("raw_input"), dict) else {},
    }
    if report_text:
        raw_json["report_text"] = report_text

    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM chats WHERE dialog_id = %s", (dialog_id,))
                chat = cur.fetchone()
                if not chat:
                    raise McpError(-32602, f"Unknown dialog_id: {dialog_id}")
                chat_id = chat["id"]
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS daily_reports_count,
                        COALESCE(SUM(messages_count), 0) AS messages_count,
                        COALESCE(SUM(extracted_goals_count), 0) AS goals_created_count,
                        COALESCE(SUM(extracted_tasks_count), 0) AS extracted_tasks_count
                    FROM chat_daily_reports
                    WHERE chat_id = %s
                      AND report_date BETWEEN %s AND %s
                      AND is_current = TRUE
                    """,
                    (chat_id, period_start, period_end),
                )
                stats = cur.fetchone() or {}
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM chat_weekly_reports WHERE chat_id = %s AND period_start = %s AND period_end = %s",
                    (chat_id, period_start, period_end),
                )
                version = cur.fetchone()["version"]
                cur.execute(
                    """
                    UPDATE chat_weekly_reports
                    SET is_current = FALSE
                    WHERE chat_id = %s AND period_start = %s AND period_end = %s AND is_current = TRUE
                    """,
                    (chat_id, period_start, period_end),
                )
                cur.execute(
                    """
                    INSERT INTO chat_weekly_reports (
                        chat_id, period_start, period_end, version, is_current, ai_request_id, prompt_id,
                        generated_at, days_count, daily_reports_count, messages_count,
                        goals_created_count, goal_updates_count, commitments_count, results_count,
                        next_steps_count, risks_count, blockers_count, unresolved_questions_count,
                        done_goal_updates_count, high_risk_goal_updates_count, summary,
                        dynamics_summary, positives_summary, problems_summary, recommendations, raw_json
                    ) VALUES (
                        %s, %s, %s, %s, TRUE, NULL, NULL, now(),
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    RETURNING id
                    """,
                    (
                        chat_id,
                        period_start,
                        period_end,
                        version,
                        (period_end - period_start).days + 1,
                        int(stats.get("daily_reports_count") or 0),
                        int(stats.get("messages_count") or 0),
                        int(stats.get("goals_created_count") or 0),
                        count_analysis_items(analysis, ("goal_updates", "key_goal_dynamics")),
                        count_analysis_items(analysis, ("commitments", "hanging_tasks_by_owner", "not_done")),
                        count_analysis_items(analysis, ("results", "closed_small_goals")),
                        count_analysis_items(analysis, ("next_steps", "recommendations")),
                        count_analysis_items(analysis, ("risks",)),
                        count_analysis_items(analysis, ("blockers",)),
                        count_analysis_items(analysis, ("unanswered_questions", "no_response_to_feedback")),
                        count_analysis_items(analysis, ("closed_small_goals",)),
                        count_analysis_items(analysis, ("hanging_goals", "weak_performers")),
                        summary,
                        str(analysis.get("dynamics_summary") or "").strip() or None,
                        str(analysis.get("positives_summary") or "").strip() or None,
                        str(analysis.get("problems_summary") or "").strip() or None,
                        str(analysis.get("recommendations") or "").strip() or None,
                        jsonb_arg(raw_json),
                    ),
                )
                report_id = cur.fetchone()["id"]
    return {"report_id": report_id, "version": version, "status": "done"}


def tool_upsert_ai_instruction(args: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(args.get("path") or "").strip()
    content = str(args.get("content") or "")
    if not raw_path:
        raise McpError(-32602, "Missing required argument: path")
    if len(content) > 500_000:
        raise McpError(-32602, "content is too large; maximum is 500000 characters")
    path_parts = [part.strip() for part in raw_path.replace("\\", "/").split("/") if part.strip()]
    if not path_parts:
        raise McpError(-32602, "path must include at least one folder name")

    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                if not safe_table_exists(cur, "ai_instruction_folders"):
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS ai_instruction_folders (
                            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                            parent_id uuid REFERENCES ai_instruction_folders(id) ON DELETE CASCADE,
                            name text NOT NULL,
                            content text NOT NULL DEFAULT '',
                            sort_order int NOT NULL DEFAULT 0,
                            created_at timestamptz NOT NULL DEFAULT now(),
                            updated_at timestamptz NOT NULL DEFAULT now(),
                            CHECK (btrim(name) <> '')
                        )
                        """
                    )
                parent_id = None
                current = None
                for index, name in enumerate(path_parts):
                    cur.execute(
                        """
                        SELECT id, name
                        FROM ai_instruction_folders
                        WHERE ((%s::uuid IS NULL AND parent_id IS NULL) OR parent_id = %s::uuid)
                          AND lower(name) = lower(%s)
                        LIMIT 1
                        """,
                        (parent_id, parent_id, name),
                    )
                    current = cur.fetchone()
                    if not current:
                        cur.execute(
                            """
                            SELECT COALESCE(max(sort_order), -1) + 1 AS sort_order
                            FROM ai_instruction_folders
                            WHERE ((%s::uuid IS NULL AND parent_id IS NULL) OR parent_id = %s::uuid)
                            """,
                            (parent_id, parent_id),
                        )
                        sort_order = cur.fetchone()["sort_order"]
                        cur.execute(
                            """
                            INSERT INTO ai_instruction_folders (parent_id, name, content, sort_order)
                            VALUES (%s, %s, '', %s)
                            RETURNING id, name
                            """,
                            (parent_id, name, sort_order),
                        )
                        current = cur.fetchone()
                    parent_id = current["id"]
                    if index == len(path_parts) - 1:
                        cur.execute(
                            """
                            UPDATE ai_instruction_folders
                            SET content = %s, updated_at = now()
                            WHERE id = %s
                            RETURNING id, name, content, updated_at
                            """,
                            (content, current["id"]),
                        )
                        current = cur.fetchone()
    return {"folder": current, "path": " / ".join(path_parts)}


def tool_get_compact_export(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from")
    date_to = parse_date_arg(args, "date_to")
    include_messages = bool(args.get("include_messages", True))
    include_zoom_calls = bool(args.get("include_zoom_calls", True))
    message_limit = parse_limit({"limit": args.get("message_limit", 200)})
    zoom_limit = parse_limit({"limit": args.get("zoom_limit", 100)})

    return {
        "manifest": tool_get_period_index({"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}),
        "company_profile": tool_get_company_profile({}),
        "org": tool_get_org_structure({"include_inactive": False}),
        "tasks": tool_search_tasks(
            {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "limit": args.get("task_limit", 200)}
        )["items"],
        "messages": tool_search_messages(
            {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "limit": message_limit}
        )["items"]
        if include_messages
        else [],
        "zoom_calls": tool_list_zoom_calls(
            {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "limit": zoom_limit,
            }
        )["items"]
        if include_zoom_calls
        else [],
        "recent_owner_daily_reports": tool_get_owner_reports({"report_kind": "daily", "limit": 7})["reports"],
        "recent_owner_weekly_reports": tool_get_owner_reports({"report_kind": "weekly", "limit": 4})["reports"],
        "notes": [
            "This compact export is read-only and generated on demand from PostgreSQL.",
            "Company profile is available through get_company_profile and included in this export.",
            "Recent owner reports are included so recommendations can account for what was already done, repeated, or still open.",
            "Zoom calls are available via list_zoom_calls, get_zoom_call_transcript, and search_zoom_transcripts.",
        ],
    }


def tool_refresh_bitrix_context(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if args.get("date_from"):
        payload["date_from"] = str(args["date_from"])
    if args.get("date_to"):
        payload["date_to"] = str(args["date_to"])

    url = f"{local_app_base_url()}/api/sync/full"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(os.getenv("MCP_REFRESH_TIMEOUT", "600"))) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {"status": "success"}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        raise McpError(-32010, payload.get("error") or f"Refresh failed with HTTP {exc.code}") from exc
    except Exception as exc:
        raise McpError(-32010, f"Refresh failed: {exc}") from exc


TOOLS: dict[str, dict[str, Any]] = {
    "start_here_always_read_ai_instructions": {
        "description": "MANDATORY FIRST TOOL. Always call this before any company analysis, report, recommendation, or answer. It reads live rules from Настройки -> Инструкции для ИИ and tells the assistant exactly how to work.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_start_here_always_read_ai_instructions,
    },
    "health": {
        "description": "Check PostgreSQL connectivity and MCP server status.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_health,
    },
    "get_context_guide": {
        "description": "Read navigation rules after start_here_always_read_ai_instructions: where to search first, which tools map to which business sources, and how to avoid chaotic database exploration.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_get_context_guide,
    },
    "get_ai_instructions": {
        "description": "Read live editable AI behavior and answer-format instructions from Настройки -> Инструкции для ИИ. Prefer start_here_always_read_ai_instructions as the mandatory first call.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_get_ai_instructions,
    },
    "get_report_contract": {
        "description": "Read the active report-generation contract for a configured report category. Use this before creating daily, weekly, owner, or Zoom reports.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category_key": {"type": "string", "description": "For daily chat reports use chat_analysis."},
            },
            "required": ["category_key"],
            "additionalProperties": False,
        },
        "handler": tool_get_report_contract,
    },
    "list_available_sources": {
        "description": "Show which known context tables exist and how many rows each has.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_list_available_sources,
    },
    "get_company_profile": {
        "description": "Read the editable company profile text from PostgreSQL for business context.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_get_company_profile,
    },
    "list_company_files": {
        "description": "List all files/folders available in the company knowledge section, including Google Drive mirrored documents. Use before reading specific company files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_empty": {"type": "boolean", "description": "Include folders/files that have no text content."},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": tool_list_company_files,
    },
    "get_company_file": {
        "description": "Read the full text and source metadata for one company knowledge file by folder_id or google_file_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "description": "company_folders.id from list_company_files/search_company_knowledge."},
                "google_file_id": {"type": "string", "description": "Google Drive file id from list_company_files/search_company_knowledge."},
            },
            "additionalProperties": False,
        },
        "handler": tool_get_company_file,
    },
    "search_company_knowledge": {
        "description": "Search the persistent 'О компании' knowledge base, including Google Drive mirrored docs/sheets. Use for rules, regulations, processes, and company facts before searching chats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text. Empty returns the latest folders/documents."},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": tool_search_company_knowledge,
    },
    "list_periods": {
        "description": "List recent dates available in chat messages and chat reports.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT}},
            "additionalProperties": False,
        },
        "handler": tool_list_periods,
    },
    "get_period_index": {
        "description": "Return counts and top chats for a date period.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["date_from", "date_to"],
            "additionalProperties": False,
        },
        "handler": tool_get_period_index,
    },
    "get_org_structure": {
        "description": "Return departments and users with managers and department memberships.",
        "inputSchema": {
            "type": "object",
            "properties": {"include_inactive": {"type": "boolean"}},
            "additionalProperties": False,
        },
        "handler": tool_get_org_structure,
    },
    "search_tasks": {
        "description": "Search Bitrix tasks by period, text, and responsible user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "query": {"type": "string"},
                "responsible_bitrix_user_id": {"type": "integer"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": tool_search_tasks,
    },
    "list_chats": {
        "description": "List active non-excluded chats, optionally with message counts for a period.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": tool_list_chats,
    },
    "search_messages": {
        "description": "Search raw chat messages and OCR text from attached images for a period. Returns original message text plus file OCR transcripts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "query": {"type": "string"},
                "dialog_id": {"type": "string"},
                "include_ocr": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["date_from", "date_to"],
            "additionalProperties": False,
        },
        "handler": tool_search_messages,
    },
    "get_chat_transcript": {
        "description": "Get raw chat transcript messages by dialog_id and period, including OCR transcripts for attached images and PDFs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dialog_id": {"type": "string"},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "include_ocr": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["dialog_id", "date_from", "date_to"],
            "additionalProperties": False,
        },
        "handler": tool_get_chat_transcript,
    },
    "get_chat_ocr_status": {
        "description": "Check whether image/PDF attachments for one chat/date already have OCR text before generating a daily report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dialog_id": {"type": "string"},
                "report_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["dialog_id", "report_date"],
            "additionalProperties": False,
        },
        "handler": tool_get_chat_ocr_status,
    },
    "process_chat_ocr": {
        "description": "Run OCR processing for image/PDF chat attachments through the local app workflow. Use when get_chat_ocr_status shows missing/pending OCR before a daily chat report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "dialog_id": {"type": "string"},
                "force": {"type": "boolean"},
            },
            "required": ["date_from"],
            "additionalProperties": False,
        },
        "handler": tool_process_chat_ocr,
    },
    "list_zoom_calls": {
        "description": "List Zoom cloud recordings/calls with dates, technical topics, participants, and transcript segment counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": tool_list_zoom_calls,
    },
    "get_zoom_call_transcript": {
        "description": "Get one Zoom call with factual Zoom participants and raw transcript segments. For Zoom reports, combine with get_org_structure to map participants and mentioned people to roles/departments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "call_id": {"type": "string"},
                "zoom_uuid": {"type": "string"},
                "include_full_text": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": tool_get_zoom_call_transcript,
    },
    "search_zoom_transcripts": {
        "description": "Search Zoom transcript segments by text and optional date range. For daily chat reports, search keywords derived from chat OCR, tasks, risks, project names, and owner names.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": tool_search_zoom_transcripts,
    },
    "save_zoom_call_report": {
        "description": "Save a generated AI report for one Zoom call directly to zoom_calls.analytical_note in PostgreSQL. Standalone Zoom reports must include factual participants, mentioned people mapped to org structure, strict task ownership/deadline gaps, and behavioral factors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "call_id": {"type": "string", "description": "Zoom call id from list_zoom_calls/get_zoom_call_transcript."},
                "zoom_uuid": {"type": "string", "description": "Zoom UUID, alternative to call_id."},
                "summary": {"type": "string"},
                "report_text": {"type": "string", "description": "Human-readable Zoom report to store in zoom_calls.analytical_note."},
                "analysis": {"type": "object", "description": "Structured Zoom report JSON from the zoom_processing prompt."},
                "model": {"type": "string"},
                "status": {"type": "string", "enum": ["done", "error"]},
                "raw_input": {"type": "object", "description": "Source manifest: transcript ids/segments, prompt id, checked context."},
            },
            "additionalProperties": False,
        },
        "handler": tool_save_zoom_call_report,
    },
    "delete_zoom_call_report": {
        "description": "Delete only the AI report for one Zoom call from zoom_calls.analytical_note and raw_json.ai_report. Does not delete the Zoom call, participants, or transcript.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "call_id": {"type": "string", "description": "Zoom call id from list_zoom_calls/get_zoom_call_transcript."},
                "zoom_uuid": {"type": "string", "description": "Zoom UUID, alternative to call_id."},
            },
            "additionalProperties": False,
        },
        "handler": tool_delete_zoom_call_report,
    },
    "get_chat_daily_report": {
        "description": "Read the current daily AI report for one chat/date, including structured report items when requested.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dialog_id": {"type": "string"},
                "report_date": {"type": "string", "description": "YYYY-MM-DD"},
                "include_items": {"type": "boolean"},
            },
            "required": ["dialog_id", "report_date"],
            "additionalProperties": False,
        },
        "handler": tool_get_chat_daily_report,
    },
    "get_owner_reports": {
        "description": "Read recent current owner daily or weekly reports. Use before recommendations and management answers to understand prior context, done/open items, and repeated issues.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_kind": {"type": "string", "enum": ["daily", "weekly"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        "handler": tool_get_owner_reports,
    },
    "get_previous_owner_daily_context": {
        "description": "Read only the previous calendar day's current owner daily report as continuity context for creating or checking a new owner daily report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_date": {"type": "string", "description": "Target owner daily report date in YYYY-MM-DD. The tool reads report_date minus one day."},
            },
            "required": ["report_date"],
            "additionalProperties": False,
        },
        "handler": tool_get_previous_owner_daily_context,
    },
    "save_chat_daily_report": {
        "description": "Save a generated daily report for one chat/date directly to PostgreSQL. Use after the MCP agent has analyzed chat OCR text, same-day Zoom reports, and previous-day report using the active chat_analysis report contract.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dialog_id": {"type": "string"},
                "report_date": {"type": "string", "description": "YYYY-MM-DD"},
                "summary": {"type": "string"},
                "report_text": {"type": "string"},
                "analysis": {"type": "object", "description": "Structured report JSON with previous_day_tasks, commitments, results, risks, etc."},
                "model": {"type": "string"},
                "status": {"type": "string", "enum": ["done", "no_data", "error"]},
                "risks_summary": {"type": "string"},
                "decisions_summary": {"type": "string"},
                "raw_input": {"type": "object"},
            },
            "required": ["dialog_id", "report_date"],
            "additionalProperties": False,
        },
        "handler": tool_save_chat_daily_report,
    },
    "save_owner_daily_report": {
        "description": "Save a generated daily owner report directly to owner_daily_reports. Use for the general daily owner report, not for per-chat reports.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_date": {"type": "string", "description": "YYYY-MM-DD"},
                "summary": {"type": "string"},
                "dynamics_summary": {"type": "string"},
                "risks_summary": {"type": "string"},
                "recommendations": {"type": "string"},
                "manager_recommendations": {"type": ["array", "object", "string"]},
                "manager_messages": {"type": ["array", "object", "string"]},
                "open_tasks": {"type": ["array", "object", "string"]},
                "overdue_tasks": {"type": ["array", "object", "string"]},
                "no_response": {"type": ["array", "object", "string"]},
                "goal_dynamics": {"type": ["array", "object", "string"]},
                "report_text": {"type": "string"},
                "raw_json": {"type": "object"},
                "analysis": {"type": "object"},
                "raw_input": {"type": "object"},
                "model": {"type": "string"},
                "status": {"type": "string", "enum": ["done", "no_data", "error"]},
            },
            "required": ["report_date"],
            "additionalProperties": False,
        },
        "handler": tool_save_owner_daily_report,
    },
    "save_owner_weekly_report": {
        "description": "Save a generated weekly owner report directly to owner_weekly_reports. Use for the general weekly owner report, not for per-chat reports.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "period_start": {"type": "string", "description": "YYYY-MM-DD"},
                "period_end": {"type": "string", "description": "YYYY-MM-DD"},
                "summary": {"type": "string"},
                "dynamics_summary": {"type": "string"},
                "risks_summary": {"type": "string"},
                "recommendations": {"type": "string"},
                "manager_recommendations": {"type": ["array", "object", "string"]},
                "manager_messages": {"type": ["array", "object", "string"]},
                "open_tasks": {"type": ["array", "object", "string"]},
                "overdue_tasks": {"type": ["array", "object", "string"]},
                "no_response": {"type": ["array", "object", "string"]},
                "goal_dynamics": {"type": ["array", "object", "string"]},
                "weekly_dynamics": {"type": ["array", "object", "string"]},
                "daily_owner_reports_manifest": {"type": ["array", "object", "string"]},
                "report_text": {"type": "string"},
                "raw_json": {"type": "object"},
                "analysis": {"type": "object"},
                "raw_input": {"type": "object"},
                "model": {"type": "string"},
                "status": {"type": "string", "enum": ["done", "no_data", "error"]},
            },
            "required": ["period_start", "period_end"],
            "additionalProperties": False,
        },
        "handler": tool_save_owner_weekly_report,
    },
    "get_chat_weekly_report": {
        "description": "Read the current weekly AI report for one chat and period.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dialog_id": {"type": "string"},
                "period_start": {"type": "string", "description": "YYYY-MM-DD"},
                "period_end": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["dialog_id", "period_start", "period_end"],
            "additionalProperties": False,
        },
        "handler": tool_get_chat_weekly_report,
    },
    "save_chat_weekly_report": {
        "description": "Save a generated weekly report for one chat directly to PostgreSQL. Use after the MCP agent has verified/generates missing daily reports, then analyzed the week using the chat_weekly_report report contract.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dialog_id": {"type": "string"},
                "period_start": {"type": "string", "description": "YYYY-MM-DD"},
                "period_end": {"type": "string", "description": "YYYY-MM-DD"},
                "summary": {"type": "string"},
                "report_text": {"type": "string"},
                "analysis": {"type": "object", "description": "Structured weekly report JSON from the chat_weekly_report report contract."},
                "model": {"type": "string"},
                "raw_input": {"type": "object"},
            },
            "required": ["dialog_id", "period_start", "period_end"],
            "additionalProperties": False,
        },
        "handler": tool_save_chat_weekly_report,
    },
    "upsert_ai_instruction": {
        "description": "Create or update one editable AI instruction folder by path in Настройки -> Инструкции для ИИ.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder path separated by /, for example: Формирование отчетов/Ежедневный отчет по чату"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "handler": tool_upsert_ai_instruction,
    },
    "get_compact_export": {
        "description": "Generate a compact export bundle for a period from live PostgreSQL data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "include_messages": {"type": "boolean"},
                "include_zoom_calls": {"type": "boolean"},
                "message_limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "task_limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "zoom_limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            },
            "required": ["date_from", "date_to"],
            "additionalProperties": False,
        },
        "handler": tool_get_compact_export,
    },
    "refresh_bitrix_context": {
        "description": "Refresh Bitrix-backed context now: team, tasks created in the period, unfinished task statuses, and chats for the period. Use before high-accuracy employee analytics when fresh Bitrix data is needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD; defaults to today minus 7 days"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD; defaults to today"},
            },
            "additionalProperties": False,
        },
        "handler": tool_refresh_bitrix_context,
    },
}


FAQ_TOOL_NAMES: set[str] = {
    "start_here_always_read_ai_instructions",
    "health",
    "get_context_guide",
    "get_ai_instructions",
    "list_available_sources",
    "get_company_profile",
    "list_company_files",
    "get_company_file",
    "search_company_knowledge",
    "get_org_structure",
    "list_zoom_calls",
    "get_zoom_call_transcript",
    "search_zoom_transcripts",
}


def _allowed_tools(tool_names: set[str] | None = None) -> dict[str, dict[str, Any]]:
    if tool_names is None:
        return TOOLS
    return {name: TOOLS[name] for name in tool_names if name in TOOLS}


def list_tools(tool_names: set[str] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": (
                spec["description"]
                if name == "start_here_always_read_ai_instructions"
                else TOOL_USAGE_CONTRACT + spec["description"]
            ),
            "inputSchema": spec["inputSchema"],
        }
        for name, spec in _allowed_tools(tool_names).items()
    ]


def handle_request(request: dict[str, Any], tool_names: set[str] | None = None) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    available_tools = _allowed_tools(tool_names)

    if method == "notifications/initialized":
        return None

    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "tools/list":
            result = {"tools": list_tools(tool_names)}
        elif method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name not in available_tools:
                raise McpError(-32601, f"Unknown or unavailable tool: {name}")
            result = text_response(available_tools[name]["handler"](args))
        else:
            raise McpError(-32601, f"Unknown method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except McpError as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": exc.message}}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip().lstrip("\ufeff")
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, default=json_default) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

