"""get_agent_link — the Albery agent hands out chat links to the company's specialised agents
and tells the asker whether THEY currently have access.

Task (Alexander, 2026-07-15): «Настроить выдачу ссылок на профильных агентов». When someone
asks how to reach a given agent (lawyer / finance / developer / news / main), Albery checks
that person's access and returns the link. If they have no access it still returns the link,
with a note that access must be granted — the chat link is NOT a secret: the target bot
enforces access on every message, so a link without access simply can't be used until access
is granted.

Access rules mirror b24bot exactly:
  • main  — the team allowlist (agent_access rows with a non-'none' tier; owner always allowed).
  • sub-agents — their explicit agent_members list; an empty list means open to anyone not
    globally denied ('none').
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from attachments import connect

# Free-text → slug. Matched as substrings against the lowercased query, so «юрист»,
# «юридический», «по договорам» all resolve to the lawyer.
_AGENT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "main": ("албери", "albery", "основн", "главн", "универсальн", "ассистент", "общий"),
    "agent-sklad": ("юрист", "юридич", "договор", "правов", "lawyer", "sklad", "склад"),
    "agent-finansist": ("финанс", "finance", "бухгалт", "деньг", "финансист", "фин "),
    "agent-razrabotchik": ("разработ", "developer", "программист", "razrabotchik", "девелопер"),
    "novostnoy-agent": ("новост", "news", "сводк", "аналитик новост"),
}


def _portal_base() -> str:
    """Portal origin (https://<portal>) for building the online-chat deep link."""
    base = (os.getenv("B24_PORTAL_BASE") or "").strip().rstrip("/")
    if base:
        return base
    for var in ("BITRIX_WEBHOOK_BASE", "B24_TESTBOT_WEBHOOK_BASE"):
        m = re.match(r"(https?://[^/]+)", os.getenv(var) or "")
        if m:
            return m.group(1)
    return ""


def _chat_link(bot_id: Any) -> str:
    """Deep link that opens the private chat with a bot in the Bitrix24 web client.
    A bot's BOT_ID equals its Bitrix user id, and /online/?IM_DIALOG=<user_id> opens that dialog."""
    base = _portal_base()
    try:
        bid = int(bot_id)
    except (TypeError, ValueError):
        return ""
    return f"{base}/online/?IM_DIALOG={bid}" if base else ""


def _main_bot_id() -> int | None:
    """The universal (main) agent row keeps bitrix_bot_id NULL on purpose; the real main bot id
    lives in the b24bot state file (env override B24_MAIN_BOT_ID)."""
    env = os.getenv("B24_MAIN_BOT_ID", "").strip()
    if env.isdigit():
        return int(env)
    try:
        import b24bot as _b
        bid = (_b._b24_load_state() or {}).get("bot_id")
        return int(bid) if bid else None
    except Exception:  # noqa: BLE001
        return None


def _has_access(slug: str, requester_id: Any) -> bool | None:
    """True / False / None(unknown, id missing). Mirrors b24bot's access enforcement."""
    if requester_id in (None, "", 0, "0"):
        return None
    try:
        uid = int(requester_id)
    except (TypeError, ValueError):
        return None
    try:
        import b24bot as _b
        if slug == "main":
            return bool(_b._b24_main_allows(uid))
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agents WHERE slug = %s", (slug,))
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute("SELECT bitrix_user_id FROM agent_members WHERE agent_id = %s", (row["id"],))
                members = {int(r["bitrix_user_id"]) for r in cur.fetchall()}
        if members:
            return uid in members
        # Empty member list = open to everyone not globally denied ('none').
        return _b._b24_tier_for(uid) != "none"
    except Exception:  # noqa: BLE001
        logging.exception("get_agent_link: access check failed slug=%s", slug)
        return None


def _load_agents() -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT slug, name, position, role_prompt, bitrix_bot_id, is_active "
                "FROM agents WHERE is_active = TRUE ORDER BY "
                "CASE WHEN slug='main' THEN 0 ELSE 1 END, name"
            )
            agents = [dict(r) for r in cur.fetchall()]
    main_bid = _main_bot_id()
    for a in agents:
        if a["slug"] == "main" and not a.get("bitrix_bot_id"):
            a["bitrix_bot_id"] = main_bid
    return agents


def _resolve(query: str, agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return agents
    hits: list[dict[str, Any]] = []
    for a in agents:
        slug = a["slug"]
        name = (a["name"] or "").lower()
        if q == slug or q in name or name in q:
            hits.append(a)
            continue
        if any(s in q for s in _AGENT_SYNONYMS.get(slug, ())):
            hits.append(a)
    return hits


def _suggested_reply(name: str, link: str, access: bool | None) -> str:
    if not link:
        return (f"Ссылку на «{name}» сейчас собрать не удалось — сообщите Александру Никитенко.")
    if access is True:
        return f"У вас есть доступ к «{name}». Вот ссылка на чат с ним: {link}"
    if access is False:
        return (f"Доступа к «{name}» у вас пока нет, но вот ссылка на него: {link}\n"
                f"Открыть доступ может Александр Никитенко — хотите, передам ему запрос? 🙌")
    return (f"Вот ссылка на чат с «{name}»: {link}\n"
            f"Если при входе появится «нет доступа» — его открывает Александр Никитенко.")


def tool_get_agent_link(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("agent") or args.get("query") or args.get("name") or "").strip()
    requester = args.get("requester_bitrix_user_id")
    if requester in ("", None):
        requester = args.get("bitrix_user_id")

    agents = _load_agents()
    matched = _resolve(query, agents)
    exact = bool(query) and len(matched) >= 1 and len(matched) <= 2

    def _entry(a: dict[str, Any]) -> dict[str, Any]:
        access = _has_access(a["slug"], requester)
        link = _chat_link(a.get("bitrix_bot_id"))
        return {
            "slug": a["slug"],
            "name": a["name"],
            "role": (a.get("position") or "").strip(),
            "chat_link": link,
            "has_access": access,
            "access_label": ("есть доступ" if access is True
                             else "нет доступа" if access is False else "доступ не проверен"),
            "suggested_reply": _suggested_reply(a["name"], link, access),
        }

    if not matched:
        # Query named an unknown agent — list everyone so Albery can offer the right one.
        return {
            "matched": False,
            "requester_bitrix_user_id": requester,
            "note": ("Не удалось однозначно понять, о каком агенте речь. Вот все профильные агенты "
                     "компании — выбери подходящего и дай его ссылку, учитывая has_access."),
            "agents": [_entry(a) for a in agents],
        }

    entries = [_entry(a) for a in matched]
    return {
        "matched": True,
        "exact": exact,
        "requester_bitrix_user_id": requester,
        "guidance": ("Дай пользователю ссылку из chat_link. Если has_access=false — прямо скажи, "
                     "что доступа пока нет, НО ВСЁ РАВНО дай ссылку и предложи передать запрос "
                     "Александру Никитенко (можешь добавить служебный маркер [[ESCALATE: суть]]). "
                     "Если has_access=true — просто дай ссылку. Ответ бери из suggested_reply."),
        "agents": entries,
    }


GET_AGENT_LINK_SPEC: dict[str, Any] = {
    "description": (
        "Выдать пользователю ссылку на чат с профильным агентом компании (Агент Албери, "
        "Агент-юрист, Агент-финансист, Агент-разработчик, Новостной агент) И проверить, есть ли у "
        "ЭТОГО пользователя доступ к нему. Вызывай, когда спрашивают «как обратиться к агенту X», "
        "«дай ссылку на юриста/финансиста», «где найти агента-разработчика» и т.п. Передавай "
        "agent = как назвали агента (юрист/финансист/разработчик/новостной/албери — синонимы "
        "распознаются) и requester_bitrix_user_id = Bitrix id ТЕКУЩЕГО собеседника. Ответ "
        "содержит chat_link, has_access и готовую фразу suggested_reply. ВАЖНО: ссылку давай в "
        "любом случае — даже если доступа нет (тогда честно скажи, что доступа пока нет, и "
        "предложи передать запрос Александру Никитенко). Без agent — вернёт всех агентов."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "agent": {"type": "string",
                      "description": "Какого агента спрашивают (имя/роль/синоним). Пусто = все агенты."},
            "requester_bitrix_user_id": {"type": "integer",
                                         "description": "Bitrix id текущего собеседника — для проверки его доступа."},
        },
        "additionalProperties": False,
    },
}
