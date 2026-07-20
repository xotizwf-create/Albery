"""Zoom integration: OAuth API client, recordings/participants/transcripts pull,
VTT/markdown parsing, operational-task extraction, the recording-event queue,
export links and the inbound Zoom webhook.

Moved verbatim out of app.py (2026-07-02 refactor, step Sh2.5 - move-only).
Registers its routes on the shared Flask `app` at import time.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import subprocess
import time

from datetime import date
from datetime import datetime
from datetime import timedelta
from flask import abort
from flask import jsonify
from flask import request
from flask import send_file
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import unquote
import requests

from config import (
    EXPORT_DIR,
    MSK_TZ,
)

from utils import (
    RU_MONTH_NAMES,
    first_non_empty,
    first_text_value,
    format_date_ru,
    format_datetime_msk_label,
    format_datetime_ru,
    iso_or_none,
    parse_datetime,
    pick,
    safe_parse_date,
    sentence_case_ru,
    to_int,
)

from app import (  # shared Flask app + db glue still living in app.py
    ZoneInfo,
    app,
    has_request_context,
    pg_connect,
    pg_json,
    pg_table_exists,
    postgres_enabled,
)


def ensure_zoom_schema() -> None:
    if not postgres_enabled():
        return
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                required_tables = ("zoom_calls", "zoom_call_participants", "zoom_call_transcript_segments")
                for table_name in required_tables:
                    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (f"public.{table_name}",))
                    row = cur.fetchone()
                    if not row or not row["exists"]:
                        raise RuntimeError(
                            f"PostgreSQL table public.{table_name} is missing. "
                            "Apply database migrations before using Zoom calls."
                        )
def zoom_oauth_url() -> str:
    value = (os.getenv("ZOOM_OAUTH_URL") or "https://zoom.us/oauth/token").strip().rstrip("/")
    if not value.endswith("/oauth/token"):
        value = f"{value}/oauth/token"
    return value
def zoom_api_base_url() -> str:
    return (os.getenv("ZOOM_API_BASE_URL") or "https://api.zoom.us/v2").strip().rstrip("/")
def zoom_access_token(account_key: str = "ZOOM_ACC2") -> str:
    account_id = os.getenv(f"{account_key}_ACCOUNT_ID", "").strip()
    client_id = os.getenv(f"{account_key}_CLIENT_ID", "").strip()
    client_secret = os.getenv(f"{account_key}_CLIENT_SECRET", "").strip()
    missing = [
        key
        for key, value in {
            f"{account_key}_ACCOUNT_ID": account_id,
            f"{account_key}_CLIENT_ID": client_id,
            f"{account_key}_CLIENT_SECRET": client_secret,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Не заданы переменные Zoom: {', '.join(missing)}")
    response = requests.post(
        zoom_oauth_url(),
        params={"grant_type": "account_credentials", "account_id": account_id},
        auth=(client_id, client_secret),
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Zoom OAuth error {response.status_code}: {response.text[:500]}")
    return str(response.json()["access_token"])
def zoom_session(account_key: str = "ZOOM_ACC2") -> requests.Session:
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {zoom_access_token(account_key)}"})
    return session
def parse_zoom_datetime(value: Any, tz_name: str = "Europe/Moscow") -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name or "Europe/Moscow"))
    return parsed
def parse_zoom_vtt(text: str) -> list[dict[str, Any]]:
    lines = text.replace("\ufeff", "").splitlines()
    cues: list[dict[str, Any]] = []
    time_re = re.compile(r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})")
    i = 0
    while i < len(lines):
        match = time_re.search(lines[i].strip())
        if not match:
            i += 1
            continue
        start_offset = match.group("start")
        end_offset = match.group("end")
        i += 1
        payload: list[str] = []
        while i < len(lines) and lines[i].strip():
            payload.append(lines[i].strip())
            i += 1
        raw_text = re.sub(r"<[^>]+>", "", " ".join(payload)).strip()
        speaker = None
        cue_text = raw_text
        speaker_match = re.match(r"^([^:]{1,80}):\s*(.*)$", raw_text)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            cue_text = speaker_match.group(2).strip()
        if cue_text:
            cues.append(
                {
                    "start": start_offset,
                    "end": end_offset,
                    "speaker": speaker,
                    "text": cue_text,
                }
            )
        i += 1
    return cues
def zoom_plain_transcript(cues: list[dict[str, Any]]) -> str:
    lines = []
    for cue in cues:
        prefix = f"[{cue.get('start')} - {cue.get('end')}]"
        if cue.get("speaker"):
            prefix += f" {cue['speaker']}:"
        lines.append(f"{prefix} {cue.get('text') or ''}".strip())
    return "\n".join(lines)
def zoom_encoded_uuid(uuid_value: str) -> str:
    # Zoom requires double-encoding meeting UUIDs containing "/" or "==".
    return quote(quote(uuid_value, safe=""), safe="")
def zoom_list_users(session: requests.Session) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    page = ""
    while True:
        response = session.get(
            f"{zoom_api_base_url()}/users",
            params={"page_size": 300, "next_page_token": page},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"Zoom users error {response.status_code}: {response.text[:500]}")
        payload = response.json()
        users.extend(payload.get("users") or [])
        page = payload.get("next_page_token") or ""
        if not page:
            return users
def zoom_list_recordings(session: requests.Session, user_id: str, date_from: date, date_to: date) -> list[dict[str, Any]]:
    meetings: list[dict[str, Any]] = []
    page = ""
    while True:
        response = session.get(
            f"{zoom_api_base_url()}/users/{user_id}/recordings",
            params={
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
                "page_size": 300,
                "next_page_token": page,
            },
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"Zoom recordings error {response.status_code}: {response.text[:500]}")
        payload = response.json()
        meetings.extend(payload.get("meetings") or [])
        page = payload.get("next_page_token") or ""
        if not page:
            return meetings
def zoom_list_participants(session: requests.Session, meeting_uuid: str) -> tuple[list[dict[str, Any]], str | None]:
    participants: list[dict[str, Any]] = []
    page = ""
    try:
        while True:
            response = session.get(
                f"{zoom_api_base_url()}/past_meetings/{zoom_encoded_uuid(meeting_uuid)}/participants",
                params={"page_size": 300, "next_page_token": page},
                timeout=30,
            )
            if not response.ok:
                return participants, f"HTTP {response.status_code}: {response.text[:300]}"
            payload = response.json()
            participants.extend(payload.get("participants") or [])
            page = payload.get("next_page_token") or ""
            if not page:
                return participants, None
    except requests.RequestException as exc:
        return participants, str(exc)
def zoom_get_recording(session: requests.Session, meeting_uuid: str) -> dict[str, Any]:
    response = session.get(
        f"{zoom_api_base_url()}/meetings/{zoom_encoded_uuid(meeting_uuid)}/recordings",
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Zoom recording error {response.status_code}: {response.text[:500]}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Zoom recording response has unexpected format")
    return payload
def zoom_transcript_files(meeting: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        file_item
        for file_item in (meeting.get("recording_files") or [])
        if file_item.get("file_type") == "TRANSCRIPT"
        or file_item.get("recording_type") == "audio_transcript"
    ]
def zoom_transcript_file_key(file_item: dict[str, Any]) -> str:
    return str(
        first_non_empty(
            file_item.get("id"),
            file_item.get("recording_id"),
            file_item.get("recording_start"),
            file_item.get("download_url"),
        )
        or ""
    ).strip()
def stored_zoom_transcript_file_keys(raw_json: Any) -> set[str]:
    if not isinstance(raw_json, dict):
        return set()
    keys: set[str] = set()
    for item in raw_json.get("transcripts") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("file_id") or "").strip()
        if not key and isinstance(item.get("file"), dict):
            key = zoom_transcript_file_key(item["file"])
        if key:
            keys.add(key)
    return keys
def load_existing_zoom_call_state(cur: Any, zoom_uuid: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT id, transcript_text, raw_json
        FROM zoom_calls
        WHERE zoom_uuid = %s
        """,
        (zoom_uuid,),
    )
    row = cur.fetchone()
    return dict(row) if row else None
def zoom_meeting_needs_transcript_sync(
    transcript_files: list[dict[str, Any]],
    existing: dict[str, Any] | None,
    force: bool = False,
) -> bool:
    if force or existing is None:
        return True
    current_keys = {zoom_transcript_file_key(item) for item in transcript_files}
    current_keys.discard("")
    stored_keys = stored_zoom_transcript_file_keys(existing.get("raw_json"))
    transcript_text = str(existing.get("transcript_text") or "").strip()
    if current_keys and not transcript_text:
        return True
    return current_keys != stored_keys
def upsert_zoom_recording_meeting(
    cur: Any,
    session: requests.Session,
    account_key: str,
    user: dict[str, Any],
    meeting: dict[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    meeting_uuid = str(meeting.get("uuid") or "")
    if not meeting_uuid:
        return {"status": "skipped", "reason": "missing_uuid"}
    tz_name = str(meeting.get("timezone") or "Europe/Moscow")
    start_utc = parse_zoom_datetime(meeting.get("start_time"), "UTC")
    if not start_utc:
        return {"status": "skipped", "uuid": meeting_uuid, "reason": "missing_start_time"}
    start_msk = start_utc.astimezone(MSK_TZ)
    duration_min = to_int(meeting.get("duration")) or 0
    end_msk = start_msk + timedelta(minutes=duration_min) if duration_min else None
    transcript_files = zoom_transcript_files(meeting)
    existing = load_existing_zoom_call_state(cur, meeting_uuid)
    needs_transcript_sync = zoom_meeting_needs_transcript_sync(transcript_files, existing, force=force)
    if existing is not None and not needs_transcript_sync:
        return {
            "status": "skipped_unchanged",
            "uuid": meeting_uuid,
            "call_id": str(existing.get("id") or ""),
            "transcript_files": len(transcript_files),
        }

    all_cues: list[dict[str, Any]] = []
    transcript_payload: list[dict[str, Any]] = []
    synced_transcript_files = 0
    for segment_index, file_item in enumerate(
        sorted(transcript_files, key=lambda item: item.get("recording_start") or ""),
        start=1,
    ):
        download_url = file_item.get("download_url")
        if not download_url:
            continue
        response = session.get(download_url, timeout=60)
        if not response.ok:
            transcript_payload.append(
                {
                    "file": file_item,
                    "error": f"HTTP {response.status_code}: {response.text[:200]}",
                }
            )
            continue
        cues = parse_zoom_vtt(response.text)
        for cue in cues:
            cue["segment_index"] = segment_index
        all_cues.extend(cues)
        transcript_payload.append(
            {
                "file_id": file_item.get("id"),
                "file_type": file_item.get("file_type"),
                "recording_type": file_item.get("recording_type"),
                "recording_start": file_item.get("recording_start"),
                "recording_end": file_item.get("recording_end"),
                "cue_count": len(cues),
            }
        )
        synced_transcript_files += 1

    participants, participant_error = zoom_list_participants(session, meeting_uuid)
    if not participants:
        participants = [
            {
                "name": user.get("display_name") or user.get("first_name") or user.get("email"),
                "user_email": user.get("email"),
                "user_id": user.get("id"),
                "role": "host_fallback",
            }
        ]

    raw_json = dict(meeting)
    raw_json["zoom_user"] = user
    raw_json["transcripts"] = transcript_payload
    if participant_error:
        raw_json["participants_error"] = participant_error

    cur.execute(
        """
        INSERT INTO zoom_calls (
            zoom_account_key, zoom_user_email, zoom_meeting_id, zoom_uuid,
            topic, technical_topic, start_time_utc, start_time_msk,
            end_time_msk, call_date, duration_min, timezone, share_url,
            transcript_text, transcript_format, raw_json, synced_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'vtt', %s, now(), now())
        ON CONFLICT (zoom_uuid) DO UPDATE SET
            zoom_account_key = EXCLUDED.zoom_account_key,
            zoom_user_email = EXCLUDED.zoom_user_email,
            zoom_meeting_id = EXCLUDED.zoom_meeting_id,
            topic = EXCLUDED.topic,
            technical_topic = EXCLUDED.technical_topic,
            start_time_utc = EXCLUDED.start_time_utc,
            start_time_msk = EXCLUDED.start_time_msk,
            end_time_msk = EXCLUDED.end_time_msk,
            call_date = EXCLUDED.call_date,
            duration_min = EXCLUDED.duration_min,
            timezone = EXCLUDED.timezone,
            share_url = EXCLUDED.share_url,
            transcript_text = EXCLUDED.transcript_text,
            transcript_format = EXCLUDED.transcript_format,
            raw_json = EXCLUDED.raw_json,
            synced_at = now(),
            updated_at = now()
        RETURNING id
        """,
        (
            account_key,
            user.get("email") or meeting.get("host_email"),
            to_int(meeting.get("id")),
            meeting_uuid,
            meeting.get("topic"),
            meeting.get("topic"),
            start_utc,
            start_msk,
            end_msk,
            start_msk.date(),
            duration_min,
            tz_name,
            meeting.get("share_url"),
            zoom_plain_transcript(all_cues),
            pg_json(raw_json),
        ),
    )
    call_id = cur.fetchone()["id"]
    cur.execute("DELETE FROM zoom_call_participants WHERE call_id = %s", (call_id,))
    cur.execute("DELETE FROM zoom_call_transcript_segments WHERE call_id = %s", (call_id,))
    for participant in participants:
        cur.execute(
            """
            INSERT INTO zoom_call_participants (
                call_id, participant_name, participant_email, participant_user_id,
                join_time, leave_time, duration_seconds, raw_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                call_id,
                participant.get("name"),
                participant.get("user_email") or participant.get("email"),
                str(participant.get("user_id") or participant.get("id") or ""),
                parse_zoom_datetime(participant.get("join_time"), tz_name),
                parse_zoom_datetime(participant.get("leave_time"), tz_name),
                to_int(participant.get("duration")),
                pg_json(participant),
            ),
        )
    for cue_index, cue in enumerate(all_cues, start=1):
        cur.execute(
            """
            INSERT INTO zoom_call_transcript_segments (
                call_id, segment_index, cue_index, start_offset, end_offset, speaker, text, raw_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (call_id, segment_index, cue_index) DO UPDATE SET
                start_offset = EXCLUDED.start_offset,
                end_offset = EXCLUDED.end_offset,
                speaker = EXCLUDED.speaker,
                text = EXCLUDED.text,
                raw_json = EXCLUDED.raw_json
            """,
            (
                call_id,
                int(cue.get("segment_index") or 1),
                cue_index,
                cue.get("start"),
                cue.get("end"),
                cue.get("speaker"),
                cue.get("text") or "",
                pg_json(cue),
            ),
        )
    return {
        "status": "synced",
        "uuid": meeting_uuid,
        "call_id": str(call_id),
        "transcript_files_synced": synced_transcript_files,
        "segments_synced": len(all_cues),
        "participant_error": participant_error,
    }
def sync_zoom_recording_by_uuid(meeting_uuid: str, account_key: str = "ZOOM_ACC2", force: bool = False) -> dict[str, Any]:
    ensure_zoom_schema()
    session = zoom_session(account_key)
    meeting = zoom_get_recording(session, meeting_uuid)
    user = {
        "email": meeting.get("host_email"),
        "id": meeting.get("host_id"),
        "display_name": meeting.get("host_email"),
    }
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = upsert_zoom_recording_meeting(cur, session, account_key, user, meeting, force=force)
    return result
def ensure_zoom_event_queue_schema() -> None:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            if not pg_table_exists(cur, "zoom_recording_events"):
                raise RuntimeError("PostgreSQL table public.zoom_recording_events is missing. Apply database migrations before processing Zoom events.")
def zoom_event_secret_valid(secret: str) -> bool:
    expected = os.getenv("ZOOM_EVENT_SECRET", "").strip()
    if not expected:
        return False
    return hmac.compare_digest(secret, expected)
def zoom_webhook_secret_token() -> str:
    return os.getenv("ZOOM_WEBHOOK_SECRET_TOKEN", "").strip()
def zoom_webhook_validation_response(payload: dict[str, Any]) -> dict[str, str] | None:
    plain_token = pick(payload, "payload.plainToken", "plainToken")
    secret_token = zoom_webhook_secret_token()
    if not plain_token or not secret_token:
        return None
    encrypted = hmac.new(
        secret_token.encode("utf-8"),
        str(plain_token).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"plainToken": str(plain_token), "encryptedToken": encrypted}
def extract_zoom_event_uuid(payload: dict[str, Any]) -> str:
    value = first_non_empty(
        pick(payload, "payload.object.uuid", "payload.object.UUID"),
        pick(payload, "object.uuid", "object.UUID"),
        pick(payload, "uuid", "UUID"),
        pick(payload, "payload.object.id", "object.id", "id"),
    )
    return str(value or "").strip()
def enqueue_zoom_recording_event(event_name: str, zoom_uuid: str, payload: dict[str, Any]) -> str:
    ensure_zoom_event_queue_schema()
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO zoom_recording_events (event_name, zoom_uuid, payload)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (event_name, zoom_uuid, pg_json(payload)),
                )
                return str(cur.fetchone()["id"])
def trigger_hermes_zoom_watchdog_from_event() -> dict[str, Any]:
    """Kick the existing Hermes zoom-to-tasks watchdog after a Zoom transcript event.

    The watchdog remains the source of truth for report generation and Telegram
    preview. This helper only starts it immediately instead of waiting for cron.
    """
    enabled = str(os.getenv("ZOOM_EVENT_TRIGGER_HERMES", "1")).strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        return {"triggered": False, "reason": "disabled"}

    script_path = Path(os.getenv("HERMES_ZOOM_WATCHDOG_PATH", "/root/.hermes/scripts/zoom_watchdog.sh"))
    if not script_path.exists():
        return {"triggered": False, "reason": "watchdog_not_found", "path": str(script_path)}

    state_path = Path(os.getenv("HERMES_ZOOM_WATCHDOG_STATE_PATH", "/root/.hermes/state/zoom_watchdog.last"))
    try:
        state_path.unlink(missing_ok=True)
    except OSError as exc:
        app.logger.warning("Could not clear Hermes zoom watchdog cooldown: %s", exc)

    try:
        process = subprocess.Popen(
            ["bash", str(script_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Could not start Hermes zoom watchdog")
        return {"triggered": False, "reason": "start_failed", "error": str(exc), "path": str(script_path)}
    return {"triggered": True, "pid": process.pid, "path": str(script_path)}
def process_zoom_recording_event_queue(limit: int = 20) -> dict[str, Any]:
    ensure_zoom_event_queue_schema()
    limit = max(1, min(int(limit or 20), 100))
    processed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, event_name, zoom_uuid
                FROM zoom_recording_events
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
        zoom_uuid = row["zoom_uuid"]
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE zoom_recording_events
                        SET status = 'processing', attempts = attempts + 1, updated_at = now()
                        WHERE id = %s AND status IN ('queued','error')
                        """,
                        (event_id,),
                    )
                    if cur.rowcount != 1:
                        continue
        try:
            result = sync_zoom_recording_by_uuid(zoom_uuid, force=False)
            if event_name == "recording.transcript_completed" and result.get("status") in {"synced", "skipped_unchanged"}:
                result["hermes_zoom_watchdog"] = trigger_hermes_zoom_watchdog_from_event()
            with pg_connect() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE zoom_recording_events
                            SET status = 'done', error_text = NULL, processed_at = now(), updated_at = now()
                            WHERE id = %s
                            """,
                            (event_id,),
                        )
            processed.append({"event_id": str(event_id), "event": event_name, **result})
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc)
            with pg_connect() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE zoom_recording_events
                            SET status = 'error', error_text = %s, updated_at = now()
                            WHERE id = %s
                            """,
                            (error_text, event_id),
                        )
            errors.append({"event_id": str(event_id), "event": event_name, "zoom_uuid": zoom_uuid, "error": error_text})
    return {"processed": processed, "errors": errors, "processed_count": len(processed), "error_count": len(errors)}
def dedupe_zoom_participants(participants: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for participant in participants or []:
        name = str(participant.get("name") or "").strip()
        email = str(participant.get("email") or "").strip()
        key = (email.lower(), name.lower())
        if not name and not email:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append({"name": name or None, "email": email or None})
    return result
ZOOM_TECHNICAL_PARTICIPANT_NAMES = {
    name.strip().lower()
    for name in os.getenv(
        "ZOOM_TECHNICAL_PARTICIPANT_NAMES",
        "координатор,coordinator,zoom,zoom room,zoom rooms,рекордер,recorder,user,гость,guest",
    ).split(",")
    if name.strip()
}
ZOOM_SPEAKER_NOISE_NAMES = {"unknown", "speaker", "webvtt", "transcript", "участник", "спикер"}
def drop_service_participants(participants: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Убрать служебные аккаунты Zoom из списка участников созвона.

    В техническом логе Zoom вместе с людьми числится аккаунт зала («Координатор», Zoom Room,
    рекордер). Человеком он не является, но доходил до отчёта строкой «не сопоставлен с
    оргструктурой, требуется уточнение» — фантомный участник в каждом созвоне
    (сообщил владелец 20.07.2026). Ничего, кроме служебных имён, не отбрасываем."""
    people = list(participants or [])
    kept = [p for p in people
            if str(p.get("name") or "").strip().lower() not in ZOOM_TECHNICAL_PARTICIPANT_NAMES]
    for person in people:
        if person not in kept:
            logging.info("zoom: служебный аккаунт «%s» исключён из участников", person.get("name"))
    return kept
def zoom_call_row_payload(
    row: dict[str, Any],
    participants: list[dict[str, Any]] | None = None,
    include_transcript: bool = False,
) -> dict[str, Any]:
    start_time = row["start_time_msk"]
    end_time = row.get("end_time_msk")
    call_date = row["call_date"]
    date_text = f"{call_date.day} {RU_MONTH_NAMES[call_date.month][1]} {call_date.year}" if isinstance(call_date, date) else format_date_ru(call_date)
    payload = {
        "id": str(row["id"]),
        "date": call_date.isoformat() if hasattr(call_date, "isoformat") else str(call_date),
        "date_text": date_text,
        "start_time_msk": iso_or_none(start_time),
        "end_time_msk": iso_or_none(end_time),
        "time_text": f"{start_time.astimezone(MSK_TZ).strftime('%H:%M')} - {end_time.astimezone(MSK_TZ).strftime('%H:%M')}" if start_time and end_time else format_datetime_ru(start_time),
        "topic": row.get("topic") or "Без темы",
        "technical_topic": row.get("technical_topic") or row.get("topic") or "Без темы",
        "participants": dedupe_zoom_participants(participants),
        "analytical_note": row.get("analytical_note") or "",
        "raw_json": row.get("raw_json") or {},
        "duration_min": row.get("duration_min"),
        "synced_at": iso_or_none(row.get("synced_at")),
        "synced_at_text": format_datetime_msk_label(row.get("synced_at")),
    }
    if include_transcript:
        payload["transcript_text"] = row.get("transcript_text") or ""
    return payload
def _zoom_md_clean_offset(value: Any) -> str:
    """HH:MM:SS timecode without milliseconds."""
    text = str(value or "").strip()
    return text.split(".")[0].split(",")[0] if text else ""
_ZOOM_PARTICIPANT_ARTIFACTS = {"", "none", "дата созвона", "время созвона", "дата", "время"}
def _zoom_export_filename(detail: dict[str, Any]) -> str:
    call_date = str(detail.get("date") or "call").strip()
    short_id = str(detail.get("id") or "")[:8]
    return f"zoom_transcript_{call_date}_{short_id}.md".strip("_")
ZOOM_EXPORT_DIR = EXPORT_DIR / "zoom"
def zoom_export_ttl_seconds() -> int:
    """How long a download link (and its file) lives. Default 30 min, env-overridable."""
    try:
        return max(60, int(os.getenv("ZOOM_EXPORT_TTL_SECONDS", "1800")))
    except ValueError:
        return 1800
def _zoom_export_token(filename: str, expires_at: int) -> str:
    """Unguessable, stateless, time-bound token: HMAC over (expiry + filename). The
    public download route recomputes it and also checks the expiry, so a link works
    without login but cannot be guessed and stops working after expires_at."""
    secret = (os.getenv("FLASK_SECRET_KEY") or os.getenv("MCP_SHARED_SECRET") or "albery-zoom-export").encode("utf-8")
    message = f"{expires_at}:{os.path.basename(filename)}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:32]
def _zoom_export_public_url(filename: str) -> str:
    expires_at = int(time.time()) + zoom_export_ttl_seconds()
    token = _zoom_export_token(filename, expires_at)
    path = f"/zoom-export/{expires_at}/{token}/{quote(os.path.basename(filename))}"
    host = (os.getenv("MCP_HOST") or os.getenv("CANONICAL_WEB_HOST") or "").strip()
    if not host:
        # Fall back to the host of the current request (the MCP call comes in on mcp.m4s.ru).
        try:
            if has_request_context():
                host = request.host
        except Exception:
            host = ""
    return f"https://{host}{path}" if host else path
def _export_display_name_path(filename: str) -> Path:
    return ZOOM_EXPORT_DIR / (os.path.basename(filename) + ".name")
def write_export_display_name(filename: str, display_name: str) -> None:
    """Remember the human title of an export next to it, so the URL can stay short and ASCII
    while the browser still saves «Договор оказания услуг.docx»."""
    try:
        _export_display_name_path(filename).write_text(display_name, encoding="utf-8")
    except OSError:
        logging.warning("export display name not saved for %s", filename, exc_info=True)
def export_display_name(filename: str) -> str:
    """Human title for a stored export; falls back to the on-disk name (legacy links)."""
    safe = os.path.basename(filename)
    try:
        name = _export_display_name_path(safe).read_text(encoding="utf-8").strip()
        if name:
            return os.path.basename(name)
    except OSError:
        pass
    return safe
_EXPORT_URL_RE = re.compile(r"/zoom-export/(\d{9,12})/([0-9a-f]{8,32})/([^\s\]\)\"'<>]+)")
def repair_export_links(text: str) -> str:
    """Fix download links whose filename no longer matches their signature.

    The model has to reproduce the URL in its answer, and on long links it drops characters
    (19.07.2026: «…оказания услуг…» arrived as «…оказания слуг…», 18.07: «договор возмездного»
    → «договозмездного», 14.07: «.xlsx» → «.xls»). The filename is covered by the HMAC, so one
    lost character turns a live link into a 404 — the user sees a broken link on the first try
    and a working one after asking again. The token is unforgeable and therefore trustworthy:
    we re-derive which stored file it was issued for and rebuild the link. Only files that
    exist in the export dir can be addressed, so this cannot leak anything new."""
    if not text or "/zoom-export/" not in text:
        return text

    def _fix(m: re.Match[str]) -> str:
        expires_s, token, raw_name = m.group(1), m.group(2), m.group(3)
        expires = int(expires_s)
        name = os.path.basename(unquote(raw_name))
        if hmac.compare_digest(token, _zoom_export_token(name, expires)):
            return m.group(0)  # intact
        repaired = None
        try:
            for path in ZOOM_EXPORT_DIR.iterdir():
                if not path.is_file() or path.name.endswith(".name"):
                    continue
                # The token identifies the file it was issued for — trust it over the text.
                if hmac.compare_digest(token, _zoom_export_token(path.name, expires)):
                    repaired = (expires, token, path.name)
                    break
            else:
                # Token damaged instead: if the named file is really there, re-sign it.
                if name and (ZOOM_EXPORT_DIR / name).is_file():
                    fresh = int(time.time()) + zoom_export_ttl_seconds()
                    repaired = (fresh, _zoom_export_token(name, fresh), name)
        except OSError:
            logging.warning("repair_export_links: export dir unreadable", exc_info=True)
        if not repaired:
            logging.warning("repair_export_links: no stored export matches a damaged link (%s)", raw_name[:80])
            return m.group(0)
        exp, tok, fname = repaired
        logging.info("repair_export_links: rebuilt a damaged download link -> %s", fname)
        return f"/zoom-export/{exp}/{tok}/{quote(fname)}"

    return _EXPORT_URL_RE.sub(_fix, text)
def cleanup_zoom_exports() -> int:
    """Delete export files older than the TTL so they don't accumulate on disk.
    Called opportunistically on each new export; safe to call anytime."""
    if not ZOOM_EXPORT_DIR.exists():
        return 0
    cutoff = time.time() - zoom_export_ttl_seconds()
    removed = 0
    for path in ZOOM_EXPORT_DIR.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
def save_zoom_markdown_export(markdown: str, filename: str) -> dict[str, Any]:
    """Persist a Markdown export to disk and return a public, token-protected download URL
    that expires after the TTL (default 30 min). Used by the MCP export tools so the
    connector returns a short link instead of the full document (which large clients
    truncate). Old exports are swept on each call so they don't fill the disk."""
    ZOOM_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_zoom_exports()
    safe_name = os.path.basename(str(filename or "").strip()) or "zoom_export.md"
    (ZOOM_EXPORT_DIR / safe_name).write_text(markdown, encoding="utf-8")
    return {
        "filename": safe_name,
        "download_url": _zoom_export_public_url(safe_name),
        "expires_in_seconds": zoom_export_ttl_seconds(),
        "bytes": len(markdown.encode("utf-8")),
    }
def _zoom_export_link_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Compact link-mode payload (no full markdown, so MCP clients don't truncate it)."""
    markdown = result.get("markdown") or ""
    saved = save_zoom_markdown_export(markdown, result.get("filename") or "zoom_export.md")
    preview = "\n".join(markdown.splitlines()[:40])
    payload = {
        "filename": saved["filename"],
        "download_url": saved["download_url"],
        "expires_in_seconds": saved.get("expires_in_seconds"),
        "chars": result.get("chars", len(markdown)),
        "bytes": saved["bytes"],
        "preview": preview,
    }
    for key in ("call_id", "topic", "date", "calls"):
        if key in result:
            payload[key] = result[key]
    return payload
def extract_zoom_operational_tasks_section(note: str) -> str:
    text = str(note or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
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
def split_zoom_operational_task_items(section: str) -> list[str]:
    text = str(section or "").strip()
    if not text:
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        markers = list(re.finditer(r"(?:^|\s)(\d+)[).]\s+", line))
        if len(markers) <= 1:
            lines.append(line)
            continue
        for index, marker in enumerate(markers):
            start = marker.start()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(line)
            item = line[start:end].strip()
            if item:
                lines.append(item)
    return lines
ZOOM_ARTIFACT_LABELS = {
    "screenshot": "скриншот", "link": "ссылка", "file": "файл",
    "comment": "комментарий", "photo": "фото",
    "скриншот": "скриншот", "ссылка": "ссылка", "файл": "файл",
    "комментарий": "комментарий", "фото": "фото",
}
def extract_zoom_labeled_parts(text: str) -> tuple[str, dict[str, str]]:
    label_pattern = re.compile(r"(Срок|Критерий(?:\s+результата)?|Подтверждение|Статус|Источник)\s*:", re.IGNORECASE)
    matches = list(label_pattern.finditer(text))
    if not matches:
        return text.strip().strip(". "), {}
    unlabeled_text = text[:matches[0].start()].strip().strip(". ")
    labels: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1).lower().replace(" ", "_")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip().strip(". ")
        labels[key] = value
    return unlabeled_text, labels
def parse_zoom_operational_task_line(line: str, fallback_number: int) -> dict[str, Any] | None:
    text = str(line or "").strip()
    if not text:
        return None
    match = re.match(r"^\s*(\d+)[.)]\s*(.*)$", text, re.DOTALL)
    if match:
        number = to_int(match.group(1)) or fallback_number
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
    task_text, labels = extract_zoom_labeled_parts(body)
    result_criteria = first_text_value(labels.get("критерий_результата"), labels.get("критерий"))
    expected_artifact = first_text_value(labels.get("подтверждение"), "")
    deadline_text = first_text_value(labels.get("срок"), "срок не указан")
    status = first_text_value(labels.get("статус"), "planned")
    source = first_text_value(labels.get("источник"), "")
    if not task_text:
        return None
    return {
        "number": number,
        "assignee_name": first_text_value(assignee_name, "Требует назначения"),
        "bitrix_user_id": None,
        "task_text": sentence_case_ru(task_text),
        "deadline_text": deadline_text.strip().rstrip(".") or "срок не указан",
        "result_criteria": result_criteria.strip().rstrip("."),
        "expected_artifact": expected_artifact.strip().rstrip("."),
        "status": status.strip().rstrip(".") or "planned",
        "source": source.strip().rstrip("."),
        "raw": {"source_line": text},
    }
def normalize_zoom_operational_tasks(
    section: str = "",
    analysis: dict[str, Any] | None = None,
    existing_tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    source_items: list[Any] = []
    if existing_tasks:
        source_items = existing_tasks
    elif isinstance(analysis, dict):
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
        task_text = first_text_value(item.get("task_text"), item.get("task"), item.get("action"), item.get("text"))
        result_criteria = first_text_value(
            item.get("result_criteria"),
            item.get("success_criteria"),
            item.get("criteria"),
            item.get("criterion"),
        )
        expected_artifact = first_text_value(item.get("expected_artifact"), "")
        expected_artifact = ZOOM_ARTIFACT_LABELS.get(expected_artifact.strip().lower(), expected_artifact.strip())
        deadline_text = first_text_value(item.get("deadline_text"), item.get("deadline"), "срок не указан")
        assignee_name = first_text_value(
            item.get("assignee_name"),
            item.get("responsible"),
            item.get("responsible_name"),
            item.get("person_name"),
            item.get("org_person"),
            item.get("display_owner"),
            item.get("owner"),
            "Требует назначения",
        )
        if not task_text:
            continue
        tasks.append({
            "number": to_int(item.get("number")) or index,
            "assignee_name": assignee_name,
            "bitrix_user_id": to_int(first_non_empty(item.get("bitrix_user_id"), item.get("user_id"))),
            "task_text": sentence_case_ru(task_text),
            "deadline_text": deadline_text.strip().rstrip(".") or "срок не указан",
            "result_criteria": result_criteria.strip().rstrip("."),
            "expected_artifact": expected_artifact,
            "status": first_text_value(item.get("status"), "planned"),
            "source": first_text_value(item.get("source"), item.get("timecode"), ", ".join(evidence_times)),
            "raw": item.get("raw") if isinstance(item.get("raw"), dict) else item,
        })

    section_tasks: list[dict[str, Any]] = []
    for raw in split_zoom_operational_task_items(section):
        parsed = parse_zoom_operational_task_line(raw, len(section_tasks) + 1)
        if parsed:
            section_tasks.append(parsed)
    if section_tasks and len(section_tasks) > len(tasks):
        return section_tasks
    return tasks or section_tasks
ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO = (
    "Также во время созвона были выделены следующие задачи, добавьте себе задачи, которые считаете нужными, "
    "в комментарии напишите, что добавили, а что нет, подтвердите артефактом"
)
def zoom_call_operational_tasks(call: dict[str, Any]) -> list[dict[str, Any]]:
    raw_json = call.get("raw_json") if isinstance(call.get("raw_json"), dict) else {}
    ai_report = raw_json.get("ai_report") if isinstance(raw_json.get("ai_report"), dict) else {}
    existing = ai_report.get("operational_tasks") if isinstance(ai_report.get("operational_tasks"), list) else None
    analysis = ai_report.get("analysis") if isinstance(ai_report.get("analysis"), dict) else None
    section = extract_zoom_operational_tasks_section(call.get("analytical_note") or "")
    return normalize_zoom_operational_tasks(section=section, analysis=analysis, existing_tasks=existing)
def zoom_dispatch_deadline(call: dict[str, Any]) -> tuple[str | None, str]:
    """Lead-card deadline from DISPATCH time (owner rule 2026-07-09): today 18:00 МСК, but if
    fewer than 3 hours remain before 18:00 (or it is evening/a day off) — next working day 11:00.
    The call date is intentionally ignored: an old call dispatched today must still get a
    deadline the lead can realistically meet."""
    import business_hours
    deadline_at = business_hours.zoom_lead_deadline_at()
    return deadline_at.isoformat(), business_hours.format_deadline_msk(deadline_at)
def _zoom_ai_analysis(call: dict[str, Any]) -> dict[str, Any]:
    """Return the saved zoom_processing JSON (raw_json.ai_report.analysis)."""
    raw_json = call.get("raw_json") if isinstance(call.get("raw_json"), dict) else {}
    ai_report = raw_json.get("ai_report") if isinstance(raw_json.get("ai_report"), dict) else {}
    analysis = ai_report.get("analysis") if isinstance(ai_report.get("analysis"), dict) else {}
    return analysis if isinstance(analysis, dict) else {}
def zoom_call_participants(call: dict[str, Any]) -> list[dict[str, Any]]:
    """Actual call participants from saved AI reports.

    New reports store people.actual_participants. Older reports only store
    analysis.leaders_present; dispatch still must identify the call lead from
    that field instead of treating the report as having no participants.
    """
    analysis = _zoom_ai_analysis(call)
    people = analysis.get("people") if isinstance(analysis.get("people"), dict) else {}
    raw = people.get("actual_participants") if isinstance(people.get("actual_participants"), list) else []
    result: list[dict[str, Any]] = []
    for person in raw:
        if not isinstance(person, dict):
            continue
        name = str(person.get("person_name") or person.get("raw_name") or "").strip()
        if not name:
            continue
        result.append({
            "name": name,
            "bitrix_user_id": to_int(person.get("bitrix_user_id")),
            "org_match": str(person.get("org_match") or "").strip().lower(),
            "is_leader": bool(person.get("is_leader")),
            "role_on_call": str(person.get("role_on_call") or "").strip().lower(),
        })
    if result:
        return result

    leaders_present = analysis.get("leaders_present") if isinstance(analysis.get("leaders_present"), list) else []
    for index, leader in enumerate(leaders_present):
        name = str(leader.get("person_name") if isinstance(leader, dict) else leader or "").strip()
        if not name:
            continue
        result.append({
            "name": name,
            "bitrix_user_id": to_int(leader.get("bitrix_user_id")) if isinstance(leader, dict) else None,
            "org_match": str(leader.get("org_match") or "").strip().lower() if isinstance(leader, dict) else "",
            "is_leader": True,
            "role_on_call": "host" if index == 0 else "leader",
        })
    return result
def zoom_call_leader_evaluations(call: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-leader evaluations from the saved report (leader_evaluations)."""
    analysis = _zoom_ai_analysis(call)
    raw = analysis.get("leader_evaluations") if isinstance(analysis.get("leader_evaluations"), list) else []
    return [item for item in raw if isinstance(item, dict)]
def zoom_dispatch_title(call: dict[str, Any]) -> str:
    call_date = safe_parse_date(call.get("date"))
    if call_date is None:
        start_for_date = parse_datetime(call.get("start_time_msk"))
        call_date = start_for_date.astimezone(MSK_TZ).date() if start_for_date else None
    date_prefix = call_date.strftime("%d.%m") if call_date else ""

    time_text = str(call.get("time_text") or "").strip()
    start_text = ""
    end_text = ""
    if "-" in time_text:
        left, right = time_text.split("-", 1)
        start_text = left.strip()
        end_text = right.strip()
    else:
        start_dt = parse_datetime(call.get("start_time_msk"))
        end_dt = parse_datetime(call.get("end_time_msk"))
        start_text = start_dt.astimezone(MSK_TZ).strftime("%H:%M") if start_dt else time_text
        end_text = end_dt.astimezone(MSK_TZ).strftime("%H:%M") if end_dt else ""

    suffix_parts = [part for part in [date_prefix, start_text] if part]
    suffix = ", ".join(suffix_parts)
    if end_text:
        suffix = f"{suffix} - {end_text}" if suffix else end_text
    return f"Итоги созвона {suffix or 'созвон'}".strip()
def _zoom_person_summaries(call: dict[str, Any]) -> list[dict[str, Any]]:
    analysis = _zoom_ai_analysis(call)
    raw = analysis.get("person_summaries") if isinstance(analysis.get("person_summaries"), list) else []
    return [item for item in raw if isinstance(item, dict)]
def _zoom_report_people_for_participant_reports(call: dict[str, Any]) -> list[dict[str, Any]]:
    """People eligible for personal participant reports.

    Personal reports go only to people who were factually present under their own
    names in `people.actual_participants`. Mentioned employees, task assignees,
    leader digest names, and technical Zoom accounts must not receive personal
    meeting reports.
    """
    return list(zoom_call_participants(call))
def zoom_credentials_status(account_key: str = "ZOOM_ACC2") -> dict[str, Any]:
    required = [
        f"{account_key}_ACCOUNT_ID",
        f"{account_key}_CLIENT_ID",
        f"{account_key}_CLIENT_SECRET",
    ]
    missing = [key for key in required if not os.getenv(key, "").strip()]
    return {"account_key": account_key, "configured": not missing, "missing": missing}
@app.get("/zoom-export/<int:expires_at>/<token>/<path:filename>")
def zoom_export_download(expires_at: int, token: str, filename: str):
    """Public, token-protected, time-limited download of a saved Zoom Markdown export.
    No login: the token is an unguessable HMAC of (expiry + filename), so the link works
    in chat/connectors but cannot be enumerated and stops working after expires_at
    (default 30 min). Expired files are deleted on access; fresh ones are swept by
    cleanup_zoom_exports. Created by the MCP export tools (save_zoom_markdown_export)."""
    safe_name = os.path.basename(filename or "")
    if not safe_name or not hmac.compare_digest(str(token), _zoom_export_token(safe_name, expires_at)):
        abort(404)
    file_path = (ZOOM_EXPORT_DIR / safe_name).resolve()
    if file_path.parent != ZOOM_EXPORT_DIR.resolve() or not file_path.exists():
        abort(404)
    if time.time() > expires_at:
        # Link expired — remove the file so it doesn't linger, then 404.
        try:
            file_path.unlink()
        except OSError:
            pass
        abort(404)
    # The stored name is short and ASCII (so the URL survives being retyped); the human
    # title lives in a sidecar and is what the browser saves the file as.
    display_name = export_display_name(safe_name)
    return send_file(
        file_path,
        as_attachment=True,
        download_name=display_name,
        mimetype=(__import__("mimetypes").guess_type(display_name)[0]
                  or ("text/markdown; charset=utf-8" if display_name.lower().endswith((".md", ".markdown"))
                      else "application/octet-stream")),
    )
@app.route("/zoom/events/<secret>", methods=["GET", "POST"])
def zoom_recording_event_webhook(secret: str):
    if not zoom_event_secret_valid(secret):
        return jsonify({"error": "forbidden"}), 403
    if request.method == "GET":
        return jsonify({"ok": True, "message": "Zoom recording event endpoint is ready."})

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": True, "ignored": True, "reason": "invalid_payload"}), 200

    event_name = str(payload.get("event") or "").strip()
    if event_name == "endpoint.url_validation":
        validation = zoom_webhook_validation_response(payload)
        if not validation:
            return jsonify({"error": "ZOOM_WEBHOOK_SECRET_TOKEN or plainToken is missing."}), 400
        return jsonify(validation)

    if event_name not in {"recording.transcript_completed", "recording.completed"}:
        return jsonify({"ok": True, "event": event_name, "ignored": True, "reason": "unsupported_event"})

    zoom_uuid = extract_zoom_event_uuid(payload)
    if not zoom_uuid:
        return jsonify({"ok": True, "event": event_name, "ignored": True, "reason": "zoom_uuid_not_found"}), 200

    event_id = enqueue_zoom_recording_event(event_name, zoom_uuid, payload)
    inline = str(os.getenv("ZOOM_EVENT_PROCESS_INLINE", "1")).strip().lower() not in {"0", "false", "no", "off"}
    process_result = process_zoom_recording_event_queue(limit=5) if inline else None
    return jsonify({
        "ok": True,
        "event_id": event_id,
        "event": event_name,
        "zoom_uuid": zoom_uuid,
        "queued": True,
        "processed_inline": inline,
        "process_result": process_result,
    })
