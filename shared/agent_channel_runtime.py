"""Channel-neutral policy/context for employee-facing Albery agents.

The agent profile owns behaviour and capabilities.  A channel adapter supplies only
transport identity, presentation rules, conversation scope and an explicitly typed
automation destination.  The IU customer runtime is intentionally not a caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class ChannelContext:
    channel: str
    conversation_id: str
    requester_name: str = ""
    requester_platform_id: str = ""
    requester_bitrix_user_id: int | None = None

    def __post_init__(self) -> None:
        if self.channel not in {"bitrix", "telegram"}:
            raise ValueError("unsupported agent channel")
        if not str(self.conversation_id).strip():
            raise ValueError("conversation_id is required")

    @property
    def scope(self) -> str:
        safe = "".join(c for c in str(self.conversation_id) if c.isalnum() or c in "_-.")[:80]
        return f"{self.channel}:{safe}"

    @property
    def automation_destination(self) -> dict[str, str]:
        return {
            "delivery_channel": self.channel,
            "delivery_conversation_id": str(self.conversation_id),
        }


def _joined_personal(instructions: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for item in instructions:
        name = str(item.get("name") or "Правило").strip()
        content = str(item.get("content") or "").strip()
        if content:
            rows.append(f"— {name}:\n{content}")
    return "\n\n".join(rows)


def _joined_skills(skills: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for item in skills:
        title = str(item.get("title") or item.get("name") or "Навык").strip()
        description = str(item.get("description") or "").strip()
        content = str(item.get("content") or "").strip()
        row = f"«{title}»" + (f" — {description}" if description else "")
        if content:
            row += f"\nПОЛНЫЙ ТЕКСТ:\n{content}"
        rows.append(row)
    return "\n\n".join(rows)


def build_agent_policy(
    profile: Mapping[str, Any],
    context: ChannelContext,
    *,
    core_instructions: str = "",
    selected_skills: Iterable[Mapping[str, Any]] = (),
    personal_instructions: Iterable[Mapping[str, Any]] = (),
    now: datetime | None = None,
) -> str:
    """Build the shared behavioural contract for Bitrix and Telegram turns.

    The wording is deliberately channel-neutral except for rendering and sender facts.
    Tool availability is still enforced by the private ``agent-<slug>`` connector.
    """
    slug = str(profile.get("slug") or "main").strip() or "main"
    name = str(profile.get("name") or "Агент Албери").strip()
    role = str(profile.get("role_prompt") or "").strip()
    is_main = slug == "main"
    channel_label = "Bitrix24" if context.channel == "bitrix" else "Telegram"
    if context.channel == "bitrix":
        presentation = (
            "Оформляй ответ для Bitrix24: короткие абзацы, списки с '- ', главное выделяй "
            "только [b]...[/b]. Не используй Markdown-заголовки, **, обратные кавычки или таблицы."
        )
    else:
        presentation = (
            "Оформляй ответ для Telegram: короткие абзацы и списки, без Bitrix BBCode. "
            "Допустимы простой текст и умеренные эмодзи; не выводи служебные маркеры."
        )
    identity = (
        f"Ты — основной агент компании «Албери», профиль `{slug}`."
        if is_main
        else f"Ты — специализированный агент «{name}», профиль `{slug}`. ТВОЯ РОЛЬ: "
             f"{role or 'специализированный помощник компании'}. Работай строго в рамках этой роли; "
             "вопросы вне роли передавай Основному агенту Албери."
    )
    actor = ""
    if context.requester_name or context.requester_platform_id:
        actor = (
            "ТЕКУЩИЙ СОБЕСЕДНИК: "
            + (context.requester_name or "имя не указано")
            + f"; канал={context.channel}; platform_user_id={context.requester_platform_id or 'unknown'}."
        )
    if context.requester_bitrix_user_id is not None:
        actor += (
            f" Его подтверждённый Bitrix user id={context.requester_bitrix_user_id}. "
            "Только этот id можно использовать для действий от лица собеседника."
        )
    elif context.channel == "telegram":
        actor += (
            " Связь с сотрудником Bitrix не подтверждена. Не передавай creator_bitrix_user_id, "
            "author_bitrix_user_id или on_behalf_bitrix_user_id и не выполняй действие от лица "
            "человека; объясни, что администратор должен связать Telegram и Bitrix identity."
        )
    destination = context.automation_destination
    automation = (
        "АВТОМАТИЗАЦИИ: регулярный запрос создавай через schedule_my_automation только после "
        "подтверждения расписания и достаточности инструментов. Передавай "
        f"delivery_channel='{destination['delivery_channel']}' и "
        f"delivery_conversation_id='{destination['delivery_conversation_id']}'. Результат обязан "
        "вернуться в этот канал; не угадывай канал по числовому id."
    )
    parts = [
        f"[Канал: {channel_label}. {identity} Отвечай по-русски. {presentation}]",
        actor,
        (
            "КАК ТЫ РАБОТАЕШЬ: твои возможности — ровно доступные в этом ходе инструменты, "
            "подключённые навыки и инструкции. Выбирай минимальное число действий. Если данных не "
            "хватает — собери их одним уточнением. Не имитируй отсутствующий инструмент. Любое "
            "изменение данных сначала подтверждай."
        ),
        # Агент закупок называл рабочую таблицу по своей роли, а ссылки на неё не было
        # нигде: ни в роли, ни в реестре таблиц, ни на Диске. Собеседник узнавал об этом
        # только следующим сообщением, а пробел оставался невидимым. Название без адреса
        # больше не выдаётся за известный документ.
        (
            "РАБОЧИЕ ТАБЛИЦЫ И ДОКУМЕНТЫ: называя рабочую таблицу, документ или папку, сразу "
            "давай ссылку на неё. Ссылка берётся из твоей роли либо из реестра таблиц компании. "
            "Если ссылки нет ни там, ни там — документа у тебя нет: скажи это прямо и попроси "
            "ссылку, вместо того чтобы называть его так, будто он у тебя есть."
        ),
        automation,
    ]
    if core_instructions.strip():
        parts.append("ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА РАБОТЫ:\n" + core_instructions.strip())
    skills = _joined_skills(selected_skills)
    if skills:
        parts.append("ТВОИ ПОДКЛЮЧЁННЫЕ НАВЫКИ:\n" + skills)
    personal = _joined_personal(personal_instructions)
    if personal:
        parts.append("ТВОИ ЛИЧНЫЕ ИНСТРУКЦИИ И НАВЫКИ:\n" + personal)
    if now is not None:
        parts.append("Текущие дата и время: " + now.strftime("%d.%m.%Y %H:%M") + " МСК.")
    return "\n\n".join(p for p in parts if p)


def load_profile_knowledge(slug: str, *, core_cap: int = 30000) -> dict[str, Any]:
    """Registry-only knowledge loader usable from both service roles without Flask imports."""
    from agent_knowledge import (
        load_agent_learned,
        load_instructions,
        load_manifest,
        load_skill_content,
        load_skills,
    )

    core_names = (
        "маршрутная карта",
        "порядок поиска",
        "формат ответа",
        "базовое поведение",
        "вопросы о возможностях и доступе",
        "критическое мышление",
    )
    core_rows = []
    instructions = load_instructions() or []
    used = set()
    for wanted in core_names:
        for item in instructions:
            name = str(item.get("name") or "").strip()
            if item.get("content") and name.lower() not in used and wanted in name.lower():
                core_rows.append("### " + name + "\n" + str(item["content"]))
                used.add(name.lower())
                break
    manifest = load_manifest(slug)
    # Match the proven Bitrix path: the main profile gets its Hermes skills from the gateway;
    # subagents receive only their selected skill metadata, with full bodies injected only for
    # custom registry skills that Hermes itself cannot load.
    selected_ids = set(manifest.get("skills") or []) if slug != "main" else set()
    selected = []
    for skill in load_skills() or []:
        if skill.get("id") not in selected_ids:
            continue
        entry = dict(skill)
        content = load_skill_content(str(skill.get("id") or "")) if skill.get("custom") else ""
        if content:
            entry["content"] = content[:30000]
        selected.append(entry)
    return {
        "core_instructions": "\n\n".join(core_rows)[:core_cap],
        "selected_skills": selected,
        "personal_instructions": load_agent_learned(slug) or [],
    }
