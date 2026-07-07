"""Bitrix24 integration: REST client (webhook + BitrixClient), inbound task/team
event webhooks, the task-event queue and task/team sync into PostgreSQL.

Moved verbatim out of app.py (2026-07-02 refactor, step Sh2.4 — move-only).
Registers its webhook routes on the shared Flask `app` at import time; app.py
imports this module at the bottom and re-imports the names its remaining code
still calls (see the import block there).
"""
from __future__ import annotations

import hmac
import json
import os
import time

from datetime import date
from datetime import datetime
from flask import jsonify
from flask import request
from psycopg.types.json import Jsonb
from typing import Any
from urllib.parse import urlencode
import requests

from config import (
    LOCAL_TZ,
)

from utils import (
    extract_collection,
    first_non_empty,
    flatten_request_payload,
    format_datetime_ru,
    format_person_name,
    is_rate_limit_error,
    iso_or_none,
    make_aware,
    normalize_status,
    parse_datetime,
    pick,
    split_bitrix_user_name,
    to_int,
)

from app import (  # shared Flask app + db glue still living in app.py
    app,
    pg_connect,
    pg_json,
    pg_table_exists,
)


def pg_user_id_by_bitrix(cur: Any, bitrix_user_id: Any) -> Any:
    user_id = to_int(bitrix_user_id)
    if user_id is None:
        return None
    cur.execute("SELECT id FROM users WHERE bitrix_user_id = %s", (user_id,))
    row = cur.fetchone()
    return row["id"] if row else None
def ensure_bitrix_task_event_queue_schema() -> None:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            if not pg_table_exists(cur, "bitrix_task_events"):
                raise RuntimeError("PostgreSQL table public.bitrix_task_events is missing. Apply database migrations before processing Bitrix events.")
def bitrix_event_secret_valid(secret: str) -> bool:
    expected = os.getenv("BITRIX_EVENT_SECRET", "").strip()
    if not expected:
        return False
    return hmac.compare_digest(secret, expected)
def normalize_bitrix_event_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    lowered = text.lower()
    if lowered == "ontaskadd":
        return "OnTaskAdd"
    if lowered == "ontaskupdate":
        return "OnTaskUpdate"
    if lowered == "ontaskdelete":
        return "OnTaskDelete"
    return text
def extract_bitrix_event_task_id(payload: dict[str, Any]) -> int | None:
    candidates = (
        pick(payload, "data.FIELDS_AFTER.ID", "data.FIELDS_AFTER.TASK_ID"),
        pick(payload, "data.FIELDS_BEFORE.ID", "data.FIELDS_BEFORE.TASK_ID"),
        pick(payload, "data.TASK_ID", "data.ID", "task_id", "TASK_ID", "ID", "id"),
    )
    for value in candidates:
        task_id = to_int(value)
        if task_id is not None:
            return task_id

    for key, value in payload.items():
        key_lower = str(key).lower()
        if "data" not in key_lower:
            continue
        if "task" not in key_lower and not key_lower.endswith("[id]"):
            continue
        task_id = to_int(value[0] if isinstance(value, list) and value else value)
        if task_id is not None:
            return task_id
    return None
def _event_field_after(payload: dict[str, Any], field: str) -> int | None:
    """Read data[FIELDS_AFTER][<field>] from a flattened Bitrix event payload (dotted or bracketed)."""
    val = pick(payload, f"data.FIELDS_AFTER.{field}", f"data.FIELDS_BEFORE.{field}")
    got = to_int(val)
    if got is not None:
        return got
    suffix = f"[fields_after][{field.lower()}]"
    for key, value in payload.items():
        if str(key).lower().endswith(suffix):
            got = to_int(value[0] if isinstance(value, list) and value else value)
            if got is not None:
                return got
    return None


def extract_bitrix_comment_event_task_id(payload: dict[str, Any]) -> int | None:
    """For OnTaskCommentAdd, the TASK id is FIELDS_AFTER.TASK_ID (FIELDS_AFTER.ID is the COMMENT id)."""
    return _event_field_after(payload, "TASK_ID")


def _extract_bitrix_event_comment_id(payload: dict[str, Any]) -> int | None:
    """The comment id for a task-comment event. On this portal the chat message id lives in
    FIELDS_AFTER.MESSAGE_ID (FIELDS_AFTER.ID is 0/unused); prefer MESSAGE_ID, fall back to ID."""
    mid = _event_field_after(payload, "MESSAGE_ID")
    if mid:
        return mid
    got = _event_field_after(payload, "ID")
    return got or None


def enqueue_bitrix_task_event(event_name: str, task_id: int, payload: dict[str, Any]) -> str:
    ensure_bitrix_task_event_queue_schema()
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bitrix_task_events (event_name, bitrix_task_id, payload)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (event_name, task_id, pg_json(payload)),
                )
                return str(cur.fetchone()["id"])
def sync_bitrix_task_by_id(task_id: int, event_name: str = "manual") -> dict[str, Any]:
    webhook_base = os.getenv("BITRIX_WEBHOOK_BASE", "").strip()
    if not webhook_base:
        raise RuntimeError("BITRIX_WEBHOOK_BASE не задан")

    normalized_event = normalize_bitrix_event_name(event_name)
    if normalized_event == "OnTaskDelete":
        deleted = delete_task_records([task_id])
        return {"task_id": task_id, "event": normalized_event, "action": "deleted", "deleted": deleted}

    client = BitrixClient(webhook_base)
    details = client.get_task_details(task_id)
    if not details:
        deleted = delete_task_records([task_id])
        return {
            "task_id": task_id,
            "event": normalized_event,
            "action": "deleted_missing_or_inaccessible",
            "deleted": deleted,
        }
    if "id" not in details and "ID" not in details:
        details["id"] = task_id
    record = build_task_record(client, details)
    upsert_task_records([record], sync_run_id=None)
    return {"task_id": task_id, "event": normalized_event, "action": "upserted"}
def process_bitrix_task_event_queue(limit: int = 20) -> dict[str, Any]:
    ensure_bitrix_task_event_queue_schema()
    limit = max(1, min(int(limit or 20), 100))
    processed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, event_name, bitrix_task_id
                FROM bitrix_task_events
                WHERE status IN ('queued','error') AND attempts < 5
                ORDER BY received_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    for row in rows:
        event_id = row["id"]
        event_name = row["event_name"]
        task_id = int(row["bitrix_task_id"])
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE bitrix_task_events
                        SET status = 'processing', attempts = attempts + 1, updated_at = now()
                        WHERE id = %s AND status IN ('queued','error')
                        """,
                        (event_id,),
                    )
                    if cur.rowcount != 1:
                        continue
        try:
            result = sync_bitrix_task_by_id(task_id, event_name)
            with pg_connect() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE bitrix_task_events
                            SET status = 'done', error_text = NULL, processed_at = now(), updated_at = now()
                            WHERE id = %s
                            """,
                            (event_id,),
                        )
            processed.append({"event_id": str(event_id), **result})
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc)
            with pg_connect() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE bitrix_task_events
                            SET status = 'error', error_text = %s, updated_at = now()
                            WHERE id = %s
                            """,
                            (error_text, event_id),
                        )
            errors.append({"event_id": str(event_id), "task_id": task_id, "event": event_name, "error": error_text})
    return {"processed": processed, "errors": errors, "processed_count": len(processed), "error_count": len(errors)}
def department_identity(department: dict[str, Any]) -> dict[str, Any]:
    dep_id = to_int(first_non_empty(department.get("ID"), department.get("id")))
    return {
        "id": dep_id,
        "name": first_non_empty(department.get("NAME"), department.get("name"), f"Отдел {dep_id}" if dep_id else None),
        "parent_id": to_int(first_non_empty(department.get("PARENT"), department.get("parent"), department.get("PARENT_ID"), department.get("parentId"))),
        "head_id": to_int(first_non_empty(department.get("UF_HEAD"), department.get("head"), department.get("HEAD"), department.get("headId"))),
    }
def department_depth(department_id: int, departments_by_id: dict[int, dict[str, Any]]) -> int:
    depth = 0
    seen: set[int] = set()
    current_id: int | None = department_id
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        department = departments_by_id.get(current_id)
        if not department:
            break
        parent_id = department.get("parent_id")
        if parent_id is None:
            break
        depth += 1
        current_id = parent_id
    return depth
def find_manager_by_department_hierarchy(user_id: int, department_ids: list[int], departments_by_id: dict[int, dict[str, Any]]) -> int | None:
    ordered_department_ids = sorted(
        {dep_id for dep_id in department_ids if dep_id in departments_by_id},
        key=lambda dep_id: department_depth(dep_id, departments_by_id),
        reverse=True,
    )
    for dep_id in ordered_department_ids:
        current_id: int | None = dep_id
        seen: set[int] = set()
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            department = departments_by_id.get(current_id)
            if not department:
                break
            head_id = to_int(department.get("head_id"))
            if head_id and head_id != user_id:
                return head_id
            current_id = to_int(department.get("parent_id"))
    return None
def normalize_team_member(user: dict[str, Any], departments: list[dict[str, Any]], users_by_id: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    user_id = to_int(first_non_empty(user.get("ID"), user.get("id")))
    if user_id is None:
        return None
    name = format_person_name(user, fallback_name=first_non_empty(user.get("NAME"), user.get("name"))) or f"Пользователь {user_id}"
    department_ids = [dep for dep in (to_int(item) for item in to_list(first_non_empty(user.get("UF_DEPARTMENT"), user.get("ufDepartment")))) if dep is not None]
    departments_by_id = {
        dep["id"]: dep
        for department in departments
        if (dep := department_identity(department)).get("id") is not None
    }
    member_departments: list[dict[str, Any]] = []

    for dep_id in department_ids:
        department = departments_by_id.get(dep_id, {})
        member_departments.append(
            {
                "id": dep_id,
                "name": department.get("name") or f"Отдел {dep_id}",
                "head_id": department.get("head_id"),
                "parent_id": department.get("parent_id"),
                "depth": department_depth(dep_id, departments_by_id),
            }
        )

    member_departments.sort(key=lambda dep: dep.get("depth") or 0, reverse=True)
    manager_id = find_manager_by_department_hierarchy(user_id, department_ids, departments_by_id)
    manager_name: str | None = None
    if manager_id:
        manager_profile = users_by_id.get(manager_id, {})
        manager_name = format_person_name(manager_profile) or f"Пользователь {manager_id}"

    return {
        "user_id": user_id,
        "name": name,
        "email": first_non_empty(user.get("EMAIL"), user.get("email")),
        "work_position": first_non_empty(user.get("WORK_POSITION"), user.get("workPosition")),
        "active": 0 if str(first_non_empty(user.get("ACTIVE"), user.get("active"), "Y")).upper() in {"N", "FALSE", "0"} else 1,
        "avatar_url": first_non_empty(user.get("PERSONAL_PHOTO"), user.get("personalPhoto"), user.get("avatar")),
        "manager_id": manager_id,
        "manager_name": manager_name,
        "departments": member_departments,
        "raw": user,
    }
def upsert_team_records_postgres(members: list[dict[str, Any]], departments: list[dict[str, Any]]) -> None:
    now = datetime.now().isoformat()
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for department in departments:
                    dep_id = to_int(first_non_empty(department.get("ID"), department.get("id")))
                    if dep_id is None:
                        continue
                    cur.execute(
                        """
                        INSERT INTO departments (
                            bitrix_department_id, name, parent_bitrix_department_id,
                            head_bitrix_user_id, raw_json, synced_at
                        ) VALUES (
                            %(bitrix_department_id)s, %(name)s, %(parent_bitrix_department_id)s,
                            %(head_bitrix_user_id)s, %(raw_json)s, %(synced_at)s
                        )
                        ON CONFLICT (bitrix_department_id) DO UPDATE SET
                            name = EXCLUDED.name,
                            parent_bitrix_department_id = EXCLUDED.parent_bitrix_department_id,
                            head_bitrix_user_id = EXCLUDED.head_bitrix_user_id,
                            raw_json = EXCLUDED.raw_json,
                            synced_at = EXCLUDED.synced_at,
                            updated_at = now()
                        """,
                        {
                            "bitrix_department_id": dep_id,
                            "name": first_non_empty(department.get("NAME"), department.get("name")),
                            "parent_bitrix_department_id": to_int(first_non_empty(department.get("PARENT"), department.get("parent"), department.get("PARENT_ID"))),
                            "head_bitrix_user_id": to_int(first_non_empty(department.get("UF_HEAD"), department.get("head"))),
                            "raw_json": Jsonb(department),
                            "synced_at": now,
                        },
                    )

                for member in members:
                    raw = member.get("raw") if isinstance(member.get("raw"), dict) else member
                    first_name, last_name, second_name = split_bitrix_user_name(raw, member.get("name"))
                    phone = first_non_empty(raw.get("PERSONAL_MOBILE"), raw.get("WORK_PHONE"), raw.get("phone"))
                    cur.execute(
                        """
                        INSERT INTO users (
                            bitrix_user_id, full_name, first_name, last_name, second_name,
                            email, phone, avatar_url, work_position, is_active,
                            manager_bitrix_user_id, raw_json, synced_at
                        ) VALUES (
                            %(bitrix_user_id)s, %(full_name)s, %(first_name)s, %(last_name)s,
                            %(second_name)s, %(email)s, %(phone)s, %(avatar_url)s,
                            %(work_position)s, %(is_active)s, %(manager_bitrix_user_id)s,
                            %(raw_json)s, %(synced_at)s
                        )
                        ON CONFLICT (bitrix_user_id) DO UPDATE SET
                            full_name = EXCLUDED.full_name,
                            first_name = EXCLUDED.first_name,
                            last_name = EXCLUDED.last_name,
                            second_name = EXCLUDED.second_name,
                            email = EXCLUDED.email,
                            phone = EXCLUDED.phone,
                            avatar_url = EXCLUDED.avatar_url,
                            work_position = EXCLUDED.work_position,
                            is_active = EXCLUDED.is_active,
                            manager_bitrix_user_id = EXCLUDED.manager_bitrix_user_id,
                            raw_json = EXCLUDED.raw_json,
                            synced_at = EXCLUDED.synced_at,
                            updated_at = now()
                        """,
                        {
                            "bitrix_user_id": member["user_id"],
                            "full_name": member.get("name"),
                            "first_name": first_name,
                            "last_name": last_name,
                            "second_name": second_name,
                            "email": member.get("email"),
                            "phone": phone,
                            "avatar_url": member.get("avatar_url"),
                            "work_position": member.get("work_position"),
                            "is_active": bool(member.get("active", 1)),
                            "manager_bitrix_user_id": member.get("manager_id"),
                            "raw_json": Jsonb(raw),
                            "synced_at": now,
                        },
                    )

                cur.execute("SELECT id, bitrix_user_id FROM users")
                user_ids = {int(row["bitrix_user_id"]): row["id"] for row in cur.fetchall()}
                cur.execute("SELECT id, bitrix_department_id FROM departments")
                department_ids = {int(row["bitrix_department_id"]): row["id"] for row in cur.fetchall()}

                for department in departments:
                    dep_id = to_int(first_non_empty(department.get("ID"), department.get("id")))
                    if dep_id is None:
                        continue
                    parent_bitrix_id = to_int(first_non_empty(department.get("PARENT"), department.get("parent"), department.get("PARENT_ID")))
                    head_bitrix_id = to_int(first_non_empty(department.get("UF_HEAD"), department.get("head")))
                    cur.execute(
                        """
                        UPDATE departments
                        SET parent_id = %(parent_id)s,
                            head_id = %(head_id)s,
                            updated_at = now()
                        WHERE bitrix_department_id = %(bitrix_department_id)s
                        """,
                        {
                            "parent_id": department_ids.get(parent_bitrix_id) if parent_bitrix_id else None,
                            "head_id": user_ids.get(head_bitrix_id) if head_bitrix_id else None,
                            "bitrix_department_id": dep_id,
                        },
                    )

                for member in members:
                    manager_bitrix_id = to_int(member.get("manager_id"))
                    cur.execute(
                        """
                        UPDATE users
                        SET manager_id = %(manager_id)s,
                            updated_at = now()
                        WHERE bitrix_user_id = %(bitrix_user_id)s
                        """,
                        {
                            "manager_id": user_ids.get(manager_bitrix_id) if manager_bitrix_id else None,
                            "bitrix_user_id": member["user_id"],
                        },
                    )

                    user_uuid = user_ids.get(member["user_id"])
                    if not user_uuid:
                        continue
                    cur.execute("DELETE FROM user_departments WHERE user_id = %(user_id)s", {"user_id": user_uuid})
                    primary_department_id = None
                    if member.get("departments"):
                        primary_department_id = to_int(member["departments"][0].get("id"))
                    for dep in member.get("departments", []):
                        dep_id = to_int(dep.get("id"))
                        department_uuid = department_ids.get(dep_id) if dep_id else None
                        if not department_uuid:
                            continue
                        cur.execute(
                            """
                            INSERT INTO user_departments (user_id, department_id, is_primary)
                            VALUES (%(user_id)s, %(department_id)s, %(is_primary)s)
                            ON CONFLICT (user_id, department_id) DO UPDATE SET
                                is_primary = EXCLUDED.is_primary
                            """,
                            {
                                "user_id": user_uuid,
                                "department_id": department_uuid,
                                "is_primary": dep_id == primary_department_id,
                            },
                        )

                active_user_ids = [member["user_id"] for member in members]
                if active_user_ids:
                    cur.execute(
                        """
                        UPDATE users
                        SET is_active = FALSE,
                            updated_at = now()
                        WHERE bitrix_user_id <> ALL(%s)
                        """,
                        (active_user_ids,),
                    )
def upsert_team_records(members: list[dict[str, Any]], departments: list[dict[str, Any]]) -> None:
    upsert_team_records_postgres(members, departments)
def load_team_members_postgres() -> list[dict[str, Any]]:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.bitrix_user_id AS user_id,
                    u.full_name AS name,
                    u.email,
                    u.work_position,
                    u.is_active AS active,
                    u.avatar_url,
                    m.bitrix_user_id AS manager_id,
                    m.full_name AS manager_name,
                    COALESCE(string_agg(d.name, ', ' ORDER BY ud.is_primary DESC, d.name), '') AS departments_text,
                    u.synced_at AS last_synced_at
                FROM users u
                LEFT JOIN users m ON m.id = u.manager_id
                LEFT JOIN user_departments ud ON ud.user_id = u.id
                LEFT JOIN departments d ON d.id = ud.department_id
                WHERE u.is_active = TRUE
                GROUP BY
                    u.id, u.bitrix_user_id, u.full_name, u.email, u.work_position,
                    u.is_active, u.avatar_url, m.bitrix_user_id, m.full_name, u.synced_at
                ORDER BY u.is_active DESC, u.full_name
                """
            )
            rows = cur.fetchall()
    team: list[dict[str, Any]] = []
    for row in rows:
        last_synced_at = iso_or_none(row["last_synced_at"])
        team.append(
            {
                "user_id": row["user_id"],
                "name": row["name"],
                "email": row["email"],
                "work_position": row["work_position"],
                "active": 1 if row["active"] else 0,
                "avatar_url": row["avatar_url"],
                "manager_id": row["manager_id"],
                "manager_name": row["manager_name"],
                "departments_text": row["departments_text"],
                "last_synced_at": last_synced_at,
                "last_synced_at_text": format_datetime_ru(last_synced_at),
            }
        )
    return team
def load_team_members() -> list[dict[str, Any]]:
    return load_team_members_postgres()
def sync_bitrix_team(webhook_base: str) -> dict[str, Any]:
    client = BitrixClient(webhook_base)
    users = client.list_users(active_only=True)
    users_by_id = {
        user_id: user
        for user in users
        if (user_id := to_int(first_non_empty(user.get("ID"), user.get("id")))) is not None
    }
    department_ids: set[int] = set()
    for user in users:
        for dep_id in [to_int(item) for item in to_list(first_non_empty(user.get("UF_DEPARTMENT"), user.get("ufDepartment")))]:
            if dep_id is not None:
                department_ids.add(dep_id)

    departments_by_id: dict[int, dict[str, Any]] = {}
    pending_department_ids = set(department_ids)
    while pending_department_ids:
        dep_id = min(pending_department_ids)
        pending_department_ids.remove(dep_id)
        department = client.get_department(dep_id)
        if department:
            identity = department_identity(department)
            normalized_dep_id = identity.get("id")
            if normalized_dep_id is not None:
                departments_by_id[normalized_dep_id] = department
                parent_id = identity.get("parent_id")
                if parent_id is not None and parent_id not in departments_by_id:
                    pending_department_ids.add(parent_id)
        time.sleep(client.request_delay)

    head_ids = {
        head_id
        for department in departments_by_id.values()
        if (head_id := department_identity(department).get("head_id")) is not None
    }
    for head_id in sorted(head_ids):
        if head_id not in users_by_id:
            profile = client.get_user(head_id)
            if profile:
                users_by_id[head_id] = profile

    departments = list(departments_by_id.values())
    members = [
        member
        for user in users
        if (member := normalize_team_member(user, departments, users_by_id)) is not None
    ]
    upsert_team_records(members, departments)
    return {"scanned": len(users), "saved": len(members), "team": load_team_members()}
def bitrix_origin_from_webhook(webhook_base: str) -> str:
    parts = webhook_base.strip().split("/")
    if len(parts) >= 3 and parts[0].startswith("http"):
        return "/".join(parts[:3])
    return webhook_base.strip().rstrip("/")
def bitrix_webhook_client() -> BitrixClient:
    webhook_base = os.getenv("BITRIX_WEBHOOK_BASE", "").strip()
    if not webhook_base:
        raise ValueError("Укажите BITRIX_WEBHOOK_BASE в .env для отправки в Bitrix.")
    return BitrixClient(webhook_base)
def bitrix_method_call(
    method: str,
    payload: dict[str, Any] | None = None,
    prefer_api: bool = True,
    fallback: bool = True,
) -> dict[str, Any]:
    client = bitrix_webhook_client()
    if not fallback:
        return client.call(method, payload or {}, use_api=prefer_api)
    return client.call_with_fallback(method, payload or {}, prefer_api=prefer_api)
def bitrix_storage_id_for_reports(client: BitrixClient) -> int:
    env_value = to_int(os.getenv("BITRIX_REPORTS_STORAGE_ID", ""))
    if env_value is not None:
        return env_value
    data = client.call_with_fallback("disk.storage.getlist", {}, prefer_api=False)
    storages = data.get("result") if isinstance(data, dict) else None
    if isinstance(storages, dict):
        storages = list(storages.values())
    if not isinstance(storages, list) or not storages:
        raise RuntimeError("Bitrix не вернул доступные хранилища Диска.")
    preferred = None
    for item in storages:
        if not isinstance(item, dict):
            continue
        entity_type = str(item.get("ENTITY_TYPE") or item.get("entityType") or "").lower()
        if entity_type in {"user", "common"}:
            preferred = item
            break
    preferred = preferred or next((item for item in storages if isinstance(item, dict)), None)
    storage_id = to_int((preferred or {}).get("ID") or (preferred or {}).get("id"))
    if storage_id is None:
        raise RuntimeError("Не удалось определить ID хранилища Bitrix Disk.")
    return storage_id
def to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                pass
        return [piece.strip() for piece in stripped.split(",") if piece.strip()]
    return [value]
class BitrixClient:
    def __init__(self, webhook_base: str):
        base = webhook_base.strip().rstrip("/")
        if not base:
            raise ValueError("BITRIX_WEBHOOK_BASE пустой")
        self.webhook_base = base
        self.session = requests.Session()
        self.user_cache: dict[int, dict[str, Any]] = {}
        self.department_cache: dict[int, dict[str, Any]] = {}
        self.methods_cache: set[str] | None = None
        self.scopes_cache: set[str] | None = None
        self.request_delay = float(os.getenv("BITRIX_REQUEST_DELAY", "0.55"))
        self.max_retries = int(os.getenv("BITRIX_MAX_RETRIES", "7"))

    def _url(self, method: str, use_api: bool) -> str:
        base = self.webhook_base
        if use_api and "/rest/" in base and "/rest/api/" not in base:
            base = base.replace("/rest/", "/rest/api/")
        return f"{base}/{method}"

    def call(self, method: str, payload: dict[str, Any] | None = None, use_api: bool = False) -> dict[str, Any]:
        url = self._url(method, use_api=use_api)
        response = self.session.post(url, json=payload or {}, timeout=60)
        if not response.ok:
            raise RuntimeError(f"{method}: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        if isinstance(data, dict) and "error" in data:
            err = data.get("error")
            err_desc = data.get("error_description")
            raise RuntimeError(f"{method}: {err} ({err_desc})")
        if not isinstance(data, dict):
            raise RuntimeError(f"{method}: неожиданный формат ответа")
        return data

    def call_with_fallback(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        prefer_api: bool = False,
    ) -> dict[str, Any]:
        attempts = [prefer_api, not prefer_api]
        last_error: Exception | None = None
        tried = set()
        for use_api in attempts:
            if use_api in tried:
                continue
            tried.add(use_api)
            try:
                return self.call(method, payload=payload, use_api=use_api)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{method}: вызов завершился ошибкой без деталей")

    def call_with_retry(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        use_api: bool = False,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self.call(method, payload=payload, use_api=use_api)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not is_rate_limit_error(exc):
                    raise
                if attempt >= self.max_retries:
                    raise
                backoff = min(1.5 * (attempt + 1), 10.0)
                time.sleep(backoff)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{method}: исчерпаны попытки повтора")

    def batch_commands(self, commands: dict[str, str]) -> dict[str, Any]:
        if not commands:
            return {"result": {}, "errors": {}}
        data = self.call_with_retry("batch", {"halt": 0, "cmd": commands}, use_api=False)
        batch_result = data.get("result", {})
        result = batch_result.get("result", {}) if isinstance(batch_result, dict) else {}
        errors = batch_result.get("result_error", {}) if isinstance(batch_result, dict) else {}
        return {
            "result": result if isinstance(result, dict) else {},
            "errors": errors if isinstance(errors, dict) else {},
        }

    @staticmethod
    def _is_method_missing_error(exc: Exception) -> bool:
        message = str(exc).lower()
        markers = (
            "error_method_not_found",
            "method not found",
            "no such method",
            "method is not defined",
            "метод не найден",
        )
        return any(marker in message for marker in markers)

    def get_available_methods(self) -> set[str]:
        if self.methods_cache is not None:
            return self.methods_cache
        methods: set[str] = set()
        for payload in ({"full": True}, {}):
            try:
                data = self.call_with_fallback("methods", payload, prefer_api=False)
                raw = data.get("result", [])
                if isinstance(raw, list):
                    methods.update(str(item) for item in raw if isinstance(item, str))
                elif isinstance(raw, dict):
                    for value in raw.values():
                        if isinstance(value, list):
                            methods.update(str(item) for item in value if isinstance(item, str))
                if methods:
                    break
            except Exception:  # noqa: BLE001
                continue
        self.methods_cache = methods
        return methods

    def get_scopes(self) -> set[str]:
        if self.scopes_cache is not None:
            return self.scopes_cache
        scopes: set[str] = set()
        for method_name in ("scope", "scope.json"):
            try:
                data = self.call(method_name, payload={}, use_api=False)
                raw = data.get("result", [])
                if isinstance(raw, list):
                    scopes = {str(item) for item in raw if isinstance(item, str)}
                break
            except Exception:  # noqa: BLE001
                continue
        self.scopes_cache = scopes
        return scopes

    @staticmethod
    def _extract_task_page(data: dict[str, Any]) -> list[dict[str, Any]]:
        result = data.get("result", data)
        raw_items: list[Any] = []
        if isinstance(result, list):
            raw_items = result
        elif isinstance(result, dict):
            for key in ("tasks", "items", "list"):
                value = result.get(key)
                if isinstance(value, list):
                    raw_items = value
                    break
            if not raw_items:
                numeric_values: list[Any] = []
                for key, value in result.items():
                    if str(key).isdigit():
                        numeric_values.append(value)
                if numeric_values:
                    raw_items = numeric_values

        page: list[dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, dict):
                page.append(item)
                continue
            if isinstance(item, (list, tuple)):
                if len(item) >= 2 and isinstance(item[1], dict):
                    converted = dict(item[1])
                    if "ID" not in converted and "id" not in converted:
                        converted["ID"] = item[0]
                    page.append(converted)
                elif len(item) == 1 and isinstance(item[0], dict):
                    page.append(item[0])
        return page

    @staticmethod
    def _extract_list_result(data: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
        result = data.get("result", data)
        raw_items: Any = result
        if isinstance(result, dict):
            raw_items = []
            for key in keys:
                value = result.get(key)
                if isinstance(value, list):
                    raw_items = value
                    break
            if not raw_items:
                for key in ("items", "users", "list"):
                    value = result.get(key)
                    if isinstance(value, list):
                        raw_items = value
                        break
        if not isinstance(raw_items, list):
            return []
        return [item for item in raw_items if isinstance(item, dict)]

    def list_recent_chats(self) -> list[dict[str, Any]]:
        chats_by_dialog: dict[str, dict[str, Any]] = {}
        limit = 200
        max_pages = int(os.getenv("BITRIX_CHAT_MAX_PAGES", "20"))
        for page in range(max_pages):
            payload = {
                "SKIP_OPENLINES": "Y",
                "SKIP_DIALOG": "N",
                "SKIP_CHAT": "N",
                "SKIP_UNDISTRIBUTED_OPENLINES": "Y",
                "ONLY_COPILOT": "N",
                "ONLY_CHANNEL": "N",
                "OFFSET": page * limit,
                "LIMIT": limit,
            }
            data = self.call_with_fallback("im.recent.list", payload, prefer_api=True)
            items = self._extract_list_result(data, "items")
            if not items:
                break
            for item in items:
                dialog_id = str(first_non_empty(item.get("id"), item.get("dialog_id"), item.get("dialogId")) or "")
                chat_id = to_int(first_non_empty(item.get("chat_id"), item.get("chatId")))
                item_type = str(item.get("type") or "").lower()
                if not dialog_id and chat_id is not None:
                    dialog_id = f"chat{chat_id}"
                chats_by_dialog[dialog_id] = item
            if len(items) < limit:
                break
            time.sleep(self.request_delay)
        return list(chats_by_dialog.values())

    def get_dialog_users(self, dialog_id: str) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        limit = 200
        offset = 0
        while True:
            payload = {
                "DIALOG_ID": dialog_id,
                "SKIP_EXTERNAL": "Y",
                "LIMIT": limit,
                "OFFSET": offset,
            }
            data = self.call_with_fallback("im.dialog.users.list", payload, prefer_api=True)
            page = self._extract_list_result(data, "users")
            if not page:
                break
            for user in page:
                user_id = to_int(user.get("id"))
                if user_id is None or user_id in seen_ids:
                    continue
                seen_ids.add(user_id)
                users.append(user)
            if len(page) < limit:
                break
            offset += limit
            time.sleep(self.request_delay)
        return users

    def list_users(self, active_only: bool = True) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        start: int | None = 0
        max_pages = int(os.getenv("BITRIX_USER_MAX_PAGES", "50"))
        for _ in range(max_pages):
            payload: dict[str, Any] = {
                "sort": "LAST_NAME",
                "order": "ASC",
            }
            if active_only:
                payload["FILTER"] = {"ACTIVE": True}
            if start is not None:
                payload["start"] = start
            data = self.call_with_fallback("user.get", payload, prefer_api=False)
            items = self._extract_list_result(data)
            if not items:
                break
            for item in items:
                user_id = to_int(first_non_empty(item.get("ID"), item.get("id")))
                if user_id is None or user_id in seen_ids:
                    continue
                seen_ids.add(user_id)
                users.append(item)
            next_value = data.get("next")
            if next_value is None:
                result = data.get("result")
                if isinstance(result, dict):
                    next_value = result.get("next")
            start = to_int(next_value)
            if start is None:
                break
            time.sleep(self.request_delay)
        return users

    def search_dialog_messages_for_day(self, dialog_id: str, target_date: date) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        try:
            return self._search_dialog_messages_for_day(dialog_id, target_date)
        except Exception:
            return self._get_dialog_messages_for_day(dialog_id, target_date)

    def get_dialog_messages_for_period(self, dialog_id: str, date_from: date, date_to: date) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        messages: list[dict[str, Any]] = []
        users_by_id: dict[int, dict[str, Any]] = {}
        files_by_id: dict[int, dict[str, Any]] = {}
        last_id: int | None = None
        limit = 50
        max_pages = int(os.getenv("BITRIX_CHAT_MESSAGE_MAX_PAGES", "200"))

        for _ in range(max_pages):
            payload: dict[str, Any] = {"DIALOG_ID": dialog_id, "LIMIT": limit}
            if last_id is not None:
                payload["LAST_ID"] = last_id
            data = self.call_with_fallback("im.dialog.messages.get", payload, prefer_api=True)
            result = data.get("result", {})
            page_messages = self._extract_list_result(data, "messages")
            raw_users = result.get("users", []) if isinstance(result, dict) else []
            raw_files = result.get("files", []) if isinstance(result, dict) else []
            if isinstance(raw_users, list):
                for user in raw_users:
                    if isinstance(user, dict):
                        user_id = to_int(user.get("id"))
                        if user_id is not None:
                            users_by_id[user_id] = user
            if isinstance(raw_files, list):
                for file_item in raw_files:
                    if isinstance(file_item, dict):
                        file_id = to_int(file_item.get("id"))
                        if file_id is not None:
                            files_by_id[file_id] = file_item
            if not page_messages:
                break

            oldest_id = None
            reached_older_than_period = False
            for message in page_messages:
                message_id = to_int(message.get("id"))
                if message_id is not None:
                    oldest_id = message_id if oldest_id is None else min(oldest_id, message_id)
                parsed = parse_datetime(message.get("date"))
                if parsed is None:
                    continue
                message_day = parsed.date()
                if date_from <= message_day <= date_to:
                    messages.append(message)
                elif message_day < date_from:
                    reached_older_than_period = True

            if oldest_id is None or reached_older_than_period:
                break
            if len(page_messages) < limit:
                break
            last_id = oldest_id
            time.sleep(self.request_delay)

        messages.sort(key=lambda item: (parse_datetime(item.get("date")) or datetime.min, to_int(item.get("id")) or 0))
        return messages, users_by_id, files_by_id

    def _search_dialog_messages_for_day(self, dialog_id: str, target_date: date) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        messages: list[dict[str, Any]] = []
        users_by_id: dict[int, dict[str, Any]] = {}
        files_by_id: dict[int, dict[str, Any]] = {}
        last_id: int | None = None
        limit = 200
        while True:
            payload: dict[str, Any] = {
                "DIALOG_ID": dialog_id,
                "DATE": f"{target_date.isoformat()}T00:00:00",
                "ORDER": {"ID": "ASC"},
                "LIMIT": limit,
            }
            if last_id is not None:
                payload["LAST_ID"] = last_id
            data = self.call_with_fallback("im.dialog.messages.search", payload, prefer_api=True)
            result = data.get("result", {})
            page_messages = self._extract_list_result(data, "messages")
            raw_users = result.get("users", []) if isinstance(result, dict) else []
            raw_files = result.get("files", []) if isinstance(result, dict) else []
            if isinstance(raw_users, list):
                for user in raw_users:
                    if isinstance(user, dict):
                        user_id = to_int(user.get("id"))
                        if user_id is not None:
                            users_by_id[user_id] = user
            if isinstance(raw_files, list):
                for file_item in raw_files:
                    if isinstance(file_item, dict):
                        file_id = to_int(file_item.get("id"))
                        if file_id is not None:
                            files_by_id[file_id] = file_item
            if not page_messages:
                break
            messages.extend(page_messages)
            next_last_id = to_int(page_messages[-1].get("id"))
            if next_last_id is None or next_last_id == last_id or len(page_messages) < limit:
                break
            last_id = next_last_id
            time.sleep(self.request_delay)
        return messages, users_by_id, files_by_id

    def _get_dialog_messages_for_day(self, dialog_id: str, target_date: date) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        messages: list[dict[str, Any]] = []
        users_by_id: dict[int, dict[str, Any]] = {}
        files_by_id: dict[int, dict[str, Any]] = {}
        last_id: int | None = None
        limit = 50
        max_pages = int(os.getenv("BITRIX_CHAT_MESSAGE_MAX_PAGES", "80"))
        saw_target_day = False
        for _ in range(max_pages):
            payload: dict[str, Any] = {"DIALOG_ID": dialog_id, "LIMIT": limit}
            if last_id is not None:
                payload["LAST_ID"] = last_id
            data = self.call_with_fallback("im.dialog.messages.get", payload, prefer_api=True)
            result = data.get("result", {})
            page_messages = self._extract_list_result(data, "messages")
            raw_users = result.get("users", []) if isinstance(result, dict) else []
            raw_files = result.get("files", []) if isinstance(result, dict) else []
            if isinstance(raw_users, list):
                for user in raw_users:
                    if isinstance(user, dict):
                        user_id = to_int(user.get("id"))
                        if user_id is not None:
                            users_by_id[user_id] = user
            if isinstance(raw_files, list):
                for file_item in raw_files:
                    if isinstance(file_item, dict):
                        file_id = to_int(file_item.get("id"))
                        if file_id is not None:
                            files_by_id[file_id] = file_item
            if not page_messages:
                break

            oldest_id = None
            reached_older_day = False
            for message in page_messages:
                message_id = to_int(message.get("id"))
                if message_id is not None:
                    oldest_id = message_id if oldest_id is None else min(oldest_id, message_id)
                parsed = parse_datetime(message.get("date"))
                if parsed is None:
                    continue
                message_day = parsed.date()
                if message_day == target_date:
                    saw_target_day = True
                    messages.append(message)
                elif message_day < target_date and (saw_target_day or not messages):
                    reached_older_day = True

            if oldest_id is None or reached_older_day:
                break
            if len(page_messages) < limit:
                break
            last_id = oldest_id
            time.sleep(self.request_delay)

        messages.sort(key=lambda item: to_int(item.get("id")) or 0)
        return messages, users_by_id, files_by_id

    def _list_tasks_v2(self) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        start = 0
        while True:
            payload = {
                "order": {"ACTIVITY_DATE": "desc"},
                "select": [
                    "ID",
                    "TITLE",
                    "DESCRIPTION",
                    "STATUS",
                    "REAL_STATUS",
                    "DEADLINE",
                    "CREATED_DATE",
                    "CHANGED_DATE",
                    "CLOSED_DATE",
                    "RESPONSIBLE_ID",
                    "CREATED_BY",
                    "ACCOMPLICES",
                    "AUDITORS",
                    "GROUP_ID",
                    "PRIORITY",
                    "MARK",
                ],
                "params": {
                    "WITH_RESULT_INFO": True,
                    "WITH_TIMER_INFO": True,
                    "WITH_PARSED_DESCRIPTION": True,
                },
                "start": start,
            }
            data = self.call_with_fallback("tasks.task.list", payload, prefer_api=False)
            page_tasks = self._extract_task_page(data)
            tasks.extend(page_tasks)

            next_start = data.get("next")
            if next_start is None:
                if len(page_tasks) < 50:
                    break
                start += 50
            else:
                next_value = to_int(next_start)
                if next_value is None:
                    break
                start = next_value
            time.sleep(self.request_delay)
        return tasks

    def _list_tasks_v3(self, stop_before: datetime | None = None) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []

        last_id: int | None = None
        seen_last_ids: set[int] = set()

        while True:
            payload: dict[str, Any] = {
                "order": {"id": "desc"},
                "select": [
                    "id",
                    "title",
                    "description",
                    "status",
                    "deadline",
                    "created",
                    "changed",
                    "closed",
                    "responsibleId",
                    "creatorId",
                    "chatId",
                    "groupId",
                    "priority",
                    "mark",
                    "fileIds",
                ],
            }
            if last_id is not None:
                payload["filter"] = [["id", "<", last_id]]

            data = self.call_with_retry("tasks.task.list", payload=payload, use_api=True)
            page_tasks = self._extract_task_page(data)
            if not page_tasks:
                break

            tasks.extend(page_tasks)
            if stop_before is not None:
                oldest_page_date: datetime | None = None
                for item in page_tasks:
                    dates = [
                        make_aware(parse_datetime(pick(item, "created", "CREATED_DATE"))),
                        make_aware(parse_datetime(pick(item, "changed", "CHANGED_DATE"))),
                        make_aware(parse_datetime(pick(item, "closed", "CLOSED_DATE"))),
                        make_aware(parse_datetime(pick(item, "deadline", "DEADLINE"))),
                    ]
                    valid_dates = [dt for dt in dates if dt is not None]
                    if valid_dates:
                        item_latest = max(valid_dates)
                        if oldest_page_date is None or item_latest < oldest_page_date:
                            oldest_page_date = item_latest
                if oldest_page_date is not None and oldest_page_date < stop_before:
                    break

            ids = [to_int(first_non_empty(item.get("id"), item.get("ID"))) for item in page_tasks]
            valid_ids = [item_id for item_id in ids if item_id is not None]
            if not valid_ids:
                break
            next_last_id = min(valid_ids)
            if next_last_id in seen_last_ids:
                break
            seen_last_ids.add(next_last_id)
            last_id = next_last_id

            time.sleep(self.request_delay)
        return tasks

    def _list_tasks_legacy_item(self) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        page_num = 1
        seen_page_signature: set[tuple[Any, ...]] = set()
        while True:
            payload = {
                "ORDER": {"ACTIVITY_DATE": "desc"},
                "SELECT": [
                    "ID",
                    "TITLE",
                    "DESCRIPTION",
                    "STATUS",
                    "REAL_STATUS",
                    "DEADLINE",
                    "CREATED_DATE",
                    "CHANGED_DATE",
                    "CLOSED_DATE",
                    "RESPONSIBLE_ID",
                    "CREATED_BY",
                    "ACCOMPLICES",
                    "AUDITORS",
                    "GROUP_ID",
                    "PRIORITY",
                    "MARK",
                ],
                "PARAMS": {
                    "NAV_PARAMS": {"nPageSize": 50, "iNumPage": page_num},
                },
            }
            data = self.call_with_fallback("task.item.list", payload, prefer_api=False)
            page_tasks = self._extract_task_page(data)
            if not page_tasks:
                break

            signature = tuple(
                first_non_empty(task.get("ID"), task.get("id"))
                for task in page_tasks[:5]
            )
            if signature in seen_page_signature:
                break
            seen_page_signature.add(signature)

            tasks.extend(page_tasks)
            if len(page_tasks) < 50:
                break
            page_num += 1
            time.sleep(self.request_delay)
        return tasks

    def _list_tasks_legacy_ctasks(self) -> list[dict[str, Any]]:
        payload_variants = (
            {
                "order": {"ACTIVITY_DATE": "desc"},
                "select": ["*"],
            },
            {
                "ORDER": {"ACTIVITY_DATE": "desc"},
                "SELECT": ["*"],
            },
        )
        for payload in payload_variants:
            try:
                data = self.call_with_fallback("task.ctasks.getlist", payload, prefer_api=False)
                page_tasks = self._extract_task_page(data)
                if page_tasks:
                    return page_tasks
            except Exception:  # noqa: BLE001
                continue
        return []

    @staticmethod
    def _extract_result_items(data: dict[str, Any], *keys: str) -> list[Any]:
        result = data.get("result", data)
        if isinstance(result, list):
            return result
        if not isinstance(result, dict):
            return []
        for key in keys:
            value = result.get(key)
            if isinstance(value, list):
                return value
        return []

    @staticmethod
    def _extract_item(data: dict[str, Any], *keys: str) -> dict[str, Any]:
        result = data.get("result", data)
        if isinstance(result, dict):
            for key in keys:
                value = result.get(key)
                if isinstance(value, dict):
                    return value
            return result
        return {}

    def list_tasks(self, stop_before: datetime | None = None) -> list[dict[str, Any]]:
        scopes = self.get_scopes()
        if "tasks" in scopes:
            try:
                return self._list_tasks_v3(stop_before=stop_before)
            except Exception:
                pass

        if not scopes:
            try:
                return self._list_tasks_v3(stop_before=stop_before)
            except Exception:
                pass

        methods = self.get_available_methods()
        prefer_legacy = (
            methods
            and "tasks.task.list" not in methods
            and ("task.item.list" in methods or "task.ctasks.getlist" in methods)
        )
        if not prefer_legacy:
            try:
                return self._list_tasks_v2()
            except Exception as exc:  # noqa: BLE001
                if not self._is_method_missing_error(exc):
                    raise

        for getter in (self._list_tasks_legacy_item, self._list_tasks_legacy_ctasks):
            try:
                legacy_tasks = getter()
                if legacy_tasks:
                    return legacy_tasks
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError(
            "Метод получения списка задач недоступен. Проверь права вебхука и доступные методы."
        )

    def get_task_details(self, task_id: int) -> dict[str, Any]:
        scopes = self.get_scopes()
        if "tasks" in scopes:
            payload_v3 = {
                "id": task_id,
                "select": [
                    "id",
                    "title",
                    "description",
                    "status",
                    "deadline",
                    "changed",
                    "closed",
                    "activity",
                    "responsible.id",
                    "responsible.name",
                    "creator.id",
                    "creator.name",
                    "chat.id",
                    "chat.entityId",
                    "chat.entityType",
                    "fileIds",
                    "groupId",
                    "priority",
                    "mark",
                    "containsChecklist",
                    "containsResults",
                ],
            }
            try:
                data = self.call("tasks.task.get", payload=payload_v3, use_api=True)
                return self._extract_item(data, "item", "task")
            except Exception:
                pass

        payload_new = {
            "id": task_id,
            "select": [
                "id",
                "title",
                "description",
                "status",
                "realStatus",
                "deadline",
                "createdDate",
                "changedDate",
                "closedDate",
                "responsible.id",
                "responsible.name",
                "creator.id",
                "creator.name",
                "chat.id",
                "chat.entityId",
                "chat.entityType",
                "fileIds",
                "checklist",
                "containsChecklist",
                "containsResults",
            ],
        }
        try:
            data = self.call_with_fallback("tasks.task.get", payload_new, prefer_api=True)
            return self._extract_item(data, "item", "task")
        except Exception:  # noqa: BLE001
            for method_name, payload_old in (
                ("tasks.task.get", {"taskId": task_id}),
                ("task.item.getdata", {"TASKID": task_id}),
                ("task.item.getdata", {"taskId": task_id}),
            ):
                try:
                    data = self.call_with_fallback(method_name, payload_old, prefer_api=False)
                    return self._extract_item(data, "task", "item", "result")
                except Exception:  # noqa: BLE001
                    continue
            return {}

    def enrich_tasks_v3_batch(self, base_tasks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        enriched: dict[int, dict[str, Any]] = {}
        scopes = self.get_scopes()
        if "tasks" not in scopes:
            return enriched

        select = [
            "id",
            "title",
            "description",
            "status",
            "deadline",
            "changed",
            "closed",
            "activity",
            "responsible.id",
            "responsible.name",
            "creator.id",
            "creator.name",
            "chat.id",
            "chat.entityId",
            "chat.entityType",
            "fileIds",
            "groupId",
            "priority",
            "mark",
            "containsChecklist",
            "containsResults",
        ]

        tasks_with_ids = [(extract_task_id(task), task) for task in base_tasks]
        task_ids = [task_id for task_id, _ in tasks_with_ids if task_id is not None]
        for offset in range(0, len(task_ids), 50):
            chunk = task_ids[offset : offset + 50]
            cmd: dict[str, str] = {}
            for task_id in chunk:
                params = "&".join([f"select[]={field}" for field in select])
                cmd[f"t{task_id}"] = f"tasks.task.get?id={task_id}&{params}"
            try:
                data = self.call_with_retry("batch", {"halt": 0, "cmd": cmd}, use_api=False)
            except Exception:
                continue

            result = data.get("result", {}).get("result", {})
            if not isinstance(result, dict):
                continue
            for key, value in result.items():
                task_id = to_int(key[1:]) if key.startswith("t") else None
                if task_id is None:
                    continue
                item = self._extract_item({"result": value}, "item", "task")
                if item:
                    enriched[task_id] = item
            time.sleep(self.request_delay)
        return enriched

    def get_task_results_v3(self, task_id: int) -> dict[str, Any]:
        payload_v3 = {"filter": [["taskId", "=", int(task_id)]]}
        try:
            return self.call_with_retry("tasks.task.result.list", payload_v3, use_api=True)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "result": []}

    def get_task_related_batch(
        self,
        base_tasks: list[dict[str, Any]],
        details_map: dict[int, dict[str, Any]],
    ) -> dict[int, dict[str, dict[str, Any]]]:
        related: dict[int, dict[str, dict[str, Any]]] = {}
        commands: dict[str, str] = {}

        def flush() -> None:
            nonlocal commands
            if not commands:
                return
            batch = self.batch_commands(commands)
            results = batch.get("result", {})
            errors = batch.get("errors", {})
            for key, value in results.items():
                prefix, _, task_id_text = key.partition("_")
                task_id = to_int(task_id_text)
                if task_id is None:
                    continue
                section = {
                    "c": "comments",
                    "r": "result",
                    "h": "history",
                    "cl": "checklist",
                }.get(prefix)
                if section is None:
                    continue
                related.setdefault(task_id, {})[section] = {"result": value}
            for key, value in errors.items():
                prefix, _, task_id_text = key.partition("_")
                task_id = to_int(task_id_text)
                if task_id is None:
                    continue
                section = {
                    "c": "comments",
                    "r": "result",
                    "h": "history",
                    "cl": "checklist",
                }.get(prefix)
                if section is None:
                    continue
                related.setdefault(task_id, {})[section] = {"error": str(value), "result": []}
            commands = {}
            time.sleep(self.request_delay)

        for base_task in base_tasks:
            task_id = extract_task_id(base_task)
            if task_id is None:
                continue
            details = details_map.get(task_id, {})
            chat_id = extract_chat_id(details) or to_int(pick(base_task, "chatId", "CHAT_ID"))

            if chat_id is not None:
                commands[f"c_{task_id}"] = "im.dialog.messages.get?" + urlencode(
                    {"DIALOG_ID": f"chat{chat_id}", "LIMIT": 200}
                )
            else:
                related.setdefault(task_id, {})["comments"] = {
                    "warning": "Нет chat.id для чтения переписки задачи",
                    "result": [],
                }

            commands[f"r_{task_id}"] = "tasks.task.result.list?" + urlencode({"taskId": task_id})
            commands[f"h_{task_id}"] = "tasks.task.history.list?" + urlencode({"taskId": task_id})
            commands[f"cl_{task_id}"] = "task.checklistitem.getlist?" + urlencode({"TASKID": task_id})

            if len(commands) >= 50:
                flush()
        flush()
        return related

    def get_task_results(self, task_id: int) -> dict[str, Any]:
        scopes = self.get_scopes()
        if "tasks" in scopes:
            payload_v3 = {"filter": [["taskId", "=", int(task_id)]]}
            try:
                return self.call_with_retry("tasks.task.result.list", payload_v3, use_api=True)
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc), "result": []}

        payload = {"taskId": task_id}
        try:
            return self.call_with_fallback("tasks.task.result.list", payload, prefer_api=True)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "result": []}

    def get_task_history(self, task_id: int) -> dict[str, Any]:
        scopes = self.get_scopes()
        if "task" not in scopes and "tasks" in scopes:
            return {"warning": "История задач доступна в REST v2 (`task` scope)", "result": []}
        payload = {"taskId": task_id}
        try:
            return self.call_with_fallback("tasks.task.history.list", payload, prefer_api=False)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "result": []}

    def get_task_checklist(self, task_id: int) -> dict[str, Any]:
        scopes = self.get_scopes()
        if "task" not in scopes and "tasks" in scopes:
            return {"warning": "Чек-лист доступен в REST v2 (`task` scope)", "result": []}
        for payload in ({"TASKID": task_id}, {"taskId": task_id}):
            try:
                return self.call_with_fallback("task.checklistitem.getlist", payload, prefer_api=False)
            except Exception:  # noqa: BLE001
                continue
        return {"error": "checklist unavailable", "result": []}

    def get_task_comments(self, task_id: int, chat_id: int | None) -> dict[str, Any]:
        # Preferred path for new task card.
        if chat_id is not None:
            payloads = (
                {"DIALOG_ID": f"chat{chat_id}", "LIMIT": 200},
                {"dialogId": f"chat{chat_id}", "limit": 200},
            )
            for payload in payloads:
                try:
                    data = self.call_with_fallback("im.dialog.messages.get", payload, prefer_api=True)
                    data["source"] = "im.dialog.messages.get"
                    return data
                except Exception:  # noqa: BLE001
                    continue

        # Fallback for legacy task card / legacy methods.
        legacy_calls = (
            ("task.commentitem.getlist", {"TASKID": task_id}),
            ("task.commentitem.getlist", {"taskId": task_id}),
            ("task.ctaskcommentitem.getlist", {"TASKID": task_id}),
            ("task.ctaskcommentitem.getlist", {"taskId": task_id}),
        )
        for method_name, payload in legacy_calls:
            try:
                data = self.call_with_fallback(method_name, payload, prefer_api=False)
                data["source"] = method_name
                return data
            except Exception:  # noqa: BLE001
                continue

        if chat_id is None:
            return {"warning": "Комментарии недоступны: нет chat id и нет legacy-метода", "result": []}
        return {"error": "Сообщения чата недоступны", "result": []}

    def get_user(self, user_id: int | None) -> dict[str, Any]:
        if user_id is None:
            return {}
        if user_id in self.user_cache:
            return self.user_cache[user_id]

        payloads = (
            {"ID": user_id},
            {"id": user_id},
            {"filter": {"ID": user_id}},
        )
        profile: dict[str, Any] = {}
        for payload in payloads:
            for _ in range(2):
                try:
                    data = self.call_with_fallback("user.get", payload, prefer_api=False)
                    result = data.get("result", [])
                    if isinstance(result, list) and result:
                        profile = result[0] if isinstance(result[0], dict) else {}
                        break
                    if isinstance(result, dict):
                        profile = result
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(0.1)
                    continue
            if profile:
                break
        if profile:
            self.user_cache[user_id] = profile
        return profile

    def get_department(self, department_id: int | None) -> dict[str, Any]:
        if department_id is None:
            return {}
        if department_id in self.department_cache:
            return self.department_cache[department_id]

        payloads = (
            {"ID": department_id},
            {"id": department_id},
            {"filter": {"ID": department_id}},
        )
        department: dict[str, Any] = {}
        for payload in payloads:
            try:
                data = self.call_with_fallback("department.get", payload, prefer_api=False)
                result = data.get("result", [])
                if isinstance(result, list) and result:
                    department = result[0] if isinstance(result[0], dict) else {}
                    break
                if isinstance(result, dict):
                    department = result
                    break
            except Exception:  # noqa: BLE001
                continue
        self.department_cache[department_id] = department
        return department
def extract_task_id(task: dict[str, Any]) -> int | None:
    return to_int(pick(task, "id", "ID"))
def extract_chat_id(details: dict[str, Any]) -> int | None:
    chat_direct = pick(details, "chat.id", "CHAT_ID", "chatId")
    if chat_direct is not None:
        return to_int(chat_direct)
    chat = details.get("chat")
    if isinstance(chat, dict):
        return to_int(first_non_empty(chat.get("id"), chat.get("ID")))
    return None
def extract_files(base_task: dict[str, Any], details: dict[str, Any]) -> list[Any]:
    return to_list(
        first_non_empty(
            pick(details, "fileIds", "files", "FILES"),
            pick(base_task, "UF_TASK_WEBDAV_FILES", "fileIds"),
        )
    )
def resolve_person(client: BitrixClient, person_id: int | None, fallback_name: Any = None) -> dict[str, Any]:
    profile = client.get_user(person_id) if person_id is not None else {}
    display_name = format_person_name(profile, fallback_name=fallback_name)

    department_ids = [to_int(dep) for dep in to_list(pick(profile, "UF_DEPARTMENT"))]
    normalized_departments = [dep for dep in department_ids if dep is not None]

    departments: list[dict[str, Any]] = []
    manager_name: str | None = None
    manager_id: int | None = None
    for dep_id in normalized_departments:
        department = client.get_department(dep_id)
        dep_name = first_non_empty(pick(department, "NAME", "name"), f"Отдел {dep_id}")
        dep_head_id = to_int(pick(department, "UF_HEAD", "head"))
        departments.append({"id": dep_id, "name": dep_name, "head_id": dep_head_id})
        if dep_head_id and manager_id is None:
            manager_id = dep_head_id
            manager_profile = client.get_user(dep_head_id)
            manager_name = format_person_name(manager_profile)
            if not manager_name:
                manager_name = f"Пользователь {dep_head_id}"

    return {
        "id": person_id,
        "name": display_name or (f"Пользователь {person_id}" if person_id else None),
        "email": pick(profile, "EMAIL", "email"),
        "departments": departments,
        "manager": {"id": manager_id, "name": manager_name},
    }
def extract_task_created_date(
    details: dict[str, Any],
    base_task: dict[str, Any],
    history_raw: dict[str, Any] | None = None,
) -> Any:
    direct_value = first_non_empty(
        pick(details, "createdDate", "created", "CREATED_DATE", "created_date", "dateCreate", "DATE_CREATE"),
        pick(base_task, "createdDate", "created", "CREATED_DATE", "created_date", "dateCreate", "DATE_CREATE"),
    )
    if direct_value:
        return direct_value

    history_items = extract_collection(history_raw or {}, "history", "items", "list")
    dated_items = [
        item for item in history_items
        if isinstance(item, dict) and first_non_empty(pick(item, "createdDate", "created", "date"))
    ]
    for item in dated_items:
        if str(item.get("field") or "").strip().upper() == "NEW":
            return first_non_empty(pick(item, "createdDate", "created", "date"))
    if dated_items:
        return min(
            (first_non_empty(pick(item, "createdDate", "created", "date")) for item in dated_items),
            key=lambda value: make_aware(parse_datetime(value)) or datetime.max.replace(tzinfo=LOCAL_TZ),
        )
    return None
def build_task_record(client: BitrixClient, base_task: dict[str, Any]) -> dict[str, Any]:
    task_id = extract_task_id(base_task)
    if task_id is None:
        raise ValueError("Получена задача без ID")

    details = client.get_task_details(task_id)
    results_raw = client.get_task_results(task_id)
    history_raw = client.get_task_history(task_id)
    checklist_raw = client.get_task_checklist(task_id)
    chat_id = extract_chat_id(details)
    comments_raw = client.get_task_comments(task_id, chat_id)

    creator_id = to_int(
        first_non_empty(
            pick(details, "creator.id", "creatorId", "CREATED_BY"),
            pick(base_task, "CREATED_BY", "createdBy", "creatorId"),
        )
    )
    responsible_id = to_int(
        first_non_empty(
            pick(details, "responsible.id", "responsibleId", "RESPONSIBLE_ID"),
            pick(base_task, "RESPONSIBLE_ID", "responsibleId", "responsibleId"),
        )
    )

    creator = resolve_person(client, creator_id, fallback_name=pick(details, "creator.name"))
    responsible = resolve_person(client, responsible_id, fallback_name=pick(details, "responsible.name"))

    status_code = first_non_empty(
        pick(details, "status", "realStatus", "STATUS", "REAL_STATUS"),
        pick(base_task, "REAL_STATUS", "STATUS", "status"),
    )
    status = normalize_status(status_code)

    deadline = first_non_empty(
        pick(details, "deadline", "DEADLINE"),
        pick(base_task, "DEADLINE", "deadline"),
    )
    closed_date = first_non_empty(
        pick(details, "closedDate", "closed", "CLOSED_DATE"),
        pick(base_task, "CLOSED_DATE", "closed"),
    )

    departments = responsible.get("departments", [])
    manager = responsible.get("manager", {})

    return {
        "task_id": task_id,
        "title": first_non_empty(pick(details, "title", "TITLE"), pick(base_task, "TITLE", "title")),
        "description": first_non_empty(
            pick(details, "description", "DESCRIPTION"),
            pick(base_task, "DESCRIPTION", "description"),
        ),
        "creator": {
            "id": creator.get("id"),
            "name": creator.get("name"),
            "email": creator.get("email"),
        },
        "responsible": {
            "id": responsible.get("id"),
            "name": responsible.get("name"),
            "email": responsible.get("email"),
        },
        "department": [dep.get("name") for dep in departments if dep.get("name")],
        "manager": manager,
        "deadline": deadline,
        "closed_date": closed_date,
        "status": status,
        "result": {
            "items": extract_collection(results_raw, "items", "results", "list"),
            "raw": results_raw,
        },
        "comments": {
            "chat_id": chat_id,
            "items": extract_collection(comments_raw, "messages", "items", "list"),
            "raw": comments_raw,
        },
        "checklist": {
            "items": extract_collection(checklist_raw, "items", "list", "checklist"),
            "raw": checklist_raw,
        },
        "history": {
            "items": extract_collection(history_raw, "history", "items", "list"),
            "raw": history_raw,
        },
        "files": extract_files(base_task, details),
        "priority": first_non_empty(
            pick(details, "priority", "PRIORITY"),
            pick(base_task, "PRIORITY", "priority"),
        ),
        "mark": first_non_empty(pick(details, "mark", "MARK"), pick(base_task, "MARK", "mark")),
        "dates": {
            "created": extract_task_created_date(details, base_task, history_raw),
            "changed": first_non_empty(
                pick(details, "changedDate", "changed", "CHANGED_DATE"),
                pick(base_task, "CHANGED_DATE", "changed"),
            ),
        },
        "raw": {
            "base": base_task,
            "details": details,
        },
    }
def upsert_task_records(records: list[dict[str, Any]], sync_run_id: int | None = None) -> None:
    now = datetime.now()
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for record in records:
                    task_id = to_int(record.get("task_id"))
                    if task_id is None or "error" in record:
                        continue
                    status = record.get("status", {}) or {}
                    responsible = record.get("responsible", {}) or {}
                    creator = record.get("creator", {}) or {}
                    dates = record.get("dates", {}) or {}
                    responsible_bitrix_id = to_int(responsible.get("id"))
                    creator_bitrix_id = to_int(creator.get("id"))
                    responsible_id = pg_user_id_by_bitrix(cur, responsible_bitrix_id)
                    creator_id = pg_user_id_by_bitrix(cur, creator_bitrix_id)
                    cur.execute(
                        """
                        INSERT INTO bitrix_tasks (
                            bitrix_task_id, title, description, status, status_name, priority,
                            creator_id, creator_bitrix_user_id, responsible_id, responsible_bitrix_user_id,
                            deadline_at, created_at_bitrix, updated_at_bitrix, closed_at_bitrix,
                            raw_json, synced_at
                        ) VALUES (
                            %(bitrix_task_id)s, %(title)s, %(description)s, %(status)s, %(status_name)s,
                            %(priority)s, %(creator_id)s, %(creator_bitrix_user_id)s,
                            %(responsible_id)s, %(responsible_bitrix_user_id)s, %(deadline_at)s,
                            %(created_at_bitrix)s, %(updated_at_bitrix)s, %(closed_at_bitrix)s,
                            %(raw_json)s, %(synced_at)s
                        )
                        ON CONFLICT (bitrix_task_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            description = EXCLUDED.description,
                            status = EXCLUDED.status,
                            status_name = EXCLUDED.status_name,
                            priority = EXCLUDED.priority,
                            creator_id = EXCLUDED.creator_id,
                            creator_bitrix_user_id = EXCLUDED.creator_bitrix_user_id,
                            responsible_id = EXCLUDED.responsible_id,
                            responsible_bitrix_user_id = EXCLUDED.responsible_bitrix_user_id,
                            deadline_at = EXCLUDED.deadline_at,
                            created_at_bitrix = EXCLUDED.created_at_bitrix,
                            updated_at_bitrix = EXCLUDED.updated_at_bitrix,
                            closed_at_bitrix = EXCLUDED.closed_at_bitrix,
                            raw_json = EXCLUDED.raw_json,
                            synced_at = EXCLUDED.synced_at,
                            updated_at = now()
                        RETURNING id
                        """,
                        {
                            "bitrix_task_id": task_id,
                            "title": record.get("title") or f"Task {task_id}",
                            "description": record.get("description"),
                            "status": str(status.get("code")) if status.get("code") is not None else None,
                            "status_name": status.get("label"),
                            "priority": record.get("priority"),
                            "creator_id": creator_id,
                            "creator_bitrix_user_id": creator_bitrix_id,
                            "responsible_id": responsible_id,
                            "responsible_bitrix_user_id": responsible_bitrix_id,
                            "deadline_at": parse_datetime(record.get("deadline")),
                            "created_at_bitrix": parse_datetime(dates.get("created")),
                            "updated_at_bitrix": parse_datetime(dates.get("changed")),
                            "closed_at_bitrix": parse_datetime(record.get("closed_date")),
                            "raw_json": pg_json(record),
                            "synced_at": now,
                        },
                    )
                    bitrix_task_uuid = cur.fetchone()["id"]
                    if sync_run_id:
                        cur.execute(
                            """
                            INSERT INTO bitrix_task_snapshots (
                                task_id, bitrix_task_id, sync_run_id, snapshot_date, status,
                                priority, responsible_id, deadline_at, closed_at_bitrix, raw_json
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (task_id, sync_run_id) DO NOTHING
                            """,
                            (
                                bitrix_task_uuid,
                                task_id,
                                sync_run_id,
                                date.today(),
                                str(status.get("code")) if status.get("code") is not None else None,
                                record.get("priority"),
                                responsible_id,
                                parse_datetime(record.get("deadline")),
                                parse_datetime(record.get("closed_date")),
                                pg_json(record),
                            ),
                        )
                    for role, user_uuid, bitrix_id in (
                        ("creator", creator_id, creator_bitrix_id),
                        ("responsible", responsible_id, responsible_bitrix_id),
                    ):
                        if user_uuid:
                            cur.execute(
                                """
                                INSERT INTO bitrix_task_members (task_id, user_id, bitrix_user_id, role)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (task_id, user_id, role) DO NOTHING
                                """,
                                (bitrix_task_uuid, user_uuid, bitrix_id, role),
                            )
    return
def delete_task_records(task_ids: list[int]) -> int:
    if not task_ids:
        return 0
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bitrix_tasks WHERE bitrix_task_id = ANY(%s)", (task_ids,))
            return cur.rowcount
@app.route("/bitrix/events/team/<secret>", methods=["GET", "POST"])
def bitrix_team_event_webhook(secret: str):
    if not bitrix_event_secret_valid(secret):
        return jsonify({"error": "forbidden"}), 403
    if request.method == "GET":
        return jsonify({"ok": True, "message": "Bitrix team event endpoint is ready."})

    payload = flatten_request_payload()
    event_name = normalize_bitrix_event_name(first_non_empty(payload.get("event"), payload.get("EVENT")))
    supported_events = {
        "onuseradd", "onuserupdate", "onuserdelete",
        "onafteruseradd", "onafteruserupdate", "onafteruserdelete",
        "ondepartmentadd", "ondepartmentupdate", "ondepartmentdelete",
        "onafterdepartmentadd", "onafterdepartmentupdate", "onafterdepartmentdelete",
    }
    if event_name and event_name.lower() not in supported_events:
        return jsonify({"ok": True, "event": event_name, "ignored": True, "reason": "unsupported_event"}), 200

    webhook_base = os.getenv("BITRIX_WEBHOOK_BASE", "").strip()
    if not webhook_base:
        return jsonify({"ok": False, "error": "BITRIX_WEBHOOK_BASE is not configured."}), 500
    try:
        result = sync_bitrix_team(webhook_base)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({
        "ok": True,
        "event": event_name,
        "processed_inline": True,
        "saved": result.get("saved", 0),
        "scanned": result.get("scanned", 0),
    })
@app.route("/bitrix/events/tasks/<secret>", methods=["GET", "POST"])
def bitrix_task_event_webhook(secret: str):
    if not bitrix_event_secret_valid(secret):
        return jsonify({"error": "forbidden"}), 403
    if request.method == "GET":
        return jsonify({"ok": True, "message": "Bitrix task event endpoint is ready."})

    payload = flatten_request_payload()
    event_name = normalize_bitrix_event_name(first_non_empty(payload.get("event"), payload.get("EVENT")))
    # In-task agent: an employee named an agent in a task comment. Fires on EVERY comment company-
    # wide, so ACK immediately and do ALL work (fetch/detect/access/run) in a background thread —
    # the guards + kill-switch live inside _b24_handle_task_comment_event.
    if (event_name or "").lower() in {"ontaskcommentadd", "ontaskcommentupdate"}:
        c_task_id = extract_bitrix_comment_event_task_id(payload)
        comment_id = _extract_bitrix_event_comment_id(payload)
        if not c_task_id or not comment_id:
            return jsonify({"ok": True, "event": event_name, "ignored": True, "reason": "no_ids"}), 200

        def _run_task_comment() -> None:
            try:
                import b24bot
                b24bot._b24_handle_task_comment_event(int(c_task_id), int(comment_id))
            except Exception:  # noqa: BLE001
                import logging as _lg
                _lg.getLogger(__name__).warning("task-comment handler failed", exc_info=True)

        import threading as _threading
        _threading.Thread(target=_run_task_comment, daemon=True).start()
        return jsonify({"ok": True, "event": event_name, "accepted": True}), 200
    if event_name not in {"OnTaskAdd", "OnTaskUpdate", "OnTaskDelete"}:
        return jsonify({"ok": True, "event": event_name, "ignored": True, "reason": "unsupported_event"})
    task_id = extract_bitrix_event_task_id(payload)
    if task_id is None or task_id <= 0:
        return jsonify({"ok": True, "event": event_name, "ignored": True, "reason": "task_id_not_found"}), 200

    event_id = enqueue_bitrix_task_event(event_name, task_id, payload)
    inline = str(os.getenv("BITRIX_TASK_EVENT_PROCESS_INLINE", "1")).strip().lower() not in {"0", "false", "no", "off"}
    process_result = process_bitrix_task_event_queue(limit=5) if inline else None
    return jsonify({
        "ok": True,
        "event_id": event_id,
        "event": event_name,
        "task_id": task_id,
        "queued": True,
        "processed_inline": inline,
        "process_result": process_result,
    })
