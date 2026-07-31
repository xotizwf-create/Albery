"""Проверки качества сообщений агента — механические, без модели.

Владелец 25.07.2026: «это ИИ продажник и консультант, нельзя чтобы он клевал в грязь лицом».

Судья на модели нужен для тона, но большая часть провалов этой недели ловится арифметикой и
разбором строки — дешевле, быстрее и без предвзятости LLM. Каждая проверка родилась из живого
случая, поэтому в описании стоит диалог, где это произошло.

Используется дважды: ночным обзором на реальной переписке (`scripts/quality_review.py`) и
тестами — на золотом наборе.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

GREETINGS = ("здравствуй", "добрый день", "добрый вечер", "доброе утро", "приветств", "привет")

# Обещания, которых агент не выполнит: инструментов для этого у него нет.
FORBIDDEN = (
    (re.compile(r"посчита\w+\s+(экономику|юнит)|расчёт\s+экономики|посмотрим\s+экономику", re.I),
     "обещает расчёт экономики — такого инструмента у агента нет (диалог 764181402, 24.07.2026)"),
    (re.compile(r"пришлите\s+артикул|скиньте\s+артикул|артикул\s+или\s+ссылку", re.I),
     "просит артикул или ссылку на товар — анализировать их агент не умеет (диалог 764181402)"),
    (re.compile(r"в течени\w+\s+\d+\s+минут|через\s+\d+\s+минут", re.I),
     "называет срок ответа от себя — за него это решают люди"),
)

# Служебные строки и маркеры, которые клиент видеть не должен.
LEAKS = (
    (re.compile(r"ПОКАЖИ_УСЛОВИЯ|НУЖЕН_ЧЕЛОВЕК|ТАКЖЕ_СПРОСИ_ЛЮДЕЙ|НЕТ_ОТВЕТА"),
     "служебный маркер утёк клиенту"),
    (re.compile(r"\[ЗАПОЛНИТЬ\]|--- ТЕКСТ КЛИЕНТУ|Источник:\s*https://docs\.google"),
     "внутренняя разметка документа утекла клиенту (23.07.2026)"),
    (re.compile(r"техническая заминка|я\s+—?\s*(бот|ИИ|искусственный интеллект)", re.I),
     "агент говорит о себе как о боте или прячется за «технической заминкой»"),
)


@dataclass(frozen=True)
class Issue:
    """Найденная проблема в одном сообщении."""
    kind: str
    detail: str


def _questions(text: str) -> int:
    return (text or "").count("?")


def check_message(text: str, *, first_in_dialog: bool = False,
                  is_verbatim_block: bool = False) -> list[Issue]:
    """Проверить ОДНО сообщение агента. Пустой список — всё в порядке.

    `is_verbatim_block` — внутри дословный документ владельца: его длину и число вопросов не
    считаем нарушением, текст задан не агентом."""
    text = (text or "").strip()
    issues: list[Issue] = []
    if not text:
        return [Issue("пусто", "агент отправил пустое сообщение")]

    if first_in_dialog and not any(g in text[:120].lower() for g in GREETINGS):
        issues.append(Issue("нет приветствия",
                            "первое сообщение человеку без приветствия (диалоги 256942600 и "
                            "1451982360, 25.07.2026)"))
    for pattern, why in FORBIDDEN:
        if pattern.search(text):
            issues.append(Issue("невыполнимое обещание", why))
    for pattern, why in LEAKS:
        if pattern.search(text):
            issues.append(Issue("утечка служебного", why))
    if not is_verbatim_block:
        if _questions(text) > 1:
            issues.append(Issue("много вопросов",
                                f"в сообщении {_questions(text)} вопроса — клиенту труднее "
                                "отвечать (правило одного вопроса)"))
        if len(text) > 1200:
            issues.append(Issue("простыня",
                                f"{len(text)} символов без дословного документа — читать тяжело"))
    return issues


def check_dialog(messages: list[dict]) -> list[Issue]:
    """Проверить переписку целиком: тут видны провалы, которых не видно в одном сообщении.

    `messages`: [{"direction": "in"|"out", "text": ..., "verbatim": bool}] по возрастанию времени.
    """
    issues: list[Issue] = []
    seen_out = False
    for msg in messages:
        text = (msg.get("text") or "").strip()
        if msg.get("direction") != "out" or msg.get("customer_visible") is False:
            continue
        issues += check_message(text, first_in_dialog=not seen_out,
                               is_verbatim_block=bool(msg.get("verbatim")))
        seen_out = True

    # Internal/error journal rows are not customer replies. A visible handoff receipt prevents
    # unexplained silence, but it is not a substantive answer until a manager actually replies.
    for i, msg in enumerate(messages):
        if msg.get("direction") != "in" or "?" not in (msg.get("text") or ""):
            continue
        visible = [
            m for m in messages[i + 1:]
            if m.get("direction") == "out" and m.get("customer_visible") is not False
        ]
        if not visible:
            issues.append(Issue("вопрос без ответа",
                                f"клиент спросил «{(msg.get('text') or '')[:60]}» и ответа не "
                                "получил (случай Георгия, 22.07.2026)"))
            break
        if not any(m.get("substantive") is not False for m in visible):
            issues.append(Issue(
                "ожидает менеджера",
                f"клиент получил подтверждение по вопросу «{(msg.get('text') or '')[:60]}», "
                "но содержательного ответа менеджера ещё нет",
            ))
            break
    return issues


def summary(issues: list[Issue]) -> str:
    """Короткая сводка для отчёта."""
    if not issues:
        return "нарушений нет"
    by_kind: dict[str, int] = {}
    for issue in issues:
        by_kind[issue.kind] = by_kind.get(issue.kind, 0) + 1
    return ", ".join(f"{kind}: {count}" for kind, count in sorted(by_kind.items()))
