"""Idempotent CRM linking for Telegram conversations in the funnel workspace.

The local ``conversation.deal_id`` is the primary source of truth.  A
deterministic Telegram marker in Bitrix is a secondary recovery mechanism for
the narrow failure window where Bitrix created a deal but the local link was
not committed.

This module intentionally has no Flask or application imports.  Network and
store dependencies can be injected, which keeps the worker integration small
and the unit tests offline.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Callable, Mapping, TypedDict

import funnel_workspace_store as workspace_store


DEFAULT_CATEGORY_ID = 16
_LINK_RETRIES = 4
_CRM_PAGE_SIZE = 200
_MAX_RECONCILE_PAGES = 100
_LOCK_STRIPES = tuple(threading.Lock() for _ in range(64))

CrmCall = Callable[[str, dict[str, Any]], Mapping[str, Any]]
ConversationGetter = Callable[[Any], Mapping[str, Any]]
CrmLinkUpdater = Callable[..., Mapping[str, Any]]
DealEnsurer = Callable[[Any], Mapping[str, Any]]


class WorkspaceCrmError(RuntimeError):
    """Base error for a CRM deal that could not be linked safely."""


class TelegramIdentityError(WorkspaceCrmError):
    """The conversation has no stable numeric Telegram identity."""


class CrmResponseError(WorkspaceCrmError):
    """The CRM returned a response without the fields required for safe linking."""


class CrmEnsureResult(TypedDict):
    conversation: dict[str, Any]
    deal_id: int
    status: str
    created: bool
    recovered: bool
    already_linked: bool
    orphan_deal_id: int | None


class CrmStageActionResult(TypedDict):
    conversation_id: int
    deal_id: int
    target_stage: str
    previous_stage: str
    status: str
    crm_link_status: str


def telegram_identity(conversation: Mapping[str, Any]) -> int:
    """Return the stable positive Telegram user id for a conversation."""

    raw = conversation.get("external_user_id")
    try:
        telegram_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise TelegramIdentityError(
            "Telegram conversation has no numeric external_user_id."
        ) from exc
    if telegram_id <= 0:
        raise TelegramIdentityError(
            "Telegram conversation external_user_id must be positive."
        )
    return telegram_id


def telegram_marker(conversation: Mapping[str, Any]) -> str:
    """Return the deterministic marker written to every workspace-created deal."""

    return f"[tg:{telegram_identity(conversation)}]"


def build_deal_payload(
    conversation: Mapping[str, Any],
    *,
    category_id: int = DEFAULT_CATEGORY_ID,
    stage_id: str,
    telegram_field: str,
    telegram_id_field: str | None = None,
) -> dict[str, Any]:
    """Build the ``create_crm_deal`` arguments for one Telegram conversation.

    ``username`` and ``display_name`` are presentation only.  The custom field
    historically configured by ``CRM_TELEGRAM_FIELD`` remains a username field:
    writing a numeric Telegram id there breaks the legacy matching code.  A
    stable id is written only to the separately configured workspace field.
    The deterministic title/comment marker is always present.
    """

    telegram_id = telegram_identity(conversation)
    marker = telegram_marker(conversation)
    username = _clean_username(conversation.get("username"))
    display_name = _one_line(conversation.get("display_name"), 100)
    username_field = str(telegram_field or "").strip()
    identity_field = str(telegram_id_field or "").strip()
    _validate_distinct_identity_field(username_field, identity_field)

    labels: list[str] = []
    if display_name:
        labels.append(display_name)
    if username and f"@{username}".casefold() not in display_name.casefold():
        labels.append(f"@{username}")
    if not labels:
        labels.append(f"ID {telegram_id}")

    title = f"Лид Telegram {marker} — {' · '.join(labels)}"
    comments = [
        "Источник: Telegram Business.",
        f"Telegram user id: {telegram_id}; workspace marker: {marker}.",
    ]
    if username:
        comments.append(f"Username на момент обращения: @{username}.")
    comments.append(
        "Сделка создана рабочим пространством воронки; "
        "numeric Telegram ID является ключом клиента."
    )
    custom_fields: dict[str, str] = {}
    if username and username_field:
        custom_fields[username_field] = username
    if identity_field:
        custom_fields[identity_field] = str(telegram_id)
    return {
        "title": title[:255],
        "category_id": int(category_id),
        "stage": str(stage_id),
        "custom_fields": custom_fields,
        "comments": "\n".join(comments),
    }


def find_existing_deal(
    conversation: Mapping[str, Any],
    *,
    crm_call: CrmCall,
    category_id: int = DEFAULT_CATEGORY_ID,
    telegram_field: str,
    telegram_id_field: str | None = None,
) -> int | None:
    """Find a deal this person already has in the funnel.

    This lookup is intentionally performed before every create.  If it fails,
    the exception is allowed to escape: creating while reconciliation is
    unavailable could duplicate a deal whose local link was lost.

    Three signals are accepted, and the whole category is paged so that none of
    them can be missed by a title search: the deterministic ``[tg:<id>]``
    marker, a dedicated numeric-id field, and the client's ``@username``.  The
    username matters because deals born OUTSIDE this workspace carry no marker
    at all — a CRM form, an old Telegram lead, an open-line chat — and the
    owner's rule is that a person already in the funnel never gets a second
    card (живой дубль 27.07.2026: #226 и #228 на одного клиента).
    """

    telegram_id = telegram_identity(conversation)
    marker = telegram_marker(conversation)
    username_field = str(telegram_field or "").strip()
    identity_field = str(telegram_id_field or "").strip()
    _validate_distinct_identity_field(username_field, identity_field)
    username = _clean_username(conversation.get("username"))
    username_pattern = _username_pattern(username)

    deals = _list_category_deals(
        crm_call,
        category_id=int(category_id),
        include_custom_fields=bool(identity_field or username_field),
    )

    # Маркер и числовое поле опознают человека НАВЕРНЯКА, username — лишь вероятно
    # (его можно сменить, и он может достаться другому). Поэтому совпадения копятся
    # раздельно: пока есть точное, вероятное не рассматривается вовсе.
    matches: list[int] = []
    username_matches: list[int] = []
    for deal in deals:
        if not isinstance(deal, Mapping):
            continue
        raw_category = deal.get("category_id", deal.get("CATEGORY_ID"))
        if raw_category not in (None, ""):
            try:
                if int(raw_category) != int(category_id):
                    continue
            except (TypeError, ValueError):
                continue

        title = str(deal.get("title") or deal.get("TITLE") or "").casefold()
        title_matches = marker.casefold() in title
        fields = deal.get("custom_fields") or {}
        raw_identity = (
            fields.get(identity_field)
            if identity_field and isinstance(fields, Mapping)
            else None
        )
        if raw_identity is None and identity_field:
            raw_identity = deal.get(identity_field)
        field_matches = bool(identity_field) and _identity_value_matches(
            raw_identity,
            telegram_id,
        )
        username_match = bool(username_pattern) and (
            bool(username_pattern.search(title))
            or _username_value_matches(
                _deal_field(deal, fields, username_field),
                username,
            )
        )
        if title_matches or field_matches or username_match:
            try:
                deal_id = _positive_int(
                    deal.get("deal_id") or deal.get("id") or deal.get("ID")
                )
            except CrmResponseError as exc:
                raise CrmResponseError(
                    "CRM returned a matching Telegram deal without a valid deal id; "
                    "deal creation was cancelled to avoid a duplicate."
                ) from exc
            if title_matches or field_matches:
                matches.append(deal_id)
            else:
                username_matches.append(deal_id)

    if matches:
        return min(matches)
    # Из нескольких чужих карточек берём самую раннюю: в ней история, а поздние —
    # как раз те дубли, ради которых всё это и делается.
    return min(username_matches) if username_matches else None


def ensure_conversation_deal(
    conversation_id: Any,
    *,
    get_conversation: ConversationGetter | None = None,
    update_crm_link: CrmLinkUpdater | None = None,
    crm_call: CrmCall | None = None,
    category_id: int = DEFAULT_CATEGORY_ID,
    stage_id: str | None = None,
    telegram_field: str | None = None,
    telegram_id_field: str | None = None,
) -> CrmEnsureResult:
    """Ensure that one workspace conversation is linked to a CRM deal.

    The function is synchronous so the existing worker can run it in its own
    background thread.  All side-effecting dependencies are injectable.
    """

    item_id = _positive_int(conversation_id)
    getter = get_conversation or workspace_store.get_conversation
    updater = update_crm_link or workspace_store.update_crm_link
    default_crm_call, default_stage, default_field, default_id_field = _tg_defaults()
    call = crm_call or default_crm_call
    stage = str(stage_id or default_stage).strip()
    field = str(default_field if telegram_field is None else telegram_field).strip()
    identity_field = str(
        default_id_field if telegram_id_field is None else telegram_id_field
    ).strip()
    if not stage:
        raise WorkspaceCrmError("CRM stage id is required.")
    _validate_distinct_identity_field(field, identity_field)

    lock = _LOCK_STRIPES[item_id % len(_LOCK_STRIPES)]
    with lock:
        conversation = dict(getter(item_id))
        _validate_conversation(conversation, item_id)
        linked_id = _conversation_deal_id(conversation)
        if linked_id is not None:
            return _result(
                conversation,
                linked_id,
                status="already_linked",
            )

        recovered_id = find_existing_deal(
            conversation,
            crm_call=call,
            category_id=category_id,
            telegram_field=field,
            telegram_id_field=identity_field,
        )
        if recovered_id is not None:
            linked, actual_id = _link_known_deal(
                item_id,
                recovered_id,
                getter=getter,
                updater=updater,
                category_id=category_id,
                stage_id=stage,
            )
            return _result(
                linked,
                actual_id,
                status="recovered" if actual_id == recovered_id else "concurrent_link",
                orphan_deal_id=recovered_id if actual_id != recovered_id else None,
            )

        # A different process may have linked the conversation while the CRM
        # reconciliation request was in flight.  Re-read immediately before the
        # irreversible external create.
        conversation = dict(getter(item_id))
        _validate_conversation(conversation, item_id)
        linked_id = _conversation_deal_id(conversation)
        if linked_id is not None:
            return _result(
                conversation,
                linked_id,
                status="already_linked",
            )

        payload = build_deal_payload(
            conversation,
            category_id=category_id,
            stage_id=stage,
            telegram_field=field,
            telegram_id_field=identity_field,
        )
        created = call("create_crm_deal", payload)
        created_id = _crm_deal_id(created)
        if created_id is None:
            raise CrmResponseError(
                "create_crm_deal succeeded without a positive deal id; "
                "the next run must reconcile by Telegram marker."
            )

        linked, actual_id = _link_known_deal(
            item_id,
            created_id,
            getter=getter,
            updater=updater,
            category_id=category_id,
            stage_id=stage,
        )
        concurrent = actual_id != created_id
        return _result(
            linked,
            actual_id,
            status="concurrent_link" if concurrent else "created",
            orphan_deal_id=created_id if concurrent else None,
        )


def apply_conversation_stage_action(
    conversation_id: Any,
    target_stage: Any,
    *,
    crm_call: CrmCall | None = None,
    ensure_deal: DealEnsurer | None = None,
) -> CrmStageActionResult:
    """Idempotently SET a conversation's deal stage after Telegram delivery.

    The pre-read closes the normal retry path: if Bitrix committed the prior
    update but the worker crashed before marking its local action done, the next
    lease observes the target stage and performs no second mutation.
    """

    item_id = _positive_int(conversation_id)
    stage = str(target_stage or "").strip()
    if not stage or len(stage) > 200:
        raise WorkspaceCrmError("CRM target stage must contain 1-200 characters.")
    if crm_call is None:
        call = _tg_defaults()[0]
    else:
        call = crm_call
    if ensure_deal is None:
        ensured = ensure_conversation_deal(item_id, crm_call=call)
    else:
        ensured = dict(ensure_deal(item_id))
    deal_id = _positive_int(
        ensured.get("deal_id")
        or (ensured.get("conversation") or {}).get("deal_id")
    )
    link_status = str(ensured.get("status") or "linked")

    current = call("get_crm_deal", {"deal_id": deal_id})
    previous_stage = _response_stage(current)
    if previous_stage is None:
        raise CrmResponseError(
            "get_crm_deal returned no stage_id; CRM stage update was cancelled."
        )
    if previous_stage == stage:
        return {
            "conversation_id": item_id,
            "deal_id": deal_id,
            "target_stage": stage,
            "previous_stage": previous_stage,
            "status": "already_applied",
            "crm_link_status": link_status,
        }

    updated = call(
        "update_crm_deal",
        {
            "deal_id": deal_id,
            # This is an idempotent desired-state SET, never an incremental move.
            "stage": stage,
        },
    )
    confirmed_stage = _response_stage(updated)
    if confirmed_stage is None:
        # Some injected/older adapters only return {"updated": true}.  Verify
        # through the read API before acknowledging the durable action.
        confirmed_stage = _response_stage(
            call("get_crm_deal", {"deal_id": deal_id})
        )
    if confirmed_stage != stage:
        raise CrmResponseError(
            f"CRM deal {deal_id} remained on stage {confirmed_stage!r}; "
            f"expected {stage!r}."
        )
    return {
        "conversation_id": item_id,
        "deal_id": deal_id,
        "target_stage": stage,
        "previous_stage": previous_stage,
        "status": "applied",
        "crm_link_status": link_status,
    }


def _link_known_deal(
    conversation_id: int,
    deal_id: int,
    *,
    getter: ConversationGetter,
    updater: CrmLinkUpdater,
    category_id: int,
    stage_id: str,
) -> tuple[dict[str, Any], int]:
    last_conflict: Exception | None = None
    for _ in range(_LINK_RETRIES):
        current = dict(getter(conversation_id))
        existing = _conversation_deal_id(current)
        if existing is not None:
            return current, existing
        try:
            linked = dict(
                updater(
                    conversation_id,
                    deal_id=deal_id,
                    funnel_id=int(category_id),
                    stage_id=stage_id,
                    expected_version=current.get("state_version"),
                )
            )
        except workspace_store.WorkspaceConflictError as exc:
            last_conflict = exc
            continue
        actual = _conversation_deal_id(linked)
        if actual is None:
            raise CrmResponseError("update_crm_link returned a conversation without deal_id.")
        return linked, actual
    raise WorkspaceCrmError(
        f"Conversation {conversation_id} kept changing while CRM link was committed."
    ) from last_conflict


def _validate_conversation(conversation: Mapping[str, Any], expected_id: int) -> None:
    actual_id = _positive_int(conversation.get("id"))
    if actual_id != expected_id:
        raise WorkspaceCrmError(
            f"Conversation getter returned id {actual_id}, expected {expected_id}."
        )
    source = str(conversation.get("source_key") or "telegram").strip().lower()
    if source != "telegram":
        raise WorkspaceCrmError(
            f"CRM adapter only supports Telegram conversations, got {source!r}."
        )
    telegram_identity(conversation)


def _result(
    conversation: Mapping[str, Any],
    deal_id: int,
    *,
    status: str,
    orphan_deal_id: int | None = None,
) -> CrmEnsureResult:
    return {
        "conversation": dict(conversation),
        "deal_id": int(deal_id),
        "status": status,
        "created": status == "created",
        "recovered": status == "recovered",
        "already_linked": status == "already_linked",
        "orphan_deal_id": orphan_deal_id,
    }


def _conversation_deal_id(value: Mapping[str, Any]) -> int | None:
    raw = value.get("deal_id")
    if raw in (None, ""):
        return None
    try:
        return _positive_int(raw)
    except CrmResponseError:
        return None


def _crm_deal_id(value: Mapping[str, Any]) -> int | None:
    raw = value.get("deal_id") or value.get("id") or value.get("ID")
    if raw in (None, ""):
        return None
    try:
        return _positive_int(raw)
    except CrmResponseError:
        return None


def deal_stage(value: Mapping[str, Any]) -> str | None:
    """Этап из ответа CRM — Битрикс отдаёт его под разными именами ключа."""
    return _response_stage(value)


def read_deal_stage(deal_id: Any, *, crm_call: CrmCall | None = None) -> str | None:
    """Текущий этап сделки. Только чтение: ошибка CRM не может испортить состояние."""
    call = crm_call or _tg_defaults()[0]
    response = call("get_crm_deal", {"deal_id": _positive_int(deal_id)})
    return deal_stage(response if isinstance(response, Mapping) else {})


def _response_stage(value: Mapping[str, Any]) -> str | None:
    if not isinstance(value, Mapping):
        return None
    nested = value.get("deal")
    record = nested if isinstance(nested, Mapping) else value
    stage = str(
        record.get("stage_id")
        or record.get("stage")
        or record.get("STAGE_ID")
        or ""
    ).strip()
    return stage or None


def _positive_int(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CrmResponseError("Expected a positive integer id.") from exc
    if result <= 0:
        raise CrmResponseError("Expected a positive integer id.")
    return result


def _one_line(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _clean_username(value: Any) -> str:
    return _one_line(value, 100).lstrip("@")


def _username_pattern(username: str) -> re.Pattern[str] | None:
    """Как узнать `@username` в названии сделки, не поймав чужую.

    Границы обязательны: без них `@ivan` совпал бы со сделкой «ivanov», и разговор
    прицепился бы к карточке другого человека — это хуже дубля. Слишком короткое имя
    не берём вовсе: Telegram таких не выдаёт, а случайное совпадение вероятно.
    """
    clean = str(username or "").strip().casefold()
    if len(clean) < 3 or not re.fullmatch(r"[a-z0-9_]+", clean):
        return None
    return re.compile(rf"(?<![a-z0-9_]){re.escape(clean)}(?![a-z0-9_])")


def _username_value_matches(value: Any, username: str) -> bool:
    """Тот же человек в поле «Telegram» сделки — с `@`, ссылкой или без них."""
    expected = str(username or "").strip().casefold()
    if not expected:
        return False
    raw = str(value or "").strip().casefold()
    if not raw:
        return False
    raw = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", raw).lstrip("@").strip()
    return raw == expected


def _deal_field(deal: Mapping[str, Any], fields: Any, field: str) -> Any:
    if not field:
        return None
    if isinstance(fields, Mapping) and field in fields:
        return fields.get(field)
    return deal.get(field)


def _identity_value_matches(value: Any, telegram_id: int) -> bool:
    if isinstance(value, Mapping):
        return any(_identity_value_matches(item, telegram_id) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_identity_value_matches(item, telegram_id) for item in value)
    normalized = str(value or "").strip().casefold()
    expected = str(telegram_id)
    return normalized in {
        expected,
        f"tg:{expected}",
        f"telegram:{expected}",
        f"tg://user?id={expected}",
    }


def _validate_distinct_identity_field(
    username_field: str,
    identity_field: str,
) -> None:
    if (
        username_field
        and identity_field
        and username_field.casefold() == identity_field.casefold()
    ):
        raise WorkspaceCrmError(
            "FUNNEL_WORKSPACE_CRM_TELEGRAM_ID_FIELD must differ from "
            "CRM_TELEGRAM_FIELD; the legacy field stores Telegram username."
        )


def _response_deals(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    deals = response.get("deals") or []
    if not isinstance(deals, list):
        raise CrmResponseError("list_crm_deals returned an invalid deals collection.")
    return deals


def _list_category_deals(
    crm_call: CrmCall,
    *,
    category_id: int,
    include_custom_fields: bool,
) -> list[Mapping[str, Any]]:
    """Read a complete category snapshot or fail closed before deal creation."""

    rows: list[Mapping[str, Any]] = []
    offset = 0
    expected_total: int | None = None
    seen_deal_ids: set[int] = set()
    for _ in range(_MAX_RECONCILE_PAGES):
        response = crm_call(
            "list_crm_deals",
            {
                "category_id": int(category_id),
                "include_custom_fields": bool(include_custom_fields),
                "limit": _CRM_PAGE_SIZE,
                "offset": offset,
            },
        )
        page = _response_deals(response)
        rows.extend(page)

        raw_total = response.get("total")
        total: int | None = None
        if raw_total not in (None, ""):
            try:
                total = int(raw_total)
            except (TypeError, ValueError) as exc:
                raise CrmResponseError(
                    "list_crm_deals returned an invalid total."
                ) from exc
            if total < 0:
                raise CrmResponseError("list_crm_deals returned a negative total.")
            if expected_total is None:
                expected_total = total
            elif expected_total != total:
                raise CrmResponseError(
                    "CRM category changed during reconciliation; "
                    "deal creation was cancelled to avoid a duplicate."
                )

        consumed = len(page)
        next_offset = offset + consumed
        if total is not None and next_offset > total:
            raise CrmResponseError(
                "list_crm_deals returned more rows than its advertised total."
            )
        for deal in page:
            if not isinstance(deal, Mapping):
                continue
            raw_deal_id = deal.get("deal_id") or deal.get("id") or deal.get("ID")
            try:
                deal_id = int(raw_deal_id)
            except (TypeError, ValueError):
                continue
            if deal_id in seen_deal_ids:
                raise CrmResponseError(
                    "CRM pagination repeated a deal; "
                    "deal creation was cancelled to avoid a duplicate."
                )
            seen_deal_ids.add(deal_id)
        if total is not None and next_offset >= total:
            return rows
        if total is None and consumed < _CRM_PAGE_SIZE:
            return rows
        if consumed == 0:
            raise CrmResponseError(
                "list_crm_deals pagination stopped before the advertised total; "
                "deal creation was cancelled to avoid a duplicate."
            )
        offset = next_offset

    raise CrmResponseError(
        "CRM category is too large to reconcile safely; "
        "deal creation was cancelled to avoid a duplicate."
    )


def _tg_defaults() -> tuple[CrmCall, str, str, str]:
    # Lazy import avoids a circular import when tg_agent wires the worker.
    import tg_agent

    return (
        tg_agent.mcp_call,
        tg_agent.STAGE_NEW,
        tg_agent.CRM_TELEGRAM_FIELD,
        os.getenv("FUNNEL_WORKSPACE_CRM_TELEGRAM_ID_FIELD", "").strip(),
    )
