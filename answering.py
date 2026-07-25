"""Ответы на вопросы клиента: отвечаем на то, что знаем, остальное честно уносим людям.

Владелец 25.07.2026: «если он знает ответ на несколько вопросов, пусть отвечает на них, а на то
что не знает — пусть эскалирует менеджеру».

Как было. Решение принималось по всему сообщению целиком: либо агент отвечал, либо молча уносил
вопрос людям. Живой случай (диалог 764181402): клиент спросил «Какой дрр нужно держать и как
происходит управление? +какая комиссия ваша по партнерской этой программе?» — три вопроса в одном
сообщении. Про комиссию ответ в документе условий ЕСТЬ, про ДРР и управление — нет. Агент выслал
весь документ второй раз, потом (после правки) вообще ушёл в «уточню у команды»: клиент не получил
ни одного ответа, хотя один из трёх был под рукой.

Как здесь. Сообщение разбирается на отдельные вопросы; на каждый ищется ответ ТОЛЬКО в
предоставленных источниках (документ условий, база знаний). Ответ без опоры на источник
отбраковывается механически: если в нём есть числа, которых нет в источниках, он считается
выдумкой. Что осталось без ответа — уходит людям одной карточкой, и клиент об этом честно
предупреждён в том же сообщении.

Слой чистый: модель приходит параметром (`ask`), поэтому весь разбор проверяется тестами.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger("answering")

NO_ANSWER = "НЕТ_ОТВЕТА"

# Разделители вопросов внутри одного сообщения: перевод строки, «+» в начале мысли, знак вопроса.
_SPLIT_RE = re.compile(r"[\n\r]+|(?<=\?)\s+|\s+\+(?=\S)")
# Числа и проценты, которые нельзя выдумывать: 44%, 30 000 ₽, 3 дней, 12.5.
_NUMBER_RE = re.compile(r"\d[\d\s.,]*")


@dataclass(frozen=True)
class Answered:
    """Один вопрос клиента и что с ним стало."""
    question: str
    answer: str = ""          # пусто — ответа в источниках нет
    source: str = ""          # чем подтверждён ответ

    @property
    def known(self) -> bool:
        return bool(self.answer.strip())


def split_questions(text: str) -> list[str]:
    """Разобрать сообщение на отдельные вопросы/просьбы.

    Клиенты пишут по три вопроса в одной строке через «+» и без знаков препинания — поэтому
    режем и по переводам строк, и по «+», и после знака вопроса."""
    parts = [p.strip(" \t-–—•") for p in _SPLIT_RE.split(text or "") if p and p.strip()]
    return [p for p in parts if len(p) > 2]


def _numbers(text: str) -> set[str]:
    """Числа из текста в нормальном виде: «30 000» и «30000» — одно и то же."""
    out = set()
    for raw in _NUMBER_RE.findall(text or ""):
        digits = re.sub(r"[^\d]", "", raw)
        if digits:
            out.add(digits.lstrip("0") or "0")
    return out


def grounded(answer: str, sources: str) -> bool:
    """Ответ опирается на источники?

    Механическая проверка вместо доверия: все числа ответа обязаны встречаться в источниках.
    Именно на числах агент врал больнее всего — цифры условий гуляли от диалога к диалогу."""
    if not answer.strip():
        return False
    return _numbers(answer) <= _numbers(sources)


_PROMPT = """Ты отвечаешь клиенту от лица компании. Ниже — ИСТОЧНИКИ и ВОПРОСЫ клиента.

Правила:
- отвечай ТОЛЬКО тем, что есть в источниках; ничего не добавляй от себя;
- нет ответа в источниках — верни для этого вопроса строку {no_answer};
- ответ короткий, 1-2 предложения, по-человечески, без канцелярита;
- цифры бери из источников буква в букву, не пересчитывай и не округляй.

ИСТОЧНИКИ:
{sources}

ВОПРОСЫ:
{questions}

Верни СТРОГО JSON-массив без пояснений, по объекту на каждый вопрос в том же порядке:
[{{"вопрос": "...", "ответ": "... или {no_answer}", "источник": "чем подтверждено"}}]"""


def _parse(raw: str, questions: list[str]) -> list[dict]:
    """Вытащить JSON из ответа модели. Модель любит обрамлять его текстом — это нормально."""
    text = (raw or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001 — сломанный JSON не должен ронять ход
        log.warning("ответ модели не разобран как JSON: %s", (raw or "")[:200])
        return []
    return data if isinstance(data, list) else []


def answer_questions(questions: list[str], sources: str, ask) -> list[Answered]:
    """Ответить на каждый вопрос по источникам. `ask` — вызов модели (строка → строка)."""
    if not questions:
        return []
    numbered = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    prompt = _PROMPT.format(sources=sources.strip(), questions=numbered, no_answer=NO_ANSWER)
    try:
        raw = ask(prompt)
    except Exception:  # noqa: BLE001 — молчание модели = вопросы уходят людям
        log.warning("модель не ответила на разбор вопросов", exc_info=True)
        return [Answered(q) for q in questions]

    rows = _parse(raw, questions)
    out: list[Answered] = []
    for i, question in enumerate(questions):
        row = rows[i] if i < len(rows) and isinstance(rows[i], dict) else {}
        answer = str(row.get("ответ") or row.get("answer") or "").strip()
        source = str(row.get("источник") or row.get("source") or "").strip()
        if NO_ANSWER in answer.upper() or not answer:
            out.append(Answered(question))
            continue
        if not grounded(answer, sources):
            # Ответ не опирается на источники — считаем, что ответа нет. Лучше отдать людям,
            # чем сказать клиенту выдуманную цифру.
            log.info("ответ на «%s» отброшен: числа не подтверждены источниками", question[:60])
            out.append(Answered(question))
            continue
        out.append(Answered(question, answer=answer, source=source))
    return out


def unknown(results: list[Answered]) -> list[str]:
    """Вопросы, на которые ответа не нашлось — их уносим людям."""
    return [r.question for r in results if not r.known]


def client_text(results: list[Answered], *, pending_note: str = "") -> str:
    """Текст клиенту: ответы на известное + честная строка про остальное."""
    lines = [r.answer.strip() for r in results if r.known]
    if not lines:
        return ""
    body = "\n\n".join(lines)
    return f"{body}\n\n{pending_note}".strip() if pending_note else body
