"""Граница автоотправки: что агент вправе отправить поставщику сам, а что — только черновиком.

Владелец выбрал гибрид: рутина по утверждённым шаблонам уходит сама, всё остальное идёт к
человеку. Правило живёт В КОДЕ, а не в инструкции агента, и это осознанно: 17.08.2026
инструкция «проверяй результат» существовала, агент её выполнял — и всё равно отдал
сломанную таблицу. Письмо внешнему контрагенту не отзывается, поэтому здесь нужен запрет,
а не просьба.

По умолчанию реестр ПУСТ: пока владелец не назвал типы писем, автоматически не уходит
ничего. Это не заглушка, а рабочее состояние — «разрешено ничего» безопаснее, чем
«разрешено на усмотрение модели».
"""
from __future__ import annotations

import re
from typing import Any

import mail

# Утверждённые владельцем шаблоны. Ключ — имя, по которому агент их запрашивает.
#   required: поля, без которых письмо не соберётся (пустое «{{цена}}» ушло бы поставщику)
#   subject/body: текст с подстановками {{поле}}
# Добавлять сюда — значит РАЗРЕШИТЬ отправку без человека. Только по прямому слову владельца.
APPROVED_TEMPLATES: dict[str, dict[str, Any]] = {}

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Zа-яА-Я0-9_]+)\s*\}\}")


class TemplateError(ValueError):
    """Шаблон не собрался — письмо не должно уйти ни в каком виде."""


def render(template_name: str, values: dict[str, Any]) -> tuple[str, str]:
    """Собрать тему и текст из шаблона. Любая незаполненная подстановка — отказ."""
    tpl = APPROVED_TEMPLATES.get(template_name)
    if not tpl:
        raise TemplateError(f"шаблон «{template_name}» не утверждён")

    missing = [f for f in tpl.get("required", []) if not str(values.get(f, "")).strip()]
    if missing:
        raise TemplateError(f"не заполнены обязательные поля: {', '.join(missing)}")

    def fill(text: str) -> str:
        def swap(match: re.Match) -> str:
            key = match.group(1)
            val = str(values.get(key, "")).strip()
            if not val:
                # Иначе поставщик получит письмо с дырой «{{срок}}» — хуже, чем не отправить.
                raise TemplateError(f"в шаблоне осталась незаполненная подстановка «{key}»")
            return val
        return _PLACEHOLDER_RE.sub(swap, text)

    return fill(str(tpl["subject"])), fill(str(tpl["body"]))


def send_or_draft(to: str, subject: str, body: str, *, template: str = "",
                  values: dict[str, Any] | None = None, cc: str = "",
                  thread_id: str = "", reply_to_message_id: str = "",
                  creds: Any = None) -> dict[str, Any]:
    """Отправить письмо, ЕСЛИ оно собрано из утверждённого шаблона; иначе — черновик.

    Возвращает `action`: "sent" или "drafted", и `reason` — почему именно так. Причина
    нужна человеку: «письмо не ушло» без объяснения выглядит как поломка.
    """
    if template:
        try:
            subject, body = render(template, values or {})
        except TemplateError as exc:
            draft = mail.mail_create_draft(to, subject or f"[черновик] {template}",
                                           body or "", cc=cc, thread_id=thread_id,
                                           reply_to_message_id=reply_to_message_id, creds=creds)
            return {"action": "drafted", "reason": f"шаблон не собрался: {exc}", **draft}
        sent = mail.mail_send_raw(to, subject, body, cc=cc, thread_id=thread_id,
                                  reply_to_message_id=reply_to_message_id, creds=creds)
        return {"action": "sent", "reason": f"утверждённый шаблон «{template}»",
                "template": template, **sent}

    draft = mail.mail_create_draft(to, subject, body, cc=cc, thread_id=thread_id,
                                   reply_to_message_id=reply_to_message_id, creds=creds)
    return {
        "action": "drafted",
        "reason": ("письмо собрано не по утверждённому шаблону — оно ждёт вашего просмотра "
                   "в черновиках"),
        **draft,
    }


def approved_template_names() -> list[str]:
    """Что сейчас разрешено отправлять без человека. Пусто — значит ничего."""
    return sorted(APPROVED_TEMPLATES)
