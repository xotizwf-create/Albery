from __future__ import annotations

import json
import importlib
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import UUID

from psycopg.types.json import Jsonb

from shared.db import connect as pg_connection, load_env_value as shared_load_env_value, normalize_postgres_url as shared_normalize_postgres_url


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SERVER_NAME = "employee-analytics-context"
SERVER_VERSION = "0.8.2"
PROTOCOL_VERSION = "2024-11-05"
MAX_LIMIT = 500
ZOOM_TRANSCRIPT_MAX_LIMIT = 2000
TOOL_USAGE_CONTRACT = (
    "Call start_here_always_read_ai_instructions first; if scope is unclear, "
    "ask one short clarifying question before guessing. "
)
REFERENCE_CACHE_TTL_SECONDS = int(os.getenv("MCP_REFERENCE_CACHE_TTL_SECONDS", "60") or "60")
TOOL_LATENCY_LOG_MS = float(os.getenv("MCP_TOOL_LATENCY_LOG_MS", "250") or "250")
_TTL_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}


class McpError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def ttl_cache_get(key: tuple[Any, ...]) -> Any | None:
    cached = _TTL_CACHE.get(key)
    if not cached:
        return None
    expires_at, value = cached
    if expires_at < time.time():
        _TTL_CACHE.pop(key, None)
        return None
    return value


def ttl_cache_set(key: tuple[Any, ...], value: Any, ttl_seconds: int = REFERENCE_CACHE_TTL_SECONDS) -> Any:
    _TTL_CACHE[key] = (time.time() + ttl_seconds, value)
    return value


def ttl_cache_delete_prefix(prefix: tuple[Any, ...]) -> None:
    for key in list(_TTL_CACHE):
        if key[: len(prefix)] == prefix:
            _TTL_CACHE.pop(key, None)


def load_database_url() -> str:
    value = shared_load_env_value("DATABASE_URL")
    if not value:
        raise McpError(-32000, "DATABASE_URL is not set in environment or .env")
    return normalize_postgres_url(value)


def load_env_value(key: str) -> str:
    return shared_load_env_value(key)


def normalize_postgres_url(database_url: str) -> str:
    return shared_normalize_postgres_url(database_url)


def connect() -> Any:
    try:
        return pg_connection()
    except RuntimeError as exc:
        raise McpError(-32000, str(exc)) from exc


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


def app_workflow_function(name: str) -> Any:
    try:
        app_module = importlib.import_module("app")
    except Exception as exc:  # noqa: BLE001
        raise McpError(-32000, f"Cannot load local app workflow module: {exc}") from exc
    workflow = getattr(app_module, name, None)
    if not callable(workflow):
        raise McpError(-32000, f"Local app workflow is not available: {name}")
    return workflow


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


def parse_limit(args: dict[str, Any], default: int = 100, max_limit: int = MAX_LIMIT) -> int:
    raw = args.get("limit", default)
    try:
        limit = int(raw)
    except (TypeError, ValueError) as exc:
        raise McpError(-32602, "limit must be an integer") from exc
    return max(1, min(limit, max_limit))


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


def tool_get_runtime_status(_: dict[str, Any]) -> dict[str, Any]:
    url = load_database_url()
    parsed = urlparse(url)
    safe_url = urlunparse((parsed.scheme, parsed.netloc.split("@")[-1], parsed.path, "", "", ""))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS database, current_schema() AS schema, now() AS server_time")
            row = cur.fetchone()
    return {
        "mode": "mcp_first_postgresql_only",
        "database": row["database"],
        "schema": row["schema"],
        "database_url": safe_url,
        "server_time": row["server_time"],
        "legacy_http_api_enabled": os.getenv("ALLOW_LEGACY_HTTP_API", "").strip() == "1",
        "path_token_auth_enabled": os.getenv("MCP_ALLOW_PATH_TOKEN", "").strip() == "1",
        "reference_cache_ttl_seconds": REFERENCE_CACHE_TTL_SECONDS,
        "rules": [
            "Use MCP tools as the primary interface for AI agents.",
            "Read and write company data through PostgreSQL-backed MCP tools.",
            "Do not depend on /api/* HTTP routes for MCP workflows.",
        ],
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
        "owner_daily_reports",
        "owner_weekly_reports",
        "owner_manager_recommendations",
        "owner_recommendation_dispatches",
        "owner_recommendation_events",
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


def load_ai_instructions(path_prefix: str | None = None) -> list[dict[str, Any]]:
    """Live instruction folders from ai_instruction_folders.

    When ``path_prefix`` is given (case-insensitive), only folders whose full
    path starts with that prefix are returned, so the assistant can fetch a
    single instruction instead of the whole tree.
    """
    rows = _load_ai_instruction_rows()
    if not path_prefix:
        return rows
    needle = path_prefix.strip().lower()
    if not needle:
        return rows
    return [row for row in rows if str(row.get("path") or "").lower().startswith(needle)]


def load_ai_instruction_index() -> list[dict[str, Any]]:
    """Compact map of instruction folders without the heavy ``content`` body.

    Lets the assistant see which instructions exist and fetch only the relevant
    one via get_ai_instructions(path=...) instead of re-reading the full tree.
    """
    index: list[dict[str, Any]] = []
    for row in _load_ai_instruction_rows():
        content = row.get("content")
        index.append(
            {
                "id": row.get("id"),
                "path": row.get("path"),
                "name": row.get("name"),
                "content_chars": len(content) if isinstance(content, str) else 0,
                "updated_at": row.get("updated_at"),
            }
        )
    return index


def _load_ai_instruction_rows() -> list[dict[str, Any]]:
    cache_key = ("ai_instruction_rows",)
    cached = ttl_cache_get(cache_key)
    if cached is not None:
        return cached
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
            return ttl_cache_set(cache_key, cur.fetchall())


INTENT_SOURCE_MAP: dict[str, list[str]] = {
    "company_rule_question": ["company_knowledge", "organization"],
    "employee_period_question": ["organization", "bitrix_tasks", "bitrix_chats", "zoom_calls", "owner_reports"],
    "chat_event_question": ["bitrix_chats", "organization"],
    "bitrix_task_creation": ["bitrix_tasks", "organization"],
    "recommendation_answer": ["owner_reports", "company_knowledge", "bitrix_tasks", "bitrix_chats", "zoom_calls"],
    "owner_daily_report_creation": ["owner_reports", "bitrix_chats", "zoom_calls", "company_knowledge", "bitrix_tasks", "organization"],
    "owner_weekly_report_creation": ["owner_reports", "bitrix_chats", "zoom_calls", "company_knowledge", "bitrix_tasks", "organization"],
}

# Aliases so a loose intent string still routes to the right workflow.
INTENT_ALIASES: dict[str, str] = {
    "company_rule": "company_rule_question",
    "rules": "company_rule_question",
    "regulation": "company_rule_question",
    "employee": "employee_period_question",
    "period": "employee_period_question",
    "analytics": "employee_period_question",
    "chat": "chat_event_question",
    "chat_event": "chat_event_question",
    "create_task": "bitrix_task_creation",
    "bitrix_task": "bitrix_task_creation",
    "recommendation": "recommendation_answer",
    "advice": "recommendation_answer",
    "owner_daily": "owner_daily_report_creation",
    "company_daily_report": "owner_daily_report_creation",
    "owner_daily_report": "owner_daily_report_creation",
    "owner_weekly": "owner_weekly_report_creation",
    "owner_weekly_report": "owner_weekly_report_creation",
}


def resolve_intent(raw: str) -> str | None:
    key = (raw or "").strip().lower()
    if not key:
        return None
    if key in INTENT_SOURCE_MAP:
        return key
    return INTENT_ALIASES.get(key)


def tool_get_context_guide(args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    instructions_index = load_ai_instruction_index()
    full_guide = {
        "purpose": "Navigation guide for using this MCP server systematically instead of guessing where data lives.",
        "ai_instructions_index": instructions_index,
        "ai_instructions_note": (
            "Full live instruction text was already returned by start_here_always_read_ai_instructions. "
            "To re-read one folder by path, call get_ai_instructions(path='Folder / Subfolder'). "
            "Pass intent to get_context_guide to receive only the workflow and sources for the current task."
        ),
        "operating_rules": [
            "Before any substantive answer or data work, call start_here_always_read_ai_instructions and follow its live instructions exactly.",
            "For unfamiliar questions, call get_context_guide after start_here_always_read_ai_instructions, then list_available_sources if source freshness or row counts matter.",
            "Act as an internal company AI agent: answers must be based on company context, regulations, reports, Bitrix tasks, chats, Zoom, and live AI instructions, not generic advice.",
            "For company rules, regulations, document mirrors, and persistent business knowledge, use search_company_knowledge first.",
            "For recommendations, management advice, or owner-facing conclusions, read recent owner reports and raw chat transcripts/OCR before concluding what is done, open, overdue, or repeated.",
            "If the request is vague, ambiguous, underspecified, or can be interpreted in several ways, stop and ask a short clarifying question before using data tools or answering.",
            "Ask what exact date/period, chat, person, task, source, output format, target decision, or save/write action is needed. Do not guess missing scope.",
            "Always use concrete names and task titles. Do not write only 'task 318099' or 'Natalia task'; write 'task 318099: Сформировать реестр платежей' with responsible person, status, deadline, and source when available.",
            "For employee identity, managers, departments, and Bitrix user ids, use get_org_structure before person-specific filters.",
            "For a date period, call get_period_index before reading messages/tasks; it shows where data exists and which chats are active.",
            "For task status, ownership, deadlines, and Bitrix work items, use search_tasks; when a task row shows comments_human_count > 0, read the actual discussion with get_task_comments(bitrix_task_id).",
            "For creating Bitrix tasks, use create_bitrix_task only when the user provided a task title, one responsible person, and a deadline. If any of these are missing, ask for the missing field instead of creating the task.",
            "For deleting Bitrix tasks, first identify exactly one bitrix_task_id, show the user the task title/status/responsible/deadline, and ask for explicit confirmation. Only after the user confirms deletion, call delete_bitrix_task with confirm=true.",
            "For discussion evidence, commitments, blockers, decisions, and OCR from chat images, use list_chats then search_messages or get_chat_transcript.",
            "For meeting evidence, use list_zoom_calls first, then search_zoom_transcripts with topic keywords, then get_zoom_call_transcript for matching calls, and get_org_structure before generating a Zoom report.",
            "For cross-source reports over a bounded date range, use get_compact_export, then deepen with source-specific tools.",
            "This project is MCP-first and PostgreSQL-only: do not call or depend on legacy /api/* HTTP routes for AI workflows.",
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
                "tools": ["search_tasks", "get_task_comments", "create_bitrix_task", "delete_bitrix_task"],
                "tables": ["bitrix_tasks", "bitrix_task_members", "bitrix_task_snapshots"],
                "use_for": ["task ownership", "deadlines", "statuses", "overdue work", "responsibility", "task discussion and comments", "creating Bitrix tasks with required title/responsible/deadline", "deleting Bitrix tasks only after exact id lookup and explicit confirmation"],
            },
            "bitrix_chats": {
                "tools": ["get_report_readiness", "list_chats", "search_messages", "get_chat_transcript", "get_chat_ocr_status", "process_chat_ocr"],
                "tables": ["chats", "chat_messages", "chat_message_files", "chat_file_ocr"],
                "use_for": ["conversation evidence", "commitments", "decisions", "questions", "OCR from screenshots", "raw chat transcript retrieval"],
                "rules": [
                    "Daily chat reports are disabled. Do not create, request, or wait for chat_daily_reports.",
                    "Use get_chat_transcript with include_ocr=true after OCR is ready.",
                    "process_chat_ocr calls the local PostgreSQL workflow directly from MCP; it does not require a local HTTP API route.",
                ],
            },
            "owner_reports": {
                "tools": ["get_report_readiness", "get_previous_owner_daily_context", "get_owner_reports", "save_owner_daily_report", "save_owner_weekly_report", "list_recommendations", "get_recommendation_feedback_context", "save_recommendation_event"],
                "tables": ["owner_daily_reports", "owner_weekly_reports", "owner_manager_recommendations", "owner_recommendation_dispatches", "owner_recommendation_events"],
                "use_for": ["recent owner context", "general daily reports for owner", "general weekly reports for owner", "owner-level report storage", "recommendation lifecycle", "recommendation feedback and statuses"],
            },
            "zoom_calls": {
                "tools": ["list_zoom_calls", "get_zoom_call_transcript", "search_zoom_transcripts", "save_zoom_call_report", "delete_zoom_call_report"],
                "tables": ["zoom_calls", "zoom_call_participants", "zoom_call_transcript_segments"],
                "use_for": ["meeting transcripts", "call participants from Zoom API", "mentioned people in transcript", "spoken decisions", "facts of task execution", "standalone Zoom report storage"],
                "rules": [
                    "For standalone Zoom reports, always include factual participants, mentioned people, a strict task block with owner/deadline/success-criteria gaps, and behavioral factors.",
                    "For chat context, Zoom relevance is based on transcript content, participants, and topics from chat/OCR/tasks, not only call title.",
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
            "bitrix_task_creation": [
                "Verify the user provided title, responsible person, and deadline.",
                "If responsible person is a name, create_bitrix_task resolves it through org structure; if ambiguous, ask for responsible_bitrix_user_id.",
                "Call create_bitrix_task(title, responsible_name/responsible_bitrix_user_id, deadline, description).",
                "Return created task_id, responsible, deadline, and title.",
                "For deletion: search_tasks(bitrix_task_id=...) first, show the exact task title/status/responsible/deadline, ask the user to confirm deletion, then call delete_bitrix_task(bitrix_task_id=..., confirm=true). Never delete by name, search text, or ambiguous reference.",
            ],
            "recommendation_answer": [
                "instructions already arrived from start_here_always_read_ai_instructions; re-read a specific folder only if needed via get_ai_instructions(path=...)",
                "search_company_knowledge(query) for company rules and regulations",
                "get_owner_reports(report_kind='daily', limit=7)",
                "get_owner_reports(report_kind='weekly', limit=4)",
                "get_period_index(date_from,date_to) for the requested period",
                "list_recommendations(status='open', date_from,date_to) and recommendation events for feedback continuity",
                "search_tasks/search_messages/search_zoom_transcripts for concrete evidence",
                "answer with specific task titles, owners, statuses, deadlines, and sources; ask clarifying questions when evidence is missing",
            ],
            "owner_daily_report_creation": [
                "instructions already arrived from start_here_always_read_ai_instructions; re-read only if needed via get_ai_instructions(path='Формирование отчетов / Ежедневный отчет по компании')",
                "open the active AI prompt in Сводная аналитика / Настройка промтов / ежедневный общий отчет для собственника and follow it as the report contract",
                "get_report_readiness(date_from=report_date,date_to=report_date) ONCE to get, in a single call: active chats with messages/OCR readiness, same-day Zoom calls and which already have an analytical_note, and whether the previous owner daily report exists — use this instead of probing each chat/Zoom one by one",
                "read company regulations with search_company_knowledge before writing recommendations; compare Bitrix/Zoom/chat facts against regulated owners, process roles, payment calendar ownership, approval rules, meeting rhythm, and SLA",
                "for every active chat with messages, read get_chat_transcript(..., include_ocr=true); daily chat reports are disabled and must not be generated",
                "for every Zoom call in missing_zoom_reports from get_report_readiness, use zoom_call_report instructions and save_zoom_call_report before continuing",
                "if previous_owner_daily_report_exists is false in readiness, stop or create the missing previous day first; otherwise get_previous_owner_daily_context(report_date) for continuity",
                "read recommendation feedback from raw chat transcripts and recommendation event context; every addressable recommendation must start with a soft greeting that accounts for the recipient's previous reply, objection, delegation, unclear answer, or missing response",
                "for every addressable recommendation, explicitly use regulation comparison when relevant: if the actual owner/executor/deadline/process differs from company regulation, mention the regulated owner/process and propose delegation, confirmation, Bitrix fixation, or regulation update",
                "only after every needed raw chat transcript/OCR, every Zoom analytical report, and previous owner_daily_report are ready, create owner_daily_report",
                "if any required source is missing or failed, stop and return the missing chat/Zoom/OCR source list instead of writing owner_daily_report",
            ],
            "owner_weekly_report_creation": [
                "get_report_readiness(date_from=week_start,date_to=week_end) ONCE to see per-day readiness across the whole week (chats/Zoom/owner daily) in a single call before deepening",
                "for each day in the week, run owner_daily_report_creation until the daily chain is complete",
                "use raw chat transcripts/OCR for weekly chat context; daily chat reports are disabled",
                "create or refresh chat_overall_weekly_report",
                "only then create owner_weekly_report",
                "if any daily source chain is incomplete, stop and return the incomplete days and missing sources",
            ],
        },
    }

    intent = resolve_intent(str(args.get("intent") or ""))
    if intent is None:
        return full_guide

    # Task-scoped view: only the workflow and sources relevant to this intent,
    # so the assistant reads one route instead of the whole guide.
    source_keys = INTENT_SOURCE_MAP.get(intent, [])
    return {
        "purpose": full_guide["purpose"],
        "intent": intent,
        "operating_rules": full_guide["operating_rules"],
        "source_map": {key: full_guide["source_map"][key] for key in source_keys if key in full_guide["source_map"]},
        "workflow": full_guide["recommended_workflows"].get(intent, []),
        "ai_instructions_index": full_guide["ai_instructions_index"],
        "ai_instructions_note": full_guide["ai_instructions_note"],
        "note": "Scoped to the requested intent. Call get_context_guide without intent to see all workflows and sources.",
    }


def tool_start_here_always_read_ai_instructions(args: dict[str, Any]) -> dict[str, Any]:
    instructions = load_ai_instructions()
    available_tools = list(args.get("_connector_tools") or sorted(TOOLS.keys()))
    connector_id = args.get("_connector_id") or "full"
    is_faq = connector_id == "faq" or set(available_tools) == FAQ_TOOL_NAMES
    connector_label = "FAQ MCP (read-only)" if is_faq else "Full MCP"
    return {
        "mandatory_status": "READ_FIRST_AND_OBEY_EXACTLY",
        "purpose": "This is the mandatory entry tool for this MCP server. The assistant must read these live settings before any analysis, report, recommendation, database lookup plan, or final answer.",
        "source": "Настройки -> Инструкции для ИИ (table ai_instruction_folders)",
        "connector_scope": {
            "connector_id": connector_id,
            "connector_label": connector_label,
            "available_tools": sorted(available_tools),
            "rules": [
                "STEP 0 (before any planning): inspect this connector's available_tools list above. Treat it as the complete and only set of capabilities you have right now.",
                "Do not assume access to any tool, data source, table, file, integration, web search, or external service that is not in available_tools. Do not invoke or describe tools by name that are not in the list.",
                "Do not look outside this MCP connector for information: no general web knowledge, no other connectors, no prior conversations, no offline assumptions about what 'usually' exists.",
                "Plan the order of actions ONLY after Step 0: pick the tool from available_tools that matches the user's task, then follow live_ai_instructions and the execution_contract below.",
                "If the user asks for data that requires a tool not present in available_tools (for example Bitrix tasks/chats, OCR, report saving, or any source not exposed here), reply briefly in the user's language: 'Нет доступа к этой информации в текущем коннекторе.' Then list which tools you do have so the user can refine the request or switch connectors. Never guess or fabricate the missing data.",
                "If multiple tools could match, prefer the more specific one. If none match, follow the previous rule.",
                "Even when available_tools is small (FAQ connector), do not apologize, do not invent capabilities, and do not offer to do work you cannot do here.",
            ],
        },
        "live_ai_instructions": instructions,
        "execution_contract": [
            "Treat every non-empty instruction as binding for the current user request.",
            "Do exactly what the relevant instruction says: source order, report format, required checks, save/read workflow, and stopping conditions.",
            "If a specific report contract is required by the instructions, call get_report_contract for that category before generating the report.",
            "If instructions require missing source checks, OCR, Zoom analysis, previous reports, or Bitrix refresh, complete those checks before conclusions.",
            "If the request conflicts with these instructions, explain the conflict and ask the user to update Настройки -> Инструкции для ИИ or confirm a one-off exception.",
            "If the user request is vague, ambiguous, or missing the needed scope, ask one concise clarifying question first. Do not infer dates, chats, people, report type, or whether to save/write unless the user said it clearly.",
            "For Bitrix task creation, never call create_bitrix_task unless the user provided all three required fields: task title, exactly one responsible person, and a deadline. If any field is missing or the responsible person is ambiguous, ask for clarification and do not create anything.",
            "For Bitrix task deletion, never call delete_bitrix_task unless the user has already confirmed deletion of one exact bitrix_task_id after seeing its title/status/responsible/deadline.",
            "When instructions are incomplete for the task, continue with get_context_guide and the relevant source tools instead of guessing.",
            "If an instruction names a tool that is not in connector_scope.available_tools, skip that step and tell the user that the action requires a connector that exposes that tool. Do not pretend the step succeeded.",
        ],
        "next_tool_guidance": [
            "The live_ai_instructions above are the full text for this request; do not re-fetch them with get_ai_instructions unless you need a folder by path after the conversation grew long.",
            "Use get_context_guide(intent='owner_daily_report_creation'|'recommendation_answer'|...) to get only the workflow and sources for the current task instead of the whole guide.",
            "Use get_report_contract when generating configured reports.",
            "Use get_report_readiness(date_from,date_to) before building daily/weekly/owner reports to learn in one call what is missing.",
            "Use list_available_sources when freshness, availability, or row counts matter.",
        ],
    }

def tool_get_ai_instructions(args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    path = str(args.get("path") or "").strip()
    instructions = load_ai_instructions(path or None)
    note = "These instructions are loaded live from ai_instruction_folders. Edit them in the UI: Settings -> AI instructions."
    if path:
        note = (
            f"Filtered to folders whose path starts with '{path}'. "
            "Omit path to read the full tree, or use get_context_guide for the index of available paths."
        )
    return {
        "instructions": instructions,
        "path": path or None,
        "note": note,
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
            "Daily/weekly chat reports are disabled; use raw chat transcript tools instead."
        ),
    }


def tool_get_company_profile(_: dict[str, Any]) -> dict[str, Any]:
    cache_key = ("company_profile",)
    cached = ttl_cache_get(cache_key)
    if cached is not None:
        return cached
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
        return ttl_cache_set(cache_key, {"title": "О компании", "content": "", "folders": folders, "updated_at": None})
    return ttl_cache_set(cache_key, {
        "title": row["title"] or "О компании",
        "content": row["content"] or "",
        "folders": folders,
        "updated_at": row["updated_at"],
    })


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
            report_periods: list[dict[str, Any]] = []
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
        "chat_reports_enabled": False,
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
            "get_report_readiness",
        ],
    }


def tool_get_report_readiness(args: dict[str, Any]) -> dict[str, Any]:
    """One-call source-readiness check for report building.

    Daily chat reports are disabled. For each date in the range this reports
    which active chats have raw messages, which Zoom calls have an analytical_note,
    and whether the current and previous owner daily reports exist.
    """
    date_from = parse_date_arg(args, "date_from")
    date_to = parse_date_arg(args, "date_to", required=False) or date_from
    if date_to < date_from:
        raise McpError(-32602, "date_to must be greater than or equal to date_from")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.message_day::date AS day, c.id AS chat_id,
                       c.dialog_id, c.chat_title, count(m.id) AS messages_count
                FROM chat_messages m
                JOIN chats c ON c.id = m.chat_id
                WHERE c.is_excluded = FALSE AND m.message_day BETWEEN %s AND %s
                GROUP BY m.message_day::date, c.id, c.dialog_id, c.chat_title
                ORDER BY day, messages_count DESC, c.chat_title NULLS LAST
                """,
                (date_from, date_to),
            )
            message_rows = cur.fetchall()

            zoom_rows: list[dict[str, Any]] = []
            if safe_table_exists(cur, "zoom_calls"):
                cur.execute(
                    """
                    SELECT id AS call_id, call_date, topic, technical_topic,
                           (analytical_note IS NOT NULL AND length(btrim(analytical_note)) > 0) AS has_report
                    FROM zoom_calls
                    WHERE call_date BETWEEN %s AND %s
                    ORDER BY call_date, start_time_msk NULLS LAST
                    """,
                    (date_from, date_to),
                )
                zoom_rows = cur.fetchall()

            owner_dates: set[Any] = set()
            if safe_table_exists(cur, "owner_daily_reports"):
                cur.execute(
                    """
                    SELECT report_date
                    FROM owner_daily_reports
                    WHERE is_current = TRUE AND report_date BETWEEN %s AND %s
                    """,
                    (date_from - timedelta(days=1), date_to),
                )
                owner_dates = {row["report_date"] for row in cur.fetchall()}

    days: list[dict[str, Any]] = []
    total_zoom_missing = 0
    days_ready = 0
    current = date_from
    while current <= date_to:
        day_chats = [row for row in message_rows if row["day"] == current]
        chat_transcripts = [
            {
                "dialog_id": row["dialog_id"],
                "chat_title": row["chat_title"],
                "messages_count": row["messages_count"],
            }
            for row in day_chats
        ]
        day_zoom = [row for row in zoom_rows if row["call_date"] == current]
        missing_zoom = [
            {
                "call_id": str(row["call_id"]),
                "topic": row.get("topic") or row.get("technical_topic"),
            }
            for row in day_zoom
            if not row["has_report"]
        ]
        owner_exists = current in owner_dates
        prev_owner_exists = (current - timedelta(days=1)) in owner_dates
        ready_for_owner = not missing_zoom and prev_owner_exists

        total_zoom_missing += len(missing_zoom)
        if ready_for_owner:
            days_ready += 1

        days.append(
            {
                "date": current,
                "chats": {
                    "with_messages": len(day_chats),
                    "transcripts": chat_transcripts,
                    "daily_reports_enabled": False,
                },
                "zoom": {
                    "calls": len(day_zoom),
                    "reports_ready": len(day_zoom) - len(missing_zoom),
                    "missing_zoom_reports": missing_zoom,
                },
                "owner_daily_report_exists": owner_exists,
                "previous_owner_daily_report_exists": prev_owner_exists,
                "ready_for_owner_daily": ready_for_owner,
            }
        )
        current += timedelta(days=1)

    return {
        "period": {"date_from": date_from, "date_to": date_to},
        "days": days,
        "summary": {
            "days": len(days),
            "daily_chat_reports_enabled": False,
            "total_zoom_missing_reports": total_zoom_missing,
            "days_ready_for_owner_daily": days_ready,
        },
        "next_actions": [
            "Do not generate chat_daily_reports; they are disabled.",
            "Read raw chat context with get_chat_transcript(dialog_id,date_from,date_to,include_ocr=true).",
            "Generate a Zoom report only for the calls in missing_zoom_reports.",
            "Build owner_daily_report only for days where ready_for_owner_daily is true; otherwise close the missing sources first.",
        ],
    }


def tool_get_org_structure(args: dict[str, Any]) -> dict[str, Any]:
    include_inactive = bool(args.get("include_inactive", False))
    cache_key = ("org_structure", include_inactive)
    cached = ttl_cache_get(cache_key)
    if cached is not None:
        return cached
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
    return ttl_cache_set(cache_key, {"departments": departments, "users": users})


def _name_tokens(value: Any) -> list[str]:
    aliases = {
        "настя": "анастасия",
        "дима": "дмитрий",
        "саша": "александр",
        "женя": "евгений",
    }
    normalized = str(value or "").strip().lower().replace("ё", "е")
    return [aliases.get(token, token) for token in re.findall(r"[a-zа-я0-9]+", normalized, flags=re.IGNORECASE)]


def _person_names_match(left: Any, right: Any) -> bool:
    left_tokens = _name_tokens(left)
    right_tokens = _name_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    smaller, larger = (left_tokens, right_tokens) if len(left_tokens) <= len(right_tokens) else (right_tokens, left_tokens)
    return all(any(candidate.startswith(token) if len(token) == 1 else candidate == token for candidate in larger) for token in smaller)


def _resolve_active_bitrix_user(bitrix_user_id: Any = None, name: Any = None) -> dict[str, Any]:
    user_id = None
    if bitrix_user_id not in (None, ""):
        try:
            user_id = int(bitrix_user_id)
        except (TypeError, ValueError) as exc:
            raise McpError(-32602, "responsible_bitrix_user_id must be an integer.") from exc

    responsible_name = str(name or "").strip()
    with connect() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    """
                    SELECT bitrix_user_id, full_name, email, work_position, is_active
                    FROM users
                    WHERE bitrix_user_id = %s AND is_active = TRUE
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise McpError(-32602, f"Не найден активный сотрудник Bitrix с id {user_id}.")
                return dict(row)

            if not responsible_name:
                raise McpError(-32602, "Нужно указать исполнителя: responsible_name или responsible_bitrix_user_id.")

            cur.execute(
                """
                SELECT bitrix_user_id, full_name, email, work_position, is_active
                FROM users
                WHERE is_active = TRUE AND bitrix_user_id IS NOT NULL
                ORDER BY full_name
                """
            )
            rows = [dict(row) for row in cur.fetchall()]

    exact = [row for row in rows if str(row.get("full_name") or "").strip().lower() == responsible_name.lower()]
    matches = exact or [row for row in rows if _person_names_match(row.get("full_name"), responsible_name)]
    if not matches:
        raise McpError(-32602, f"Не удалось найти исполнителя в оргструктуре: {responsible_name}.")
    if len(matches) > 1:
        candidates = [
            {
                "bitrix_user_id": row.get("bitrix_user_id"),
                "full_name": row.get("full_name"),
                "work_position": row.get("work_position"),
            }
            for row in matches[:10]
        ]
        raise McpError(-32602, "Исполнитель найден неоднозначно. Укажите responsible_bitrix_user_id. Кандидаты: " + json.dumps(candidates, ensure_ascii=False))
    return matches[0]


def _resolve_active_bitrix_users(
    bitrix_user_ids: Any,
    names: Any,
    *,
    role_label: str,
    id_field: str,
    name_field: str,
) -> list[dict[str, Any]]:
    ids_input = bitrix_user_ids if isinstance(bitrix_user_ids, list) else []
    names_input = names if isinstance(names, list) else []
    if not ids_input and not names_input:
        return []

    parsed_ids: list[int] = []
    for raw in ids_input:
        if raw in (None, ""):
            continue
        try:
            parsed_ids.append(int(raw))
        except (TypeError, ValueError) as exc:
            raise McpError(-32602, f"{id_field}: каждый элемент должен быть integer.") from exc

    clean_names = [str(n).strip() for n in names_input if str(n or "").strip()]

    resolved: list[dict[str, Any]] = []
    seen: set[int] = set()

    with connect() as conn:
        with conn.cursor() as cur:
            for uid in parsed_ids:
                if uid in seen:
                    continue
                cur.execute(
                    """
                    SELECT bitrix_user_id, full_name, email, work_position, is_active
                    FROM users
                    WHERE bitrix_user_id = %s AND is_active = TRUE
                    LIMIT 1
                    """,
                    (uid,),
                )
                row = cur.fetchone()
                if not row:
                    raise McpError(
                        -32602,
                        f"{role_label} не найден: активный сотрудник Bitrix с id {uid} отсутствует.",
                    )
                seen.add(uid)
                resolved.append(dict(row))

            if clean_names:
                cur.execute(
                    """
                    SELECT bitrix_user_id, full_name, email, work_position, is_active
                    FROM users
                    WHERE is_active = TRUE AND bitrix_user_id IS NOT NULL
                    ORDER BY full_name
                    """
                )
                all_active = [dict(r) for r in cur.fetchall()]
                for name in clean_names:
                    exact = [
                        r for r in all_active
                        if str(r.get("full_name") or "").strip().lower() == name.lower()
                    ]
                    matches = exact or [
                        r for r in all_active if _person_names_match(r.get("full_name"), name)
                    ]
                    if not matches:
                        raise McpError(
                            -32602,
                            f"{role_label} не найден в оргструктуре: {name}.",
                        )
                    if len(matches) > 1:
                        candidates = [
                            {
                                "bitrix_user_id": r.get("bitrix_user_id"),
                                "full_name": r.get("full_name"),
                                "work_position": r.get("work_position"),
                            }
                            for r in matches[:10]
                        ]
                        raise McpError(
                            -32602,
                            f"{role_label} '{name}' найден неоднозначно. Уточните через {id_field}. Кандидаты: "
                            + json.dumps(candidates, ensure_ascii=False),
                        )
                    uid = int(matches[0]["bitrix_user_id"])
                    if uid in seen:
                        continue
                    seen.add(uid)
                    resolved.append(matches[0])

    return resolved


_BITRIX_WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def _build_bitrix_regular_parameters(periodic: dict[str, Any]) -> dict[str, Any]:
    type_raw = str(periodic.get("type") or "").strip().lower()
    if type_raw not in {"daily", "weekly", "monthly"}:
        raise McpError(-32602, "periodic.type must be one of: daily, weekly, monthly.")

    interval_raw = periodic.get("interval")
    if interval_raw in (None, ""):
        interval = 1
    else:
        try:
            interval = int(interval_raw)
        except (TypeError, ValueError) as exc:
            raise McpError(-32602, "periodic.interval must be a positive integer.") from exc
        if interval < 1:
            raise McpError(-32602, "periodic.interval must be >= 1.")

    params: dict[str, Any] = {"REPEAT_EVERY": interval}

    if type_raw == "daily":
        daily_mode = str(periodic.get("daily_mode") or "all").strip().lower()
        if daily_mode not in {"all", "workdays"}:
            raise McpError(-32602, "periodic.daily_mode must be 'all' or 'workdays'.")
        params["REPEAT_TYPE"] = "daily"
        params["DAILY_MODE"] = daily_mode
    elif type_raw == "weekly":
        weekdays_raw = periodic.get("weekdays")
        if not isinstance(weekdays_raw, list) or not weekdays_raw:
            raise McpError(
                -32602,
                "periodic.weekdays must be a non-empty list when type=weekly (e.g. ['MO','WE','FR']).",
            )
        normalized_weekdays: list[str] = []
        for code in weekdays_raw:
            code_str = str(code or "").strip().upper()
            if code_str not in _BITRIX_WEEKDAY_CODES:
                raise McpError(
                    -32602,
                    f"periodic.weekdays: '{code}' is not a valid weekday code. Use MO/TU/WE/TH/FR/SA/SU.",
                )
            if code_str not in normalized_weekdays:
                normalized_weekdays.append(code_str)
        params["REPEAT_TYPE"] = "weekly"
        params["REPEAT_WEEKDAYS"] = normalized_weekdays
    else:  # monthly
        dom_raw = periodic.get("day_of_month")
        if dom_raw in (None, ""):
            raise McpError(-32602, "periodic.day_of_month is required when type=monthly (1-31).")
        try:
            dom = int(dom_raw)
        except (TypeError, ValueError) as exc:
            raise McpError(-32602, "periodic.day_of_month must be an integer 1-31.") from exc
        if not 1 <= dom <= 31:
            raise McpError(-32602, "periodic.day_of_month must be between 1 and 31.")
        params["REPEAT_TYPE"] = "monthlydays"
        params["REPEAT_MONTHDAY"] = dom

    until_raw = str(periodic.get("until") or "").strip()
    if until_raw:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", until_raw):
            raise McpError(-32602, "periodic.until must be YYYY-MM-DD.")
        params["REPEAT_TILL"] = until_raw

    return params


def _normalize_bitrix_deadline(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise McpError(-32602, "Нужно указать крайний срок задачи: deadline.")
    if re.match(r"^\d{2}\.\d{2}\.\d{4}$", raw):
        parsed = datetime.strptime(raw, "%d.%m.%Y").date()
        return f"{parsed.isoformat()}T19:00:00+03:00"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return f"{raw}T19:00:00+03:00"
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw):
        return raw
    raise McpError(-32602, "deadline должен быть в формате YYYY-MM-DD, DD.MM.YYYY или ISO datetime.")


def _bitrix_call_with_fallback(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    workflow = app_workflow_function("bitrix_method_call")
    try:
        return workflow(method, payload, True)
    except ValueError as exc:
        raise McpError(-32000, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise McpError(-32010, f"Bitrix API call failed: {exc}") from exc


def tool_create_bitrix_task(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()
    if not title:
        raise McpError(-32602, "Нужно указать название задачи: title.")
    deadline = _normalize_bitrix_deadline(args.get("deadline"))
    responsible = _resolve_active_bitrix_user(args.get("responsible_bitrix_user_id"), args.get("responsible_name"))
    description = str(args.get("description") or "").strip() or title
    priority_raw = str(args.get("priority") or "normal").strip().lower()
    priority = 2 if priority_raw in {"high", "critical", "2", "важно", "высокий"} else 1

    auditors = _resolve_active_bitrix_users(
        args.get("auditor_bitrix_user_ids"),
        args.get("auditor_names"),
        role_label="Наблюдатель",
        id_field="auditor_bitrix_user_ids",
        name_field="auditor_names",
    )

    fields: dict[str, Any] = {
        "TITLE": title,
        "DESCRIPTION": description,
        "RESPONSIBLE_ID": int(responsible["bitrix_user_id"]),
        "DEADLINE": deadline,
        "PRIORITY": priority,
    }
    if auditors:
        fields["AUDITORS"] = [int(u["bitrix_user_id"]) for u in auditors]

    tags = args.get("tags")
    if isinstance(tags, list):
        clean_tags = [str(tag).strip() for tag in tags if str(tag or "").strip()]
        if clean_tags:
            fields["TAGS"] = clean_tags

    periodic_arg = args.get("periodic")
    is_periodic = False
    regular_parameters: dict[str, Any] | None = None
    if isinstance(periodic_arg, dict) and periodic_arg:
        regular_parameters = _build_bitrix_regular_parameters(periodic_arg)
        fields["IS_REGULAR"] = "Y"
        fields["REGULAR_PARAMETERS"] = regular_parameters
        is_periodic = True

    response = _bitrix_call_with_fallback("tasks.task.add", {"fields": fields})
    result = response.get("result") if isinstance(response, dict) else {}
    task_id = None
    if isinstance(result, dict):
        task = result.get("task") if isinstance(result.get("task"), dict) else {}
        task_id = task.get("id") or result.get("id")
    else:
        task_id = result
    return {
        "created": True,
        "task_id": task_id,
        "title": title,
        "description": description,
        "deadline": deadline,
        "responsible": {
            "bitrix_user_id": responsible.get("bitrix_user_id"),
            "full_name": responsible.get("full_name"),
            "work_position": responsible.get("work_position"),
        },
        "auditors": [
            {
                "bitrix_user_id": u.get("bitrix_user_id"),
                "full_name": u.get("full_name"),
                "work_position": u.get("work_position"),
            }
            for u in auditors
        ],
        "is_periodic": is_periodic,
        "regular_parameters": regular_parameters,
        "bitrix_response": response.get("result") if isinstance(response, dict) else response,
        "rule": "Task creation requires title, responsible_name/responsible_bitrix_user_id, and deadline. Missing or ambiguous data blocks creation.",
    }


def tool_delete_bitrix_task(args: dict[str, Any]) -> dict[str, Any]:
    raw_task_id = args.get("bitrix_task_id")
    if raw_task_id in (None, ""):
        raise McpError(-32602, "Нужно указать точный номер задачи: bitrix_task_id.")
    try:
        task_id = int(raw_task_id)
    except (TypeError, ValueError) as exc:
        raise McpError(-32602, "bitrix_task_id must be an integer.") from exc
    if task_id <= 0:
        raise McpError(-32602, "bitrix_task_id must be a positive integer.")

    confirmed = args.get("confirm") is True
    if not confirmed:
        raise McpError(
            -32602,
            "Удаление задачи требует явного подтверждения. Сначала покажите пользователю точную задачу "
            "(номер, название, статус, ответственный, дедлайн) и спросите подтверждение. После подтверждения "
            "повторите вызов с confirm=true.",
        )

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    t.bitrix_task_id,
                    t.title,
                    t.status,
                    t.status_name,
                    t.deadline_at,
                    ru.bitrix_user_id AS responsible_bitrix_user_id,
                    ru.full_name AS responsible_name
                FROM bitrix_tasks t
                LEFT JOIN users ru ON ru.id = t.responsible_id
                WHERE t.bitrix_task_id = %s
                LIMIT 1
                """,
                (task_id,),
            )
            row = cur.fetchone()
    if not row:
        raise McpError(
            -32602,
            f"Задача Bitrix {task_id} не найдена в локальном индексе. Сначала проверьте номер через search_tasks.",
        )
    task = dict(row)

    expected_title = str(args.get("expected_title") or "").strip()
    actual_title = str(task.get("title") or "").strip()
    if expected_title and expected_title.lower() != actual_title.lower():
        raise McpError(
            -32602,
            "expected_title не совпадает с найденной задачей. Удаление остановлено, чтобы не удалить не ту задачу.",
        )

    response = _bitrix_call_with_fallback("tasks.task.delete", {"taskId": task_id})
    return {
        "deleted": True,
        "task": {
            "bitrix_task_id": task.get("bitrix_task_id"),
            "title": task.get("title"),
            "status": task.get("status"),
            "status_name": task.get("status_name"),
            "deadline_at": task.get("deadline_at").isoformat() if hasattr(task.get("deadline_at"), "isoformat") else task.get("deadline_at"),
            "responsible_bitrix_user_id": task.get("responsible_bitrix_user_id"),
            "responsible_name": task.get("responsible_name"),
        },
        "bitrix_response": response.get("result") if isinstance(response, dict) else response,
        "rule": "Deletion requires exact bitrix_task_id and confirm=true after the user has seen the exact task and explicitly confirmed deletion.",
    }


# --- Bitrix task comments -------------------------------------------------
# Task comments are stored inside bitrix_tasks.raw_json -> 'comments' -> 'items'
# as Bitrix IM messages. Human comments have a real author_id (> 0) and empty
# params; auto-generated notifications use author_id 0 or carry an ATTACH card.

_BB_BR_RE = re.compile(r"\[BR\]", re.IGNORECASE)
_BB_USER_RE = re.compile(r"\[USER=\d+\]\s*(.*?)\s*\[/USER\]", re.IGNORECASE | re.DOTALL)
_BB_URL_NAMED_RE = re.compile(r"\[URL=[^\]]+\](.*?)\[/URL\]", re.IGNORECASE | re.DOTALL)
_BB_URL_PLAIN_RE = re.compile(r"\[URL\](.*?)\[/URL\]", re.IGNORECASE | re.DOTALL)
_BB_TIMESTAMP_RE = re.compile(r"\[TIMESTAMP=[^\]]+\]", re.IGNORECASE)
_BB_DISK_RE = re.compile(r"\[DISK\s+FILE\s+ID=[^\]]+\]", re.IGNORECASE)
_BB_BULLET_RE = re.compile(r"\[\*\]")
# Only strip known Bitrix BB tags so that user-typed brackets like "[важно]" survive.
_BB_KNOWN_TAG_RE = re.compile(
    r"\[/?(?:B|I|U|S|BR|LIST|URL|USER|IMG|QUOTE|CODE|TABLE|TR|TD|P|SIZE|COLOR|"
    r"FONT|DISK|RATING|PROGRESS|TIMESTAMP|ATTACH|SPOILER|ANCHOR|H[1-6])"
    r"(?:[ =][^\]]*)?\]",
    re.IGNORECASE,
)
_WS_INLINE_RE = re.compile(r"[ \t]+")
_WS_NEWLINE_RE = re.compile(r"\n{3,}")


def clean_bitrix_text(text: Any) -> str:
    """Strip Bitrix BB-codes, keeping readable text (mentions, link labels)."""
    if not text:
        return ""
    s = str(text)
    s = _BB_BR_RE.sub("\n", s)
    s = _BB_USER_RE.sub(lambda m: m.group(1) or "", s)
    s = _BB_URL_NAMED_RE.sub(lambda m: m.group(1) or "", s)
    s = _BB_URL_PLAIN_RE.sub(lambda m: m.group(1) or "", s)
    s = _BB_TIMESTAMP_RE.sub("", s)
    s = _BB_DISK_RE.sub("", s)
    s = _BB_BULLET_RE.sub("\n• ", s)
    s = _BB_KNOWN_TAG_RE.sub("", s)
    s = _WS_INLINE_RE.sub(" ", s)
    s = _WS_NEWLINE_RE.sub("\n\n", s)
    return s.strip()


def comment_author_id(item: dict[str, Any]) -> int | None:
    raw = item.get("author_id", item.get("AUTHOR_ID"))
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_service_task_comment(item: dict[str, Any]) -> bool:
    """True for auto-generated task notifications / status cards, not human text."""
    if not isinstance(item, dict):
        return True
    if not comment_author_id(item):  # author_id 0 / missing => system message
        return True
    params = item.get("params")
    if isinstance(params, dict) and ("ATTACH" in params or "ATTACH_ID" in params):
        return True
    return False


def normalize_task_comment(item: dict[str, Any], names_by_id: dict[int, str]) -> dict[str, Any]:
    author_id = comment_author_id(item)
    raw_text = item.get("text") or item.get("MESSAGE") or item.get("POST_MESSAGE") or ""
    return {
        "comment_id": item.get("id") or item.get("ID"),
        "author_bitrix_user_id": author_id,
        "author_name": names_by_id.get(author_id) if author_id else None,
        "created_at": item.get("date") or item.get("POST_DATE"),
        "is_service": is_service_task_comment(item),
        "text": clean_bitrix_text(raw_text),
    }


TASK_DESCRIPTION_PREVIEW_CHARS = 500


def tool_search_tasks(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from", required=False)
    date_to = parse_date_arg(args, "date_to", required=False)
    query = str(args.get("query") or "").strip()
    responsible_bitrix_user_id = args.get("responsible_bitrix_user_id")
    bitrix_task_id = args.get("bitrix_task_id")
    include_full_description = bool(args.get("include_full_description", False))
    limit = parse_limit(args)
    offset = parse_offset(args)

    filters = []
    params: list[Any] = []
    # Direct id lookup uses the UNIQUE index on bitrix_task_id and is instant.
    # When an id is given, ignore other filters so a known task is always found.
    if bitrix_task_id not in (None, ""):
        try:
            filters.append("t.bitrix_task_id = %s")
            params.append(int(bitrix_task_id))
        except (TypeError, ValueError) as exc:
            raise McpError(-32602, "bitrix_task_id must be an integer") from exc
    else:
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

    if include_full_description:
        description_cols = "t.description, length(t.description) AS description_full_length, FALSE AS description_truncated"
    else:
        description_cols = (
            f"left(t.description, {TASK_DESCRIPTION_PREVIEW_CHARS}) AS description, "
            "length(t.description) AS description_full_length, "
            f"COALESCE(length(t.description) > {TASK_DESCRIPTION_PREVIEW_CHARS}, FALSE) AS description_truncated"
        )

    where_sql = "WHERE " + " AND ".join(filters) if filters else ""
    params.extend([limit, offset])
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    t.bitrix_task_id, t.title, {description_cols}, t.status, t.status_name,
                    t.priority, t.created_at_bitrix, t.updated_at_bitrix, t.deadline_at,
                    t.closed_at_bitrix,
                    cu.bitrix_user_id AS creator_bitrix_user_id,
                    cu.full_name AS creator_name,
                    ru.bitrix_user_id AS responsible_bitrix_user_id,
                    ru.full_name AS responsible_name,
                    COALESCE(jsonb_array_length(
                        CASE WHEN jsonb_typeof(t.raw_json->'comments'->'items') = 'array'
                             THEN t.raw_json->'comments'->'items' ELSE '[]'::jsonb END), 0)
                        AS comments_total_count,
                    (SELECT count(*) FROM jsonb_array_elements(
                        CASE WHEN jsonb_typeof(t.raw_json->'comments'->'items') = 'array'
                             THEN t.raw_json->'comments'->'items' ELSE '[]'::jsonb END) c
                     WHERE COALESCE(NULLIF(c->>'author_id', '')::bigint, 0) <> 0
                       AND NOT (jsonb_typeof(c->'params') = 'object' AND (c->'params') ? 'ATTACH'))
                        AS comments_human_count
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
    note = None
    if not include_full_description and any(row.get("description_truncated") for row in rows):
        note = (
            f"description is truncated to {TASK_DESCRIPTION_PREVIEW_CHARS} chars to keep the result small; "
            "see description_full_length. To read one task in full use "
            "search_tasks(bitrix_task_id=..., include_full_description=true)."
        )
    return {"items": rows, "limit": limit, "offset": offset, "note": note}


def tool_get_task_comments(args: dict[str, Any]) -> dict[str, Any]:
    raw_task_id = args.get("bitrix_task_id")
    if raw_task_id in (None, ""):
        raise McpError(-32602, "Missing required argument: bitrix_task_id")
    try:
        task_id = int(raw_task_id)
    except (TypeError, ValueError) as exc:
        raise McpError(-32602, "bitrix_task_id must be an integer") from exc

    include_service = bool(args.get("include_service", False))
    order = str(args.get("order") or "asc").lower()
    if order not in ("asc", "desc"):
        order = "asc"
    limit = parse_limit(args, default=50)
    offset = parse_offset(args)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    t.bitrix_task_id, t.title, t.status, t.status_name,
                    t.raw_json->'comments'->'items'   AS items,
                    t.raw_json->'comments'->>'chat_id' AS chat_id,
                    cu.full_name AS creator_name,
                    ru.full_name AS responsible_name
                FROM bitrix_tasks t
                LEFT JOIN users cu ON cu.id = t.creator_id
                LEFT JOIN users ru ON ru.id = t.responsible_id
                WHERE t.bitrix_task_id = %s
                """,
                (task_id,),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "bitrix_task_id": task_id,
                    "found": False,
                    "items": [],
                    "note": "Task not found in bitrix_tasks. Sync it first or check the id.",
                }

            items = row["items"] if isinstance(row["items"], list) else []
            author_ids: set[int] = set()
            for it in items:
                if isinstance(it, dict):
                    aid = comment_author_id(it)
                    if aid:
                        author_ids.add(aid)

            names_by_id: dict[int, str] = {}
            if author_ids:
                cur.execute(
                    "SELECT bitrix_user_id, full_name FROM users WHERE bitrix_user_id = ANY(%s)",
                    (list(author_ids),),
                )
                for r in cur.fetchall():
                    if r["bitrix_user_id"] is not None:
                        names_by_id[int(r["bitrix_user_id"])] = r["full_name"]

    normalized = [normalize_task_comment(it, names_by_id) for it in items if isinstance(it, dict)]
    human = [c for c in normalized if not c["is_service"]]
    pool = normalized if include_service else human
    pool.sort(key=lambda c: c.get("created_at") or "", reverse=(order == "desc"))
    page = pool[offset : offset + limit]

    return {
        "bitrix_task_id": row["bitrix_task_id"],
        "found": True,
        "title": row["title"],
        "status": row["status_name"] or row["status"],
        "creator_name": row["creator_name"],
        "responsible_name": row["responsible_name"],
        "chat_id": row["chat_id"],
        "total_comments": len(normalized),
        "human_comments": len(human),
        "service_comments": len(normalized) - len(human),
        "include_service": include_service,
        "order": order,
        "limit": limit,
        "offset": offset,
        "returned": len(page),
        "items": page,
    }


def tool_list_chats(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from", required=False)
    date_to = parse_date_arg(args, "date_to", required=False)
    query = str(args.get("query") or "").strip()
    limit = parse_limit(args)
    offset = parse_offset(args)

    join_params: list[Any] = []
    filter_params: list[Any] = []
    filters = ["c.is_excluded = FALSE"]
    if query:
        filters.append(
            """
            (
                c.chat_title ILIKE %s
                OR c.dialog_id ILIKE %s
                OR EXISTS (
                    SELECT 1
                    FROM chat_members cmq
                    JOIN users uq ON uq.id = cmq.user_id
                    WHERE cmq.chat_id = c.id
                      AND (
                          uq.full_name ILIKE %s
                          OR uq.email ILIKE %s
                          OR uq.work_position ILIKE %s
                          OR uq.bitrix_user_id::text = %s
                      )
                )
            )
            """
        )
        like = f"%{query}%"
        filter_params.extend([like, like, like, like, like, query])
    message_join = ""
    message_select = "0 AS period_messages_count"
    if date_from and date_to:
        message_join = """
            LEFT JOIN chat_messages m
                ON m.chat_id = c.id AND m.message_day BETWEEN %s AND %s
        """
        join_params.extend([date_from, date_to])
        message_select = "count(m.id) AS period_messages_count"

    params = [*join_params, *filter_params, limit, offset]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    c.dialog_id, c.bitrix_chat_id, c.chat_title, c.chat_type,
                    c.members_count, c.last_message_at,
                    {message_select},
                    COALESCE(
                        (
                            SELECT jsonb_agg(
                                jsonb_build_object(
                                    'bitrix_user_id', u.bitrix_user_id,
                                    'full_name', u.full_name,
                                    'work_position', u.work_position
                                )
                                ORDER BY u.full_name
                            )
                            FROM chat_members cm
                            JOIN users u ON u.id = cm.user_id
                            WHERE cm.chat_id = c.id
                        ),
                        '[]'::jsonb
                    ) AS members
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

    try:
        process_chat_image_ocr_for_period = app_workflow_function("process_chat_image_ocr_for_period")
        return process_chat_image_ocr_for_period(date_from, date_to, force=force, dialog_id=dialog_id or None)
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
                    array_remove(array_agg(DISTINCT COALESCE(zcp.participant_name, zcp.participant_email)), NULL) AS participants_raw,
                    (
                        SELECT array_remove(array_agg(DISTINCT s.speaker), NULL)
                        FROM zoom_call_transcript_segments s
                        WHERE s.call_id = zc.id
                    ) AS speakers
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
    # Prefer real transcript speaker names over shared technical Zoom account names.
    resolver = app_workflow_function("resolve_zoom_participants")
    for row in rows:
        resolved = resolver(
            [{"name": name} for name in (row.get("participants_raw") or [])],
            row.get("speakers") or [],
        )
        row["participants"] = [p["name"] for p in resolved if p.get("name")]
    return {"items": rows, "limit": limit, "offset": offset}


def tool_get_zoom_call_transcript(args: dict[str, Any]) -> dict[str, Any]:
    call_id = str(args.get("call_id") or "").strip()
    zoom_uuid = str(args.get("zoom_uuid") or "").strip()
    if not call_id and not zoom_uuid:
        raise McpError(-32602, "Missing required argument: call_id or zoom_uuid")
    include_full_text = bool(args.get("include_full_text", True))
    limit = parse_limit(args, 500, ZOOM_TRANSCRIPT_MAX_LIMIT)
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
            # All distinct transcript speakers (not just the current page) so shared
            # Zoom accounts ("Координатор") resolve to the real renamed names.
            cur.execute(
                "SELECT DISTINCT speaker FROM zoom_call_transcript_segments "
                "WHERE call_id = %s AND speaker IS NOT NULL",
                (call["id"],),
            )
            all_speakers = [row["speaker"] for row in cur.fetchall()]

    resolver = app_workflow_function("resolve_zoom_participants")
    resolved_participants = resolver(
        [
            {"name": p.get("participant_name"), "email": p.get("participant_email")}
            for p in participants
        ],
        all_speakers,
    )

    call_payload = dict(call)
    call_payload.pop("raw_json", None)
    if not include_full_text:
        call_payload.pop("transcript_text", None)
    return {
        "call": call_payload,
        "participants": resolved_participants,
        "participants_raw": participants,
        "segments": segments,
        "total_segments": total_segments,
        "limit": limit,
        "offset": offset,
    }


def tool_export_zoom_call_markdown(args: dict[str, Any]) -> dict[str, Any]:
    call_id = str(args.get("call_id") or "").strip()
    if not call_id:
        raise McpError(-32602, "Missing required argument: call_id")
    workflow = app_workflow_function("export_zoom_call_markdown")
    try:
        return json_safe(workflow(call_id))
    except ValueError as exc:
        raise McpError(-32602, str(exc)) from exc


def tool_export_zoom_transcripts_markdown(args: dict[str, Any]) -> dict[str, Any]:
    raw_ids = args.get("call_ids")
    if raw_ids is not None and not isinstance(raw_ids, list):
        raise McpError(-32602, "call_ids must be an array of Zoom call ids.")
    call_ids = [str(value).strip() for value in (raw_ids or []) if str(value).strip()] or None
    date_from = parse_date_arg(args, "date_from", required=False)
    date_to = parse_date_arg(args, "date_to", required=False)
    include_gd = bool(args.get("include_google_drive", False))
    if not call_ids and not date_from and not date_to:
        raise McpError(-32602, "Provide call_ids, or date_from/date_to.")
    workflow = app_workflow_function("export_zoom_calls_markdown")
    try:
        result = workflow(
            call_ids=call_ids,
            date_from=date_from.isoformat() if date_from else None,
            date_to=date_to.isoformat() if date_to else None,
            include_google_drive=include_gd,
        )
    except ValueError as exc:
        raise McpError(-32602, str(exc)) from exc
    return json_safe(result)


def _first_text_value(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _sentence_case_ru(value: Any) -> str:
    text = str(value or "").strip().rstrip(".")
    return text[:1].upper() + text[1:] if text else text


def _split_zoom_operational_task_items(section: str) -> list[str]:
    text = str(section or "").strip()
    if not text:
        return []
    items: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        markers = list(re.finditer(r"(?:^|\s)(\d+)[).]\s+", line))
        if len(markers) <= 1:
            items.append(line)
            continue
        for index, marker in enumerate(markers):
            start = marker.start()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(line)
            item = line[start:end].strip()
            if item:
                items.append(item)
    return items


def _extract_zoom_labeled_parts(text: str) -> tuple[str, dict[str, str]]:
    label_pattern = re.compile(r"(Срок|Критерий(?:\s+результата)?|Статус|Источник)\s*:", re.IGNORECASE)
    matches = list(label_pattern.finditer(text))
    if not matches:
        return text.strip().strip(". "), {}
    unlabeled = text[:matches[0].start()].strip().strip(". ")
    labels: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1).lower().replace(" ", "_")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        labels[key] = text[match.end():end].strip().strip(". ")
    return unlabeled, labels


def _parse_zoom_operational_task_line(line: str, fallback_number: int) -> dict[str, Any] | None:
    text = str(line or "").strip()
    if not text:
        return None
    match = re.match(r"^\s*(\d+)[.)]\s*(.*)$", text, re.DOTALL)
    if match:
        number = int(match.group(1)) if match.group(1).isdigit() else fallback_number
        body = match.group(2).strip()
    else:
        number = fallback_number
        body = text
    assignee_name = ""
    strict_assignee = re.match(r"^Ответственный:\s*(.*?)\.\s*(.*)$", body, re.IGNORECASE | re.DOTALL)
    if strict_assignee:
        assignee_name = strict_assignee.group(1).strip()
        body = strict_assignee.group(2).strip()
    elif "—" in body:
        assignee_name, body = [part.strip() for part in body.split("—", 1)]
    elif " - " in body:
        assignee_name, body = [part.strip() for part in body.split(" - ", 1)]

    body = re.sub(r"^Задача:\s*", "", body, flags=re.IGNORECASE).strip()
    task_text, labels = _extract_zoom_labeled_parts(body)
    if not task_text:
        return None
    return {
        "number": number,
        "assignee_name": _first_text_value(assignee_name, "Требует назначения"),
        "bitrix_user_id": None,
        "task_text": _sentence_case_ru(task_text),
        "deadline_text": _first_text_value(labels.get("срок"), "срок не указан").rstrip("."),
        "result_criteria": _first_text_value(labels.get("критерий_результата"), labels.get("критерий")).rstrip("."),
        "status": _first_text_value(labels.get("статус"), "planned").rstrip("."),
        "source": _first_text_value(labels.get("источник"), "").rstrip("."),
        "raw": {"source_line": text},
    }


def _extract_zoom_operational_tasks_section(report_text: str) -> str:
    lines = str(report_text or "").strip().splitlines()
    collecting = False
    section_lines: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not collecting:
            if re.match(r"^\s*(?:4[.)]|IV[.)]?)\s*\**\s*Операционные задачи", line, re.IGNORECASE):
                collecting = True
            continue
        if re.match(
            r"^\s*(?:[5-9][.)]|1[0-2][.)]|V[.)]?|VI[.)]?|VII[.)]?|VIII[.)]?|IX[.)]?)\s+\**\s*"
            r"(?:Поведенческие|Риски|Проблемы|Блокеры|Решения|Итоги|Вывод|Следующие|Контроль|Рекомендации)",
            line,
            re.IGNORECASE,
        ):
            break
        section_lines.append(raw.rstrip())
    return "\n".join(section_lines).strip()


def normalize_zoom_operational_tasks_for_raw_json(report_text: str, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    source_items: list[Any] = []
    if isinstance(analysis, dict):
        for key in ("operational_tasks", "tasks"):
            value = analysis.get(key)
            if isinstance(value, list) and value:
                source_items = value
                break
    for index, item in enumerate(source_items, start=1):
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence_times = [
            str(evidence_item.get("time") or "").strip()
            for evidence_item in evidence
            if isinstance(evidence_item, dict) and str(evidence_item.get("time") or "").strip()
        ]
        task_text = _first_text_value(item.get("task_text"), item.get("task"), item.get("action"), item.get("text"))
        if not task_text:
            continue
        tasks.append({
            "number": int(item.get("number") or index) if str(item.get("number") or index).isdigit() else index,
            "assignee_name": _first_text_value(
                item.get("assignee_name"),
                item.get("responsible"),
                item.get("responsible_name"),
                item.get("person_name"),
                item.get("org_person"),
                item.get("display_owner"),
                item.get("owner"),
                "Требует назначения",
            ),
            "bitrix_user_id": item.get("bitrix_user_id") or item.get("user_id"),
            "task_text": _sentence_case_ru(task_text),
            "deadline_text": _first_text_value(item.get("deadline_text"), item.get("deadline"), "срок не указан").rstrip("."),
            "result_criteria": _first_text_value(
                item.get("result_criteria"),
                item.get("success_criteria"),
                item.get("criteria"),
                item.get("criterion"),
            ).rstrip("."),
            "status": _first_text_value(item.get("status"), "planned"),
            "source": _first_text_value(item.get("source"), item.get("timecode"), ", ".join(evidence_times)).rstrip("."),
            "raw": item,
        })
    section = _extract_zoom_operational_tasks_section(report_text)
    section_tasks: list[dict[str, Any]] = []
    for raw in _split_zoom_operational_task_items(section):
        parsed = _parse_zoom_operational_task_line(raw, len(section_tasks) + 1)
        if parsed:
            section_tasks.append(parsed)
    if section_tasks and len(section_tasks) > len(tasks):
        return section_tasks
    return tasks or section_tasks


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

    operational_tasks = normalize_zoom_operational_tasks_for_raw_json(report_text, analysis)
    report_payload = {
        "source": "mcp_save_zoom_call_report",
        "summary": summary,
        "report_text": report_text,
        "operational_tasks": operational_tasks,
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
    "recommendation_feedback": "recommendation_feedback",
    "strange_feedback": "strange_feedback",
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


# --- restored from HEAD (audit cleanup collateral): owner reports + recommendations ---
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


RECOMMENDATION_STATUSES = {
    "new",
    "draft",
    "queued",
    "sent",
    "seen",
    "acked",
    "accepted",
    "in_progress",
    "needs_clarification",
    "disagreed",
    "delegated",
    "done",
    "rejected",
    "no_response",
    "overdue",
    "requires_manager_review",
    "cancelled",
    "error",
}

OPEN_RECOMMENDATION_STATUSES = {
    "new",
    "draft",
    "queued",
    "sent",
    "seen",
    "acked",
    "accepted",
    "in_progress",
    "needs_clarification",
    "disagreed",
    "delegated",
    "no_response",
    "overdue",
    "requires_manager_review",
    "error",
}


def tool_list_recommendations(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from", required=False)
    date_to = parse_date_arg(args, "date_to", required=False)
    status = str(args.get("status") or "").strip()
    dialog_id = str(args.get("dialog_id") or "").strip()
    include_events = bool(args.get("include_events", True))
    manager_bitrix_user_id = args.get("manager_bitrix_user_id")
    employee_bitrix_user_id = args.get("employee_bitrix_user_id")
    limit = parse_limit(args, 100)
    offset = parse_offset(args)

    filters = ["1=1"]
    params: list[Any] = []
    if date_from and date_to:
        filters.append("COALESCE(r.report_date, r.period_start, r.created_at::date) BETWEEN %s AND %s")
        params.extend([date_from, date_to])
    elif date_from or date_to:
        raise McpError(-32602, "date_from and date_to must be provided together")
    if status:
        if status == "open":
            filters.append("r.status = ANY(%s::text[])")
            params.append(sorted(OPEN_RECOMMENDATION_STATUSES))
        else:
            if status not in RECOMMENDATION_STATUSES:
                raise McpError(-32602, f"Unknown recommendation status: {status}")
            filters.append("r.status = %s")
            params.append(status)
    if dialog_id:
        filters.append("(sc.dialog_id = %s OR fc.dialog_id = %s OR r.feedback_dialog_id = %s)")
        params.extend([dialog_id, dialog_id, dialog_id])
    if manager_bitrix_user_id not in (None, ""):
        filters.append("r.manager_bitrix_user_id = %s")
        params.append(int(manager_bitrix_user_id))
    if employee_bitrix_user_id not in (None, ""):
        filters.append("r.employee_bitrix_user_id = %s")
        params.append(int(employee_bitrix_user_id))

    params.extend([limit, offset])
    with connect() as conn:
        with conn.cursor() as cur:
            if not safe_table_exists(cur, "owner_manager_recommendations"):
                return {"items": [], "events": [], "message": "Recommendation table does not exist."}
            cur.execute(
                f"""
                SELECT
                    r.id, r.source_scope, r.report_date, r.period_start, r.period_end,
                    r.status, r.priority, r.recommendation_type, r.subject,
                    r.recommendation_text, r.expected_action, r.due_date,
                    r.response_due_at, r.execution_due_at, r.sent_at, r.last_response_at,
                    r.manager_review_required, r.current_interpretation,
                    mu.full_name AS manager_name,
                    r.manager_bitrix_user_id,
                    eu.full_name AS employee_name,
                    r.employee_bitrix_user_id,
                    sc.dialog_id AS source_dialog_id,
                    sc.chat_title AS source_chat_title,
                    fc.dialog_id AS feedback_dialog_id,
                    fc.chat_title AS feedback_chat_title,
                    r.source_payload, r.created_at, r.updated_at
                FROM owner_manager_recommendations r
                LEFT JOIN users mu ON mu.id = r.manager_user_id
                LEFT JOIN users eu ON eu.id = r.employee_user_id
                LEFT JOIN chats sc ON sc.id = r.source_chat_id
                LEFT JOIN chats fc ON fc.id = r.feedback_chat_id
                WHERE {' AND '.join(filters)}
                ORDER BY r.manager_review_required DESC, r.updated_at DESC, r.created_at DESC
                LIMIT %s OFFSET %s
                """,
                params,
            )
            items = cur.fetchall()
            events: list[dict[str, Any]] = []
            if include_events and items and safe_table_exists(cur, "owner_recommendation_events"):
                ids = [str(item["id"]) for item in items]
                cur.execute(
                    """
                    SELECT
                        e.recommendation_id, e.event_type, e.author_type,
                        u.full_name AS author_name, e.author_bitrix_user_id,
                        e.dialog_id, c.chat_title, e.bitrix_message_id,
                        e.chat_message_day, e.old_status, e.new_status,
                        e.event_text, e.interpretation, e.source_payload, e.event_at
                    FROM owner_recommendation_events e
                    LEFT JOIN users u ON u.id = e.author_user_id
                    LEFT JOIN chats c ON c.id = e.chat_id
                    WHERE e.recommendation_id = ANY(%s::uuid[])
                    ORDER BY e.event_at DESC
                    LIMIT 500
                    """,
                    (ids,),
                )
                events = cur.fetchall()
    return {
        "items": items,
        "events": events,
        "limit": limit,
        "offset": offset,
        "rule": "Use recommendation events as the lifecycle log; do not infer final status from chat text without recording an event.",
    }


def tool_get_recommendation_feedback_context(args: dict[str, Any]) -> dict[str, Any]:
    dialog_id = str(args.get("dialog_id") or "").strip()
    if not dialog_id:
        raise McpError(-32602, "Missing required argument: dialog_id")
    report_date = parse_date_arg(args, "report_date")
    previous_date = report_date - timedelta(days=1)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, dialog_id, chat_title FROM chats WHERE dialog_id = %s", (dialog_id,))
            chat = cur.fetchone()
            if not chat:
                raise McpError(-32602, f"Unknown dialog_id: {dialog_id}")
            cur.execute(
                "SELECT user_id FROM chat_members WHERE chat_id = %s AND is_active = TRUE",
                (chat["id"],),
            )
            member_ids = [str(row["user_id"]) for row in cur.fetchall()]
            member_clause = "FALSE"
            member_params: list[Any] = []
            if member_ids:
                member_clause = "(r.manager_user_id = ANY(%s::uuid[]) OR r.employee_user_id = ANY(%s::uuid[]))"
                member_params = [member_ids, member_ids]
            cur.execute(
                f"""
                SELECT
                    r.id, r.status, r.priority, r.recommendation_type, r.subject,
                    r.recommendation_text, r.expected_action, r.due_date,
                    r.response_due_at, r.execution_due_at, r.sent_at, r.last_response_at,
                    r.manager_review_required, r.current_interpretation,
                    mu.full_name AS manager_name,
                    eu.full_name AS employee_name,
                    sc.dialog_id AS source_dialog_id,
                    fc.dialog_id AS feedback_dialog_id
                FROM owner_manager_recommendations r
                LEFT JOIN users mu ON mu.id = r.manager_user_id
                LEFT JOIN users eu ON eu.id = r.employee_user_id
                LEFT JOIN chats sc ON sc.id = r.source_chat_id
                LEFT JOIN chats fc ON fc.id = r.feedback_chat_id
                WHERE (
                    r.source_chat_id = %s
                    OR r.feedback_chat_id = %s
                    OR COALESCE(r.feedback_dialog_id, '') = %s
                    OR {member_clause}
                )
                  AND (
                    r.status = ANY(%s::text[])
                    OR r.created_at::date BETWEEN %s AND %s
                    OR r.sent_at::date BETWEEN %s AND %s
                    OR r.last_response_at::date BETWEEN %s AND %s
                  )
                ORDER BY r.manager_review_required DESC, r.updated_at DESC
                LIMIT 100
                """,
                [
                    chat["id"],
                    chat["id"],
                    dialog_id,
                    *member_params,
                    sorted(OPEN_RECOMMENDATION_STATUSES),
                    previous_date,
                    report_date,
                    previous_date,
                    report_date,
                    previous_date,
                    report_date,
                ],
            )
            recommendations = cur.fetchall()
            events: list[dict[str, Any]] = []
            if recommendations and safe_table_exists(cur, "owner_recommendation_events"):
                ids = [str(item["id"]) for item in recommendations]
                cur.execute(
                    """
                    SELECT recommendation_id, event_type, author_type, dialog_id,
                           bitrix_message_id, chat_message_day, old_status, new_status,
                           event_text, interpretation, source_payload, event_at
                    FROM owner_recommendation_events
                    WHERE recommendation_id = ANY(%s::uuid[])
                    ORDER BY event_at DESC
                    LIMIT 300
                    """,
                    (ids,),
                )
                events = cur.fetchall()
    return {
        "dialog_id": dialog_id,
        "report_date": report_date,
        "period_to_check": {"date_from": previous_date, "date_to": report_date},
        "recommendations": recommendations,
        "events": events,
        "rule": (
            "For daily chat reports, compare previous-day and current-day messages against these recommendations. "
            "Unclear, evasive, contradictory, or off-topic replies must be marked as 'Странная обратная связь' "
            "and saved with status requires_manager_review or needs_clarification."
        ),
    }


def tool_save_recommendation_event(args: dict[str, Any]) -> dict[str, Any]:
    recommendation_id = str(args.get("recommendation_id") or "").strip()
    if not recommendation_id:
        raise McpError(-32602, "Missing required argument: recommendation_id")
    try:
        recommendation_uuid = UUID(recommendation_id)
    except ValueError as exc:
        raise McpError(-32602, "recommendation_id must be a UUID") from exc
    event_type = str(args.get("event_type") or "ai_interpreted").strip()
    if event_type not in {"created", "sent", "delivered", "seen", "employee_replied", "ai_interpreted", "status_changed", "manager_reviewed", "task_created", "closed", "source_found"}:
        raise McpError(-32602, f"Unknown event_type: {event_type}")
    author_type = str(args.get("author_type") or "ai").strip()
    if author_type not in {"system", "ai", "manager", "employee"}:
        raise McpError(-32602, f"Unknown author_type: {author_type}")
    new_status = str(args.get("new_status") or "").strip() or None
    if new_status and new_status not in RECOMMENDATION_STATUSES:
        raise McpError(-32602, f"Unknown recommendation status: {new_status}")
    event_text = str(args.get("event_text") or "").strip() or None
    interpretation = args.get("interpretation") if isinstance(args.get("interpretation"), dict) else {}
    source_payload = args.get("source_payload") if isinstance(args.get("source_payload"), dict) else {}
    dialog_id = str(args.get("dialog_id") or "").strip() or None
    bitrix_message_id = args.get("bitrix_message_id")
    chat_message_day = parse_date_arg(args, "chat_message_day", required=False)
    author_bitrix_user_id = args.get("author_bitrix_user_id")

    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT id, status FROM owner_manager_recommendations WHERE id = %s", (recommendation_uuid,))
                recommendation = cur.fetchone()
                if not recommendation:
                    raise McpError(-32602, f"Unknown recommendation_id: {recommendation_id}")
                chat_id = None
                if dialog_id:
                    cur.execute("SELECT id FROM chats WHERE dialog_id = %s", (dialog_id,))
                    chat = cur.fetchone()
                    chat_id = chat["id"] if chat else None
                old_status = str(args.get("old_status") or recommendation.get("status") or "new")
                cur.execute(
                    """
                    INSERT INTO owner_recommendation_events (
                        recommendation_id, event_type, author_type, author_bitrix_user_id,
                        chat_id, dialog_id, bitrix_message_id, chat_message_day,
                        old_status, new_status, event_text, interpretation, source_payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        recommendation_uuid,
                        event_type,
                        author_type,
                        int(author_bitrix_user_id) if author_bitrix_user_id not in (None, "") else None,
                        chat_id,
                        dialog_id,
                        int(bitrix_message_id) if bitrix_message_id not in (None, "") else None,
                        chat_message_day,
                        old_status,
                        new_status,
                        event_text,
                        jsonb_arg(interpretation),
                        jsonb_arg(source_payload),
                    ),
                )
                event_id = cur.fetchone()["id"]
                if new_status:
                    review_required = bool(args.get("manager_review_required")) or bool(interpretation.get("requires_manager_review")) or new_status == "requires_manager_review"
                    cur.execute(
                        """
                        UPDATE owner_manager_recommendations
                        SET status = %s,
                            feedback_chat_id = COALESCE(feedback_chat_id, %s),
                            feedback_dialog_id = COALESCE(feedback_dialog_id, %s),
                            current_interpretation = CASE WHEN %s::jsonb = '{}'::jsonb THEN current_interpretation ELSE %s::jsonb END,
                            manager_review_required = manager_review_required OR %s,
                            last_response_at = CASE
                                WHEN %s IN ('accepted','in_progress','needs_clarification','disagreed','delegated','done','requires_manager_review')
                                THEN now()
                                ELSE last_response_at
                            END,
                            closed_at = CASE WHEN %s IN ('done','rejected','cancelled') THEN COALESCE(closed_at, now()) ELSE closed_at END,
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (
                            new_status,
                            chat_id,
                            dialog_id,
                            jsonb_arg(interpretation),
                            jsonb_arg(interpretation),
                            review_required,
                            new_status,
                            new_status,
                            recommendation_uuid,
                        ),
                    )
    return {"event_id": str(event_id), "recommendation_id": recommendation_id, "new_status": new_status}


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
                analysis_for_messages = {
                    "manager_messages": args.get("manager_messages"),
                    "manager_recommendations": args.get("manager_recommendations"),
                }
                if analysis_for_messages["manager_messages"] or analysis_for_messages["manager_recommendations"]:
                    app_workflow_function("save_owner_daily_manager_messages")(
                        cur, report_id, report_date, analysis_for_messages
                    )
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


def tool_list_pending_zoom_operational_dispatches(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from", required=False)
    date_to = parse_date_arg(args, "date_to", required=False)
    workflow = app_workflow_function("list_pending_zoom_operational_dispatches")
    rows = workflow(date_from, date_to)
    return {"pending": json_safe(rows), "pending_count": len(rows)}


def tool_preview_zoom_operational_tasks(args: dict[str, Any]) -> dict[str, Any]:
    call_id = str(args.get("call_id") or "").strip()
    if not call_id:
        raise McpError(-32602, "Missing required argument: call_id")
    workflow = app_workflow_function("preview_zoom_operational_tasks")
    return json_safe(workflow(call_id))


def tool_dispatch_zoom_operational_tasks(args: dict[str, Any]) -> dict[str, Any]:
    if args.get("confirm") is not True:
        raise McpError(
            -32602,
            "Sending requires confirm=true. The owner must have explicitly approved creating aggregated 'Итоги созвона' "
            "Bitrix tasks for this zoom call (one task per responsible person, exactly like the Albery UI 'Отправка задач'). "
            "Only then call dispatch_zoom_operational_tasks with confirm=true.",
        )
    call_id = str(args.get("call_id") or "").strip()
    if not call_id:
        raise McpError(-32602, "Missing required argument: call_id")
    workflow = app_workflow_function("dispatch_zoom_operational_tasks")
    result = workflow(call_id)
    return json_safe(result)


def _resolve_current_owner_daily_report(cur: Any, report_date: date) -> dict[str, Any]:
    cur.execute(
        """
        SELECT id, version, summary, dynamics_summary, risks_summary, recommendations, report_text
        FROM owner_daily_reports
        WHERE report_date = %s AND is_current = TRUE
        LIMIT 1
        """,
        (report_date,),
    )
    row = cur.fetchone()
    if not row:
        raise McpError(-32602, f"Нет текущего ежедневного owner-отчёта за {report_date.isoformat()}.")
    return dict(row)


def tool_list_pending_owner_recommendations(args: dict[str, Any]) -> dict[str, Any]:
    report_date = parse_date_arg(args, "report_date")
    with connect() as conn:
        with conn.cursor() as cur:
            report = _resolve_current_owner_daily_report(cur, report_date)
            cur.execute(
                """
                SELECT r.id, r.manager_user_id, r.manager_bitrix_user_id,
                       u.full_name AS manager_full_name,
                       r.recommendation_text, r.subject, r.priority, r.due_date,
                       r.recommendation_type, r.status, r.created_at
                FROM owner_manager_recommendations r
                LEFT JOIN users u ON u.id = r.manager_user_id
                WHERE r.owner_daily_report_id = %s
                  AND r.manager_bitrix_user_id IS NOT NULL
                  AND r.status NOT IN ('sent','cancelled','done','rejected')
                ORDER BY
                  CASE r.priority
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    ELSE 4
                  END,
                  r.created_at
                """,
                (report["id"],),
            )
            rows = [json_safe(dict(row)) for row in cur.fetchall()]
    return {
        "report_id": str(report["id"]),
        "report_date": report_date.isoformat(),
        "report_summary": report.get("summary"),
        "report_dynamics_summary": report.get("dynamics_summary"),
        "report_risks_summary": report.get("risks_summary"),
        "report_text": report.get("report_text"),
        "recommendations": rows,
        "recommendations_count": len(rows),
    }


def tool_send_owner_recommendations_to_bitrix(args: dict[str, Any]) -> dict[str, Any]:
    if args.get("confirm") is not True:
        raise McpError(
            -32602,
            "Sending requires confirm=true. First show the owner the exact drafts (per recipient) and get explicit "
            "approval. Only then call send_owner_recommendations_to_bitrix with confirm=true.",
        )
    report_date = parse_date_arg(args, "report_date")
    recipient_recommendations = args.get("recipient_recommendations")
    if not isinstance(recipient_recommendations, dict) or not recipient_recommendations:
        raise McpError(
            -32602,
            "recipient_recommendations must be a non-empty object {bitrix_user_id: message_text}.",
        )
    normalized: dict[str, str] = {}
    for key, value in recipient_recommendations.items():
        user_id = to_int(key)
        text = str(value or "").strip()
        if user_id is None or not text:
            continue
        normalized[str(user_id)] = text
    if not normalized:
        raise McpError(-32602, "recipient_recommendations contains no valid (bitrix_user_id, text) entries.")
    recipient_ids = [int(k) for k in normalized.keys()]
    with connect() as conn:
        with conn.cursor() as cur:
            report = _resolve_current_owner_daily_report(cur, report_date)
    workflow = app_workflow_function("send_owner_report_recommendations_to_bitrix")
    result = workflow(str(report["id"]), "daily", recipient_ids, normalized)
    return {
        "report_id": str(report["id"]),
        "report_date": report_date.isoformat(),
        "sent": result.get("sent", 0),
        "failed": result.get("failed", 0),
        "results": json_safe(result.get("results") or []),
        "errors": json_safe(result.get("errors") or []),
    }


def tool_send_owner_weekly_report_pdf(args: dict[str, Any]) -> dict[str, Any]:
    if args.get("confirm") is not True:
        raise McpError(
            -32602,
            "Sending requires confirm=true. First save the weekly report via save_owner_weekly_report, show the owner "
            "the report period and that the PDF goes to Evgeniy, get explicit approval. Only then call "
            "send_owner_weekly_report_pdf with confirm=true.",
        )
    period_start = parse_date_arg(args, "period_start")
    period_end = parse_date_arg(args, "period_end")
    recipient_ids = args.get("recipient_bitrix_user_ids")
    if recipient_ids in (None, "", []):
        recipient_ids = [1]  # Evgeniy Palei by default
    if not isinstance(recipient_ids, list):
        recipient_ids = [recipient_ids]
    normalized_ids: list[int] = []
    for value in recipient_ids:
        user_id = to_int(value)
        if user_id is not None:
            normalized_ids.append(user_id)
    if not normalized_ids:
        raise McpError(-32602, "recipient_bitrix_user_ids contains no valid integer id.")
    loader = app_workflow_function("load_owner_weekly_report")
    report = loader(period_start, period_end)
    # load_owner_weekly_report returns the dict from owner_weekly_report_to_dict,
    # which exposes the primary key as "report_id" (not "id"). Accept both so the
    # lookup never falsely reports "not found" and re-triggers a save/retry loop.
    report_pk = (report.get("report_id") or report.get("id")) if report else None
    if not report_pk:
        raise McpError(
            -32004,
            f"Текущий недельный отчёт за {period_start.isoformat()}–{period_end.isoformat()} не найден. "
            "Сначала сохрани его через save_owner_weekly_report.",
        )
    report_id = str(report_pk)
    workflow = app_workflow_function("send_owner_report_pdf_to_bitrix")
    result = workflow(report_id, "weekly", normalized_ids)
    return {
        "report_id": report_id,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "recipient_bitrix_user_ids": normalized_ids,
        "filename": result.get("filename"),
        "pdf_size": result.get("pdf_size"),
        "sent": result.get("sent", 0),
        "failed": result.get("failed", 0),
        "results": json_safe(result.get("results") or []),
        "errors": json_safe(result.get("errors") or []),
    }


def _resolve_message_recipient(
    recipient_bitrix_user_id: Any = None,
    recipient_name: Any = None,
) -> dict[str, Any]:
    user_id = None
    if recipient_bitrix_user_id not in (None, ""):
        try:
            user_id = int(recipient_bitrix_user_id)
        except (TypeError, ValueError) as exc:
            raise McpError(-32602, "recipient_bitrix_user_id must be an integer.") from exc

    name_clean = str(recipient_name or "").strip()
    with connect() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    """
                    SELECT bitrix_user_id, full_name, email, work_position, is_active
                    FROM users
                    WHERE bitrix_user_id = %s AND is_active = TRUE
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise McpError(-32602, f"Не найден активный сотрудник Bitrix с id {user_id}.")
                return dict(row)

            if not name_clean:
                raise McpError(
                    -32602,
                    "Нужно указать получателя: recipient_name или recipient_bitrix_user_id.",
                )

            cur.execute(
                """
                SELECT bitrix_user_id, full_name, email, work_position, is_active
                FROM users
                WHERE is_active = TRUE AND bitrix_user_id IS NOT NULL
                ORDER BY full_name
                """
            )
            rows = [dict(row) for row in cur.fetchall()]

    exact = [row for row in rows if str(row.get("full_name") or "").strip().lower() == name_clean.lower()]
    matches = exact or [row for row in rows if _person_names_match(row.get("full_name"), name_clean)]
    if not matches:
        raise McpError(-32602, f"Не удалось найти получателя в оргструктуре: {name_clean}.")
    if len(matches) > 1:
        candidates = [
            {
                "bitrix_user_id": row.get("bitrix_user_id"),
                "full_name": row.get("full_name"),
                "work_position": row.get("work_position"),
            }
            for row in matches[:10]
        ]
        raise McpError(
            -32602,
            "Получатель найден неоднозначно. Уточните recipient_bitrix_user_id. Кандидаты: "
            + json.dumps(candidates, ensure_ascii=False),
        )
    return matches[0]


def tool_send_bitrix_message(args: dict[str, Any]) -> dict[str, Any]:
    if args.get("confirm") is not True:
        raise McpError(
            -32602,
            "Sending a Bitrix message requires confirm=true. First show the user the exact final message_text "
            "and the resolved recipient (full_name + work_position + bitrix_user_id) and get explicit approval. "
            "Only then call send_bitrix_message with confirm=true.",
        )
    message_text = str(args.get("message_text") or "").strip()
    if not message_text:
        raise McpError(-32602, "message_text must be a non-empty string.")
    if len(message_text) > 20000:
        raise McpError(-32602, "message_text is too long (max 20000 characters).")
    recipient = _resolve_message_recipient(
        args.get("recipient_bitrix_user_id"),
        args.get("recipient_name"),
    )
    workflow = app_workflow_function("send_bitrix_personal_message")
    try:
        result = workflow(int(recipient["bitrix_user_id"]), message_text)
    except ValueError as exc:
        raise McpError(-32602, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise McpError(-32010, f"Bitrix send failed: {exc}") from exc
    return {
        "sent": True,
        "recipient": {
            "bitrix_user_id": recipient.get("bitrix_user_id"),
            "full_name": recipient.get("full_name"),
            "work_position": recipient.get("work_position"),
        },
        "channel": result.get("channel"),
        "message_text": message_text,
        "bitrix_response": json_safe(result.get("response")),
        "im_message_error": result.get("im_message_error"),
    }


def tool_cancel_owner_recommendation(args: dict[str, Any]) -> dict[str, Any]:
    rec_id_raw = str(args.get("recommendation_id") or "").strip()
    if not rec_id_raw:
        raise McpError(-32602, "Missing required argument: recommendation_id")
    try:
        rec_uuid = UUID(rec_id_raw)
    except (TypeError, ValueError) as exc:
        raise McpError(-32602, "recommendation_id must be a UUID.") from exc
    reason = str(args.get("reason") or "").strip()
    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, status FROM owner_manager_recommendations WHERE id = %s",
                    (rec_uuid,),
                )
                row = cur.fetchone()
                if not row:
                    raise McpError(-32602, f"Recommendation {rec_id_raw} not found.")
                old_status = str(row.get("status") or "new")
                cur.execute(
                    """
                    UPDATE owner_manager_recommendations
                    SET status = 'cancelled', closed_at = COALESCE(closed_at, now()), updated_at = now()
                    WHERE id = %s
                    """,
                    (rec_uuid,),
                )
                cur.execute(
                    """
                    INSERT INTO owner_recommendation_events (
                        recommendation_id, event_type, author_type,
                        old_status, new_status, event_text, source_payload
                    )
                    VALUES (%s, 'cancelled', 'system', %s, 'cancelled', %s, %s)
                    """,
                    (rec_uuid, old_status, reason or "Cancelled by owner.", jsonb_arg({"reason": reason})),
                )
    return {
        "recommendation_id": rec_id_raw,
        "old_status": old_status,
        "new_status": "cancelled",
        "reason": reason or None,
    }


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
                    raise McpError(-32000, "ai_instruction_folders table is missing. Apply database migrations before writing AI instructions.")
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
    ttl_cache_delete_prefix(("ai_instruction_rows",))
    return {"folder": current, "path": " / ".join(path_parts)}


def _compact_owner_report(report: dict[str, Any]) -> dict[str, Any]:
    """Keep only the lightweight summary fields; drop heavy report_text/raw_json.

    The full text stays available through get_owner_reports and
    get_previous_owner_daily_context when the assistant actually needs it.
    """
    return {
        "id": report.get("id"),
        "report_date": report.get("report_date"),
        "period_start": report.get("period_start"),
        "period_end": report.get("period_end"),
        "version": report.get("version"),
        "generated_at": report.get("generated_at"),
        "summary": report.get("summary"),
        "dynamics_summary": report.get("dynamics_summary"),
        "risks_summary": report.get("risks_summary"),
        "recommendations": report.get("recommendations"),
    }


def _compact_company_profile() -> dict[str, Any]:
    """Profile header plus a folder index without the heavy document bodies.

    Full document text stays available through search_company_knowledge and
    get_company_file; bundling every mirrored doc here costs tens of thousands
    of tokens on every export.
    """
    profile = tool_get_company_profile({})
    folders = profile.get("folders") or []
    index = [
        {
            "id": folder.get("id"),
            "parent_id": folder.get("parent_id"),
            "name": folder.get("name"),
            "content_chars": len(folder.get("content") or ""),
            "updated_at": folder.get("updated_at"),
        }
        for folder in folders
    ]
    return {
        "title": profile.get("title"),
        "content": profile.get("content"),
        "updated_at": profile.get("updated_at"),
        "folders_index": index,
        "note": "Folder bodies omitted. Read a document with get_company_file(folder_id) or search with search_company_knowledge.",
    }


def tool_get_compact_export(args: dict[str, Any]) -> dict[str, Any]:
    date_from = parse_date_arg(args, "date_from")
    date_to = parse_date_arg(args, "date_to")
    include_messages = bool(args.get("include_messages", True))
    include_zoom_calls = bool(args.get("include_zoom_calls", True))
    message_limit = parse_limit({"limit": args.get("message_limit", 100)})
    zoom_limit = parse_limit({"limit": args.get("zoom_limit", 50)})

    return {
        "manifest": tool_get_period_index({"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}),
        "company_profile": _compact_company_profile(),
        "org": tool_get_org_structure({"include_inactive": False}),
        "tasks": tool_search_tasks(
            {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "limit": args.get("task_limit", 100)}
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
        "recent_owner_daily_reports": [
            _compact_owner_report(r) for r in tool_get_owner_reports({"report_kind": "daily", "limit": 5})["reports"]
        ],
        "recent_owner_weekly_reports": [
            _compact_owner_report(r) for r in tool_get_owner_reports({"report_kind": "weekly", "limit": 2})["reports"]
        ],
        "notes": [
            "This compact export is read-only and generated on demand from PostgreSQL.",
            "Task descriptions are previews; read one task in full via search_tasks(bitrix_task_id=..., include_full_description=true).",
            "Owner reports here are summaries only; read full report_text via get_owner_reports or get_previous_owner_daily_context when needed.",
            "Zoom calls are available via list_zoom_calls, get_zoom_call_transcript, and search_zoom_transcripts.",
        ],
    }


_GOOGLE_SHEETS_RE = re.compile(r"https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_\-]+)")
_GOOGLE_DOCS_RE = re.compile(r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_\-]+)")
_GOOGLE_GID_RE = re.compile(r"[?&#]gid=(\d+)")


def _rewrite_url_for_export(url: str) -> tuple[str, str]:
    m = _GOOGLE_SHEETS_RE.search(url)
    if m:
        sheet_id = m.group(1)
        gid_match = _GOOGLE_GID_RE.search(url)
        gid_part = f"&gid={gid_match.group(1)}" if gid_match else ""
        return (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv{gid_part}",
            "google_sheet_csv",
        )
    m = _GOOGLE_DOCS_RE.search(url)
    if m:
        doc_id = m.group(1)
        return (
            f"https://docs.google.com/document/d/{doc_id}/export?format=txt",
            "google_doc_text",
        )
    return (url, "raw")


def _strip_html_to_text(html: str) -> str:
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def tool_fetch_url(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url:
        raise McpError(-32602, "url is required.")
    if not re.match(r"^https?://", url):
        raise McpError(-32602, "url must start with http:// or https://.")
    max_chars_raw = args.get("max_chars")
    if max_chars_raw in (None, ""):
        max_chars = 50_000
    else:
        try:
            max_chars = int(max_chars_raw)
        except (TypeError, ValueError) as exc:
            raise McpError(-32602, "max_chars must be a positive integer.") from exc
        if max_chars < 100 or max_chars > 200_000:
            raise McpError(-32602, "max_chars must be between 100 and 200000.")
    strip_html_flag = bool(args.get("strip_html", True))

    fetched_url, kind = _rewrite_url_for_export(url)
    request = urllib.request.Request(
        fetched_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AlberyMCP/0.7 fetch_url)",
            "Accept": "text/csv,text/plain,text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "") or ""
            raw_bytes = response.read(max_chars * 6 + 4096)
            final_url = response.geturl() or fetched_url
    except urllib.error.HTTPError as exc:
        try:
            body_preview = exc.read().decode("utf-8", "replace")[:500]
        except Exception:  # noqa: BLE001
            body_preview = ""
        hint = ""
        if kind in {"google_sheet_csv", "google_doc_text"} and exc.code in (401, 403, 302, 401):
            hint = (
                "Похоже, документ Google закрыт. Откройте доступ «Любой, у кого есть ссылка — Просмотр» "
                "(Поделиться → изменить доступ → Любой, у кого есть ссылка), либо положите файл в Drive-папку, "
                "которую читает Albery через Apps Script, и используйте search_company_knowledge / list_company_files."
            )
        return {
            "ok": False,
            "original_url": url,
            "fetched_url": fetched_url,
            "kind": kind,
            "status": exc.code,
            "error": f"HTTP {exc.code}",
            "body_preview": body_preview,
            "hint": hint,
        }
    except Exception as exc:  # noqa: BLE001
        raise McpError(-32010, f"Fetch failed: {exc}") from exc

    charset_match = re.search(r"charset=([a-zA-Z0-9_\-]+)", content_type)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        text = raw_bytes.decode(charset, errors="replace")
    except LookupError:
        text = raw_bytes.decode("utf-8", errors="replace")

    looks_html = ("html" in content_type.lower()) or text.lstrip().lower().startswith(("<!doctype", "<html"))
    if strip_html_flag and looks_html and kind == "raw":
        text = _strip_html_to_text(text)

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]

    return {
        "ok": True,
        "original_url": url,
        "fetched_url": fetched_url,
        "final_url": final_url,
        "kind": kind,
        "status": status,
        "content_type": content_type,
        "char_count": len(text),
        "truncated": truncated,
        "text": text,
    }


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
    "get_runtime_status": {
        "description": "Inspect MCP-first/PostgreSQL-only runtime mode, database target, cache TTL, and whether legacy HTTP API compatibility is enabled.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_get_runtime_status,
    },
    "get_context_guide": {
        "description": "Read navigation rules after start_here_always_read_ai_instructions: where to search first, which tools map to which business sources, and how to avoid chaotic database exploration. Pass intent to get only the workflow and sources for the current task instead of the whole guide.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "Optional task route. One of: company_rule_question, employee_period_question, chat_event_question, bitrix_task_creation, recommendation_answer, owner_daily_report_creation, owner_weekly_report_creation. Returns only that workflow and its sources. Omit for the full guide.",
                },
            },
            "additionalProperties": False,
        },
        "handler": tool_get_context_guide,
    },
    "get_ai_instructions": {
        "description": "Read live editable AI behavior and answer-format instructions from Настройки -> Инструкции для ИИ. start_here_always_read_ai_instructions already returns the full text, so call this only to re-read one folder by path. Use get_context_guide for the index of available paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional folder path prefix, e.g. 'Формирование отчетов / Ежедневный отчет по компании'. Returns only matching folders. Omit to read the full tree.",
                },
            },
            "additionalProperties": False,
        },
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
        "description": "List recent dates available in chat messages and Zoom/owner sources.",
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
    "get_report_readiness": {
        "description": (
            "Report-building readiness for a date range in one call: per day, which active chats have "
            "messages and which already have a current daily report (missing_daily_reports), which Zoom "
            "calls already have an analytical_note (missing_zoom_reports), and whether the current and "
            "previous owner daily reports exist. Call this before daily/weekly/owner reports instead of "
            "probing each chat and Zoom call separately."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD; defaults to date_from for a single day"},
            },
            "required": ["date_from"],
            "additionalProperties": False,
        },
        "handler": tool_get_report_readiness,
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
        "description": (
            "Search Bitrix tasks by id, period, text, or responsible user. When the request mentions a task "
            "number (e.g. 318241), pass it as bitrix_task_id for an instant single-task lookup — do NOT put the "
            "number in query (query matches title/description text only). Each row includes comments_total_count "
            "and comments_human_count; when a task has human comments, read them with get_task_comments(bitrix_task_id). "
            "Descriptions are truncated by default to keep results small; set include_full_description=true (best with "
            "bitrix_task_id) to read one task's full description."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bitrix_task_id": {"type": "integer", "description": "Exact Bitrix task number. Fastest path; ignores other filters."},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "query": {"type": "string", "description": "Text match on title/description. Not for task numbers — use bitrix_task_id instead."},
                "responsible_bitrix_user_id": {"type": "integer"},
                "include_full_description": {"type": "boolean", "description": "Return full descriptions instead of a 500-char preview. Use only with bitrix_task_id or a small result set."},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": tool_search_tasks,
    },
    "get_task_comments": {
        "description": (
            "Read the discussion/comments of one Bitrix task by bitrix_task_id (from search_tasks). "
            "Returns human comments with author name, timestamp, and BB-code-cleaned text. "
            "Auto-generated notifications (overdue reminders, status cards, completion notices) are "
            "excluded by default; set include_service=true to include them. Use this to find what "
            "people asked, decided, committed, or blocked on a task — search_tasks alone does not show it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bitrix_task_id": {"type": "integer", "description": "Bitrix task id from search_tasks."},
                "include_service": {
                    "type": "boolean",
                    "description": "Include system notifications and status-card messages. Default false.",
                },
                "order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "description": "Chronological order by date. Default asc (oldest first).",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["bitrix_task_id"],
            "additionalProperties": False,
        },
        "handler": tool_get_task_comments,
    },
    "create_bitrix_task": {
        "description": (
            "Create one Bitrix task through the configured Bitrix webhook. Supports one-off and recurring tasks, "
            "and optional observers (наблюдатели) resolved against the org structure. STRICT RULE: do not call "
            "this tool unless the user provided a task title, exactly one responsible person, and a deadline. "
            "If title, responsible_name/responsible_bitrix_user_id, or deadline is missing, ask the user for it "
            "first. The tool resolves responsible_name and auditor_names through the active org structure and "
            "refuses ambiguous matches. To make the task recurring, pass periodic={type, ...}; otherwise the "
            "task is one-off. Before creating, confirm with the user: title, responsible, deadline, full list "
            "of observers (if any), and periodic schedule (if any)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Required task title."},
                "description": {"type": "string", "description": "Task description. If omitted, title is used."},
                "responsible_name": {"type": "string", "description": "Responsible employee name from org structure (fuzzy-matched against active users)."},
                "responsible_bitrix_user_id": {"type": "integer", "description": "Exact Bitrix user id of the responsible employee. Preferred over responsible_name when known."},
                "deadline": {"type": "string", "description": "Required deadline: YYYY-MM-DD, DD.MM.YYYY, or ISO datetime. For recurring tasks this is the first instance deadline."},
                "priority": {"type": "string", "enum": ["normal", "high", "critical"]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "auditor_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of observer (наблюдатель) full names from the active org structure. Each is fuzzy-matched against users; ambiguity is refused.",
                },
                "auditor_bitrix_user_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional list of observer Bitrix user ids. Preferred over auditor_names when known. Merged with names; duplicates are dropped.",
                },
                "periodic": {
                    "type": "object",
                    "description": "Optional recurrence schedule. Omit for a one-off task. When present, the task is created as IS_REGULAR=Y with the corresponding REGULAR_PARAMETERS in Bitrix.",
                    "properties": {
                        "type": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "Recurrence type."},
                        "interval": {"type": "integer", "minimum": 1, "description": "Every N units (default 1). E.g. interval=2 with type=weekly = every other week."},
                        "weekdays": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]},
                            "description": "Required for type=weekly. Two-letter day codes (MO/TU/WE/TH/FR/SA/SU).",
                        },
                        "day_of_month": {"type": "integer", "minimum": 1, "maximum": 31, "description": "Required for type=monthly. Day of the month (1-31)."},
                        "daily_mode": {"type": "string", "enum": ["all", "workdays"], "description": "Optional for type=daily (default 'all'). 'workdays' = skip weekends."},
                        "until": {"type": "string", "description": "Optional end date YYYY-MM-DD. After this date, no new instances are created."},
                    },
                    "required": ["type"],
                    "additionalProperties": False,
                },
            },
            "required": ["title", "deadline"],
            "additionalProperties": False,
        },
        "handler": tool_create_bitrix_task,
    },
    "delete_bitrix_task": {
        "description": (
            "Delete one Bitrix task through the configured Bitrix webhook. STRICT CONFIRMATION RULE: do not call "
            "this tool until the user has confirmed deletion of one exact bitrix_task_id after seeing the task "
            "title, status, responsible person, and deadline. Never delete by title/search text/name or ambiguous "
            "reference. First use search_tasks(bitrix_task_id=...) to show the exact task, then ask for confirmation. "
            "Only after the user confirms, call delete_bitrix_task(bitrix_task_id=..., confirm=true)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bitrix_task_id": {"type": "integer", "description": "Exact Bitrix task number to delete."},
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Means the user explicitly confirmed deleting this exact task after seeing its details.",
                },
                "expected_title": {
                    "type": "string",
                    "description": "Optional safety check: if provided, must exactly match the locally indexed task title.",
                },
            },
            "required": ["bitrix_task_id", "confirm"],
            "additionalProperties": False,
        },
        "handler": tool_delete_bitrix_task,
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
                "limit": {"type": "integer", "minimum": 1, "maximum": ZOOM_TRANSCRIPT_MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": tool_get_zoom_call_transcript,
    },
    "export_zoom_call_markdown": {
        "description": (
            "Export ONE Zoom call as a ready-to-send Markdown document: header with topic, date, "
            "time (МСК), duration and participants, then the FULL transcript line by line (speaker + "
            "timecode). Returns {markdown, filename, call_id, chars}. Use this when the owner asks to "
            "get/send a call's transcript 'в md'/'markdown'/'файлом' — deliver the `markdown` value to "
            "the chat (preferably as a .md file attachment using the suggested `filename`)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "call_id": {"type": "string", "description": "Zoom call id from list_zoom_calls/get_zoom_call_transcript."},
            },
            "required": ["call_id"],
            "additionalProperties": False,
        },
        "handler": tool_export_zoom_call_markdown,
    },
    "export_zoom_transcripts_markdown": {
        "description": (
            "Export MANY Zoom calls into ONE Markdown document with a table of contents and clear '---' "
            "boundaries between meetings; each meeting has metadata (topic, date, МСК time, duration, "
            "participants) plus its FULL transcript. Select either by explicit call_ids OR by a "
            "date_from/date_to range (YYYY-MM-DD). Google Drive transcript imports (noisy duplicates) are "
            "excluded unless include_google_drive=true. Returns {markdown, filename, calls, chars}. The "
            "document can be large — deliver `markdown` to the chat as a .md file attachment, not as plain text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "call_ids": {"type": "array", "items": {"type": "string"}, "description": "Explicit Zoom call ids; overrides the date range."},
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "include_google_drive": {"type": "boolean", "description": "Keep Google Drive transcript imports (default false)."},
            },
            "additionalProperties": False,
        },
        "handler": tool_export_zoom_transcripts_markdown,
    },
    "search_zoom_transcripts": {
        "description": "Search Zoom transcript segments by text and optional date range. For chat context, search keywords derived from chat OCR, tasks, risks, project names, and owner names.",
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
    "list_recommendations": {
        "description": "List addressable recommendations with lifecycle status and optional event history. Use this before checking recommendation feedback or repeated recommendations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "status": {"type": "string", "description": "Use 'open' for all non-final statuses, or a concrete recommendation status."},
                "dialog_id": {"type": "string"},
                "manager_bitrix_user_id": {"type": "integer"},
                "employee_bitrix_user_id": {"type": "integer"},
                "include_events": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": tool_list_recommendations,
    },
    "get_recommendation_feedback_context": {
        "description": "Read active recommendations and event history relevant to one chat/date. Use with raw chat transcripts to analyze previous-day and current-day replies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dialog_id": {"type": "string"},
                "report_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["dialog_id", "report_date"],
            "additionalProperties": False,
        },
        "handler": tool_get_recommendation_feedback_context,
    },
    "save_recommendation_event": {
        "description": "Append one lifecycle event for an addressable recommendation and optionally update its status. Use after interpreting a human reply.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recommendation_id": {"type": "string"},
                "event_type": {"type": "string", "enum": ["created", "sent", "delivered", "seen", "employee_replied", "ai_interpreted", "status_changed", "manager_reviewed", "task_created", "closed", "source_found"]},
                "author_type": {"type": "string", "enum": ["system", "ai", "manager", "employee"]},
                "author_bitrix_user_id": {"type": "integer"},
                "dialog_id": {"type": "string"},
                "bitrix_message_id": {"type": "integer"},
                "chat_message_day": {"type": "string", "description": "YYYY-MM-DD"},
                "old_status": {"type": "string"},
                "new_status": {"type": "string"},
                "event_text": {"type": "string"},
                "interpretation": {"type": "object"},
                "source_payload": {"type": "object"},
                "manager_review_required": {"type": "boolean"},
            },
            "required": ["recommendation_id"],
            "additionalProperties": False,
        },
        "handler": tool_save_recommendation_event,
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
    "send_owner_weekly_report_pdf": {
        "description": (
            "Send the current weekly owner report as a PDF into Bitrix personal messages (default recipient: "
            "Evgeniy Palei, bitrix_user_id 1). Builds the PDF from the saved owner_weekly_report for the given period "
            "and attaches it via im.disk.file.commit. STRICT RULE: confirm=true is mandatory — first build and save "
            "the weekly report via save_owner_weekly_report, show the owner the report period and that the PDF goes to "
            "Evgeniy, get explicit approval, and only then call this tool with confirm=true. The weekly report for that "
            "period must already be saved (is_current)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "period_start": {"type": "string", "description": "YYYY-MM-DD (Monday of the reported week)."},
                "period_end": {"type": "string", "description": "YYYY-MM-DD (Friday/Sunday of the reported week)."},
                "recipient_bitrix_user_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Bitrix user ids to send the PDF to. Default [1] (Evgeniy Palei).",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Means the owner explicitly approved sending the weekly PDF to Evgeniy.",
                },
            },
            "required": ["period_start", "period_end", "confirm"],
            "additionalProperties": False,
        },
        "handler": tool_send_owner_weekly_report_pdf,
    },
    "list_pending_zoom_operational_dispatches": {
        "description": (
            "List Zoom calls that have a saved analytical report but were NOT yet dispatched as aggregated "
            "'Итоги созвона' tasks to Bitrix. Each pending entry has call_id, call_date, topic, time, and duration. "
            "Use this AS THE FIRST STEP when the owner answers 'ставь' / 'создавай' / 'да' after a zoom-to-tasks "
            "Telegram summary: it tells you exactly which call_ids still need dispatching. "
            "DEFAULT PERIOD: today only (Europe/Moscow) — covers exactly the calls the owner just approved. "
            "Pass date_from explicitly if you really need to dispatch older calls (rare; usually the owner only "
            "wants the call they just saw in the summary)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD (default: today minus 2 days)"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD (default: today)"},
            },
            "additionalProperties": False,
        },
        "handler": tool_list_pending_zoom_operational_dispatches,
    },
    "preview_zoom_operational_tasks": {
        "description": (
            "Preview the aggregated 'Итоги созвона' Bitrix tasks that would be created for one Zoom call WITHOUT sending. "
            "Returns title (e.g. 'Итоги созвона 09:28'), per-recipient cards (one card = one aggregated task per "
            "responsible person, grouping all of their operational_tasks from the call), deadline (call_date 19:00 МСК), "
            "and the standard description '"
            "Ознакомьтесь со списком выделенных из созвона задач и поставьте себе самые важные в Битрикс...'. "
            "Do not call this if you only need to send — just call dispatch_zoom_operational_tasks directly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "call_id": {"type": "string", "description": "Zoom call UUID (from list_pending_zoom_operational_dispatches or list_zoom_calls)."},
            },
            "required": ["call_id"],
            "additionalProperties": False,
        },
        "handler": tool_preview_zoom_operational_tasks,
    },
    "dispatch_zoom_operational_tasks": {
        "description": (
            "Create aggregated 'Итоги созвона <ЧЧ:ММ>' Bitrix tasks for ONE Zoom call: one task per responsible person, "
            "deadline = call_date 19:00 МСК, description = standard 'Ознакомьтесь со списком...' header plus the list of "
            "this person's operational_tasks from the call. Behaves EXACTLY like Albery UI button 'Отправка задач'. "
            "STRICT CONFIRMATION RULE: do not call unless the owner has just explicitly approved sending (replied "
            "'ставь' / 'создавай' / 'да' to the zoom-to-tasks Telegram summary). confirm=true is mandatory. "
            "DO NOT use create_bitrix_task to recreate individual tasks here — this is the right tool for zoom "
            "operational tasks; it groups them and produces the exact card format the owner expects. "
            "After success the call is marked with raw_json.ai_report.bitrix_dispatch.dispatched_at so "
            "list_pending_zoom_operational_dispatches will not return it again. "
            "PARTIAL SUCCESS: the result returns 'sent' (number of tasks created) and 'skipped_assignees' "
            "(responsible people with no matching Bitrix user, e.g. not in the team sync). This is a SUCCESS, "
            "not an error — do NOT retry. Report it to the owner like 'Поставил N задач; не нашёл в Битриксе: "
            "<names> — добавьте их в Битрикс или поставьте задачу вручную.'"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "call_id": {"type": "string", "description": "Zoom call UUID."},
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Means the owner explicitly approved creating aggregated 'Итоги созвона' tasks for this call.",
                },
            },
            "required": ["call_id", "confirm"],
            "additionalProperties": False,
        },
        "handler": tool_dispatch_zoom_operational_tasks,
    },
    "list_pending_owner_recommendations": {
        "description": (
            "List addressed manager recommendations from the current owner_daily_report for a given date. "
            "Returns rows with id, manager_full_name, manager_bitrix_user_id, recommendation_text, subject, priority, due_date, status. "
            "Also returns the report summary, dynamics_summary, risks_summary, and report_text so the agent can build "
            "personal message drafts (e.g. add the day conclusion to Evgeniy's draft). "
            "Excludes recommendations already sent, cancelled, done, or rejected. "
            "Use this after save_owner_daily_report and before send_owner_recommendations_to_bitrix."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["report_date"],
            "additionalProperties": False,
        },
        "handler": tool_list_pending_owner_recommendations,
    },
    "send_owner_recommendations_to_bitrix": {
        "description": (
            "Send owner_daily_report recommendations to Bitrix recipients as personal messages from the configured "
            "BITRIX_WEBHOOK_BASE account (owner). STRICT RULE: do not call this tool unless the owner has just "
            "approved the exact final texts you are about to send. Confirm=true is mandatory. "
            "Each entry in recipient_recommendations is treated as a complete final message for that Bitrix user id — "
            "send it as-is, do not edit or wrap. Uses im.message.add with fallback to im.notify.personal.add if the "
            "private chat is blocked. Logs the outcome to owner_recommendation_dispatches and updates "
            "owner_manager_recommendations.status to 'sent' for matching rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_date": {"type": "string", "description": "YYYY-MM-DD — the owner_daily_report date."},
                "recipient_recommendations": {
                    "type": "object",
                    "description": "Map of bitrix_user_id -> final personal message text. Each value is sent as-is.",
                    "additionalProperties": {"type": "string"},
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Means the owner has explicitly approved sending these exact texts.",
                },
            },
            "required": ["report_date", "recipient_recommendations", "confirm"],
            "additionalProperties": False,
        },
        "handler": tool_send_owner_recommendations_to_bitrix,
    },
    "send_bitrix_message": {
        "description": (
            "Send one personal Bitrix message to a single employee via the configured BITRIX_WEBHOOK_BASE account "
            "(your own user — there is no separate bot user). Uses im.message.add with fallback to "
            "im.notify.personal.add when the private chat is blocked. STRICT RULES: "
            "(1) confirm=true is mandatory — first resolve the recipient and show the user the exact final "
            "message_text together with the recipient's full_name + work_position + bitrix_user_id, then ask "
            "for explicit approval; only then call this tool with confirm=true. "
            "(2) Pass either recipient_bitrix_user_id (preferred — exact integer) or recipient_name (fuzzy lookup "
            "against active users). If recipient_name is ambiguous, the tool returns the candidates and refuses "
            "to send — re-ask the user and call again with recipient_bitrix_user_id. "
            "(3) message_text is sent verbatim — do not wrap, edit, or prepend signatures."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipient_bitrix_user_id": {
                    "type": "integer",
                    "description": "Exact Bitrix user id of the recipient. Preferred over recipient_name.",
                },
                "recipient_name": {
                    "type": "string",
                    "description": "Full name (or distinctive part) of the recipient. Used only when recipient_bitrix_user_id is not provided. Fuzzy-matched against active users; ambiguity is refused.",
                },
                "message_text": {
                    "type": "string",
                    "description": "Final message to send, verbatim. Up to 20000 characters.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Means the user has explicitly approved sending this exact text to this exact recipient.",
                },
            },
            "required": ["message_text", "confirm"],
            "additionalProperties": False,
        },
        "handler": tool_send_bitrix_message,
    },
    "cancel_owner_recommendation": {
        "description": (
            "Mark one owner_manager_recommendations row as cancelled (e.g. the owner decided not to send it). "
            "Writes an owner_recommendation_events 'cancelled' entry with the reason. Use when the owner explicitly "
            "rejects a draft for a specific person."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recommendation_id": {"type": "string", "description": "UUID of the recommendation row."},
                "reason": {"type": "string", "description": "Short owner-provided reason (optional)."},
            },
            "required": ["recommendation_id"],
            "additionalProperties": False,
        },
        "handler": tool_cancel_owner_recommendation,
    },
    "fetch_url": {
        "description": (
            "Fetch the contents of a web URL the user sent in chat (article, public Google Sheet, public Google Doc, "
            "raw text file, etc.) and return it as plain text. Use this when the user shares a link and asks you to "
            "read, summarize, or extract data from it. Special handling: Google Sheets URLs are auto-rewritten to "
            "CSV export, Google Docs URLs to TXT export. HTML pages are stripped to text by default. Size is hard-"
            "capped (default 50000 chars, max 200000) to protect the context window. On 401/403 from Google docs, "
            "the response includes a hint about opening link sharing or using the synced Drive folder. Do NOT use "
            "this for company knowledge that already lives in Albery — prefer search_company_knowledge, "
            "list_company_files, get_company_file, search_messages, get_zoom_call_transcript for that."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full http(s) URL to fetch. Google Sheets/Docs links are auto-rewritten to export endpoints.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 200000,
                    "description": "Hard cap on returned text length. Default 50000. Bump only if a larger document is genuinely needed.",
                },
                "strip_html": {
                    "type": "boolean",
                    "description": "Strip HTML tags to plain text when the response looks like HTML. Default true.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        "handler": tool_fetch_url,
    },
    "upsert_ai_instruction": {
        "description": "Create or update one editable AI instruction folder by path in Настройки -> Инструкции для ИИ.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder path separated by /, for example: Формирование отчетов/Ежедневный отчет по компании"},
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
}


FAQ_TOOL_NAMES: set[str] = {
    "start_here_always_read_ai_instructions",
    "health",
    "get_runtime_status",
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
            if name == "start_here_always_read_ai_instructions":
                connector_id = "faq" if tool_names == FAQ_TOOL_NAMES else "full"
                args = {
                    **args,
                    "_connector_tools": sorted(available_tools.keys()),
                    "_connector_id": connector_id,
                }
            started = time.perf_counter()
            result_payload = available_tools[name]["handler"](args)
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            if duration_ms >= TOOL_LATENCY_LOG_MS:
                result_size = len(json.dumps(json_safe(result_payload), ensure_ascii=False, default=json_default))
                logger.info("mcp_tool_call name=%s duration_ms=%s result_bytes=%s", name, duration_ms, result_size)
            result = text_response(result_payload)
        else:
            raise McpError(-32601, f"Unknown method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except McpError as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": exc.message}}
    except Exception:
        logger.exception("Unhandled MCP request error: method=%s id=%s", method, request_id)
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "Internal MCP error."}}


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
        except Exception:
            logger.exception("Failed to parse or handle MCP stdin request")
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid MCP request."}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, default=json_default) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
