"""Контракт одного клиентского хода: модель предлагает — код разрешает.

Владелец 26.07.2026: «сделаем RAG-систему гибкой, агент отвечает исходя из неё; если уверенность
выше 0.65 — отвечает, если нет — автоматическая эскалация человеку».

Почему не как было. Старый обмен с моделью шёл магическими строками: `ПОКАЖИ_УСЛОВИЯ`,
`НУЖЕН_ЧЕЛОВЕК`, `ТАКЖЕ_СПРОСИ_ЛЮДЕЙ`. Ветка срабатывала, только если модель вернула маркер
СЛОВО В СЛОВО и больше ничего; любая обёртка вокруг него ломала ход, а решение о действии
принималось сравнением строк. Отсюда «агент как скрипт»: он не выбирал действие, он попадал или
не попадал в шаблон.

Как здесь. Модель возвращает один объект `TurnPlan` с закрытым списком действий. Всё, что вне
контракта, — неизвестное поле, чужое действие, невалидная уверенность, ссылка на источник,
которого ей не давали, — считается сбоем разбора и уводит ход в передачу человеку. Это
fail-closed: непонятый ответ модели никогда не превращается в сообщение клиенту.

Порог уверенности намеренно НЕ равен само-оценке модели: модели переоценивают себя, и «я уверен
на 0.9» ничего не стоит. Итоговый скор собирается из трёх слагаемых, два из которых —
измеримые факты о самом ответе (нашлись ли карточки знаний и опирается ли текст на них), и лишь
одно — мнение модели.

Слой чистый: на входе строка ответа модели и уже известные факты, на выходе решение. Ни базы,
ни сети, ни Telegram — поэтому весь разбор проверяется тестами.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

# Закрытый список действий. Модель не может назвать инструмент, аргумент мутации или своё
# собственное действие: она выбирает одну из этих строк, а исполняет её код.
REPLY_ONLY = "reply_only"
SEND_TERMS = "send_terms"
SEND_FORM = "send_form"
SEND_CONTRACT = "send_contract"
HANDOFF = "handoff"

ACTIONS = frozenset({REPLY_ONLY, SEND_TERMS, SEND_FORM, SEND_CONTRACT, HANDOFF})

# Поля контракта. Всё, чего здесь нет, — повод не доверять ходу целиком.
_REQUIRED = frozenset({"reply", "next_action", "confidence"})
_OPTIONAL = frozenset({"intent", "answered", "unresolved", "source_ids", "handoff_reason"})
_ALLOWED = _REQUIRED | _OPTIONAL

# Порог из требования владельца. Вынесен в окружение, чтобы поднять его на время наладки
# без деплоя.
THRESHOLD = float(os.getenv("IU_CONFIDENCE_THRESHOLD", "0.65") or 0.65)

# Веса слагаемых уверенности. Мнение модели весит меньше всех намеренно: это единственная
# часть, которую нельзя проверить.
W_RETRIEVAL = 0.45
W_GROUNDING = 0.35
W_SELF = 0.20

# Числа и проценты, которые нельзя выдумывать: 44%, 30 000 ₽, 3 дня, 12.5.
_NUMBER_RE = re.compile(r"\d[\d\s.,]*")
# Слова, после которых ответ перестаёт быть просто разговором и становится утверждением о
# коммерции, сроках или праве. Именно на них агент врал больнее всего.
_FACTUAL_RE = re.compile(
    r"комисси\w*|тариф\w*|стоим\w*|цен[аыу]\w*|процент\w*|сроч?к\w*|налог\w*|НДС|"
    r"договор\w*|гарант\w*|ДРР|дрр|оборот\w*|скидк\w*|услови\w*|оплат\w*|штраф\w*|"
    r"маркировк\w*|Честный\s+знак",
    re.I,
)
# Модель любит обрамлять JSON пояснениями и ограждениями ```json — это нормально и не должно
# считаться сбоем.
_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```", re.I)


class ContractError(ValueError):
    """Ответ модели не является валидным ходом. Всегда ведёт к передаче человеку."""


@dataclass(frozen=True)
class TurnPlan:
    """Один разрешённый ход: что сказать клиенту и что должен сделать код."""

    reply: str
    next_action: str
    confidence: float
    intent: tuple[str, ...] = ()
    answered: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    handoff_reason: str = ""

    @property
    def wants_human(self) -> bool:
        return self.next_action == HANDOFF


@dataclass(frozen=True)
class Verdict:
    """Решение о ходе: пускать текст клиенту или звать человека."""

    allowed: bool
    score: float
    retrieval: float
    grounding: float
    self_report: float
    reasons: tuple[str, ...] = ()
    checked: bool = True     # False — ход не содержал фактов, порог не применялся

    @property
    def escalate(self) -> bool:
        return not self.allowed


def _strings(value, field_name: str) -> tuple[str, ...]:
    """Список строк или ничего. Строку вместо списка принимаем — это частая опечатка модели."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, list):
        raise ContractError(f"поле «{field_name}» должно быть списком строк")
    out = []
    for item in value:
        if not isinstance(item, (str, int, float)):
            raise ContractError(f"поле «{field_name}» содержит не строку")
        text = str(item).strip()
        if text:
            out.append(text)
    return tuple(out)


def _json_objects(raw: str):
    """Все сбалансированные JSON-объекты из болтливого вывода CLI, снаружи внутрь.

    Hermes зовётся подпроцессом и печатает ответ как текст: вокруг объекта бывают пояснения,
    ограждения и служебные строки. Ищем скобки вручную, а не регуляркой, потому что внутри
    строк JSON фигурные скобки — обычные символы."""
    text = _FENCE_RE.sub("", str(raw or ""))
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        depth = 0
        in_string = False
        escaped = False
        for pos in range(start, len(text)):
            ch = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[start:pos + 1]
                    break


def parse(raw: str, *, offered_sources: tuple[str, ...] | list[str] = ()) -> TurnPlan:
    """Разобрать ответ модели в ход. Любая неясность — исключение, а не догадка.

    `offered_sources` — карточки знаний, которые этому ходу реально показали. Модель не может
    сослаться на источник, которого не видела: выдуманный `source_id` означает выдуманный факт."""
    data = None
    for candidate in _json_objects(raw):
        try:
            parsed = json.loads(candidate)
        except Exception:  # noqa: BLE001 — обрывок текста, похожий на объект: пробуем следующий
            continue
        if isinstance(parsed, dict) and _REQUIRED & set(parsed):
            data = parsed
            break
    if data is None:
        raise ContractError("в ответе модели нет объекта хода")

    unknown = set(data) - _ALLOWED
    if unknown:
        # Fail-closed: лишнее поле значит, что модель играет по другому контракту, и остальным
        # её решениям в этом ходе доверять нельзя.
        raise ContractError(f"неизвестные поля хода: {', '.join(sorted(unknown))}")
    missing = _REQUIRED - set(data)
    if missing:
        raise ContractError(f"в ходе нет обязательных полей: {', '.join(sorted(missing))}")

    action = str(data.get("next_action") or "").strip()
    if action not in ACTIONS:
        raise ContractError(f"действие «{action}» вне контракта")

    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        raise ContractError("уверенность не является числом") from None
    if not 0.0 <= confidence <= 1.0:
        raise ContractError(f"уверенность {confidence} вне диапазона 0..1")

    reply = str(data.get("reply") or "").strip()
    handoff_reason = str(data.get("handoff_reason") or "").strip()
    if action == HANDOFF and not handoff_reason:
        raise ContractError("передача человеку без указанной причины")
    if action != HANDOFF and not reply:
        # Пустой текст при любом действии кроме передачи человеку — это молчание клиенту,
        # то самое, из-за которого люди ждали часами.
        raise ContractError("ход без текста клиенту")

    sources = _strings(data.get("source_ids"), "source_ids")
    allowed_sources = {str(s).strip() for s in offered_sources}
    invented = [s for s in sources if s not in allowed_sources]
    if invented:
        raise ContractError(f"ссылка на источник, которого не давали: {', '.join(invented)}")

    return TurnPlan(
        reply=reply,
        next_action=action,
        confidence=confidence,
        intent=_strings(data.get("intent"), "intent"),
        answered=_strings(data.get("answered"), "answered"),
        unresolved=_strings(data.get("unresolved"), "unresolved"),
        source_ids=sources,
        handoff_reason=handoff_reason,
    )


def states_facts(plan: TurnPlan) -> bool:
    """Утверждает ли ход что-то о деле, или это просто разговор.

    Порог охраняет ФАКТЫ, а не беседу. Без этого «Здравствуйте! Чем помочь?» пришлось бы
    отдавать человеку — у приветствия нет ни карточек знаний, ни источников, и любой порог оно
    провалило бы. Живой консультант так себя не ведёт."""
    if plan.answered:
        return True
    text = plan.reply
    return bool(_NUMBER_RE.search(text) or _FACTUAL_RE.search(text))


def _numbers(text: str) -> set[str]:
    """Числа в нормальном виде: «30 000» и «30000» — одно и то же."""
    out = set()
    for raw in _NUMBER_RE.findall(text or ""):
        digits = re.sub(r"[^\d]", "", raw)
        if digits:
            out.add(digits.lstrip("0") or "0")
    return out


def unbacked_numbers(plan: TurnPlan, sources_text: str) -> set[str]:
    """Числа ответа, которых нет в источниках.

    Это ВЕТО, а не слагаемое оценки. Средневзвешенный скор позволял высокому поиску и бодрой
    само-оценке модели перевесить проваленную проверку цифр: ответ «для вас сделаем 20%» при
    источнике с 44% набирал 0.81 и уходил клиенту. Цифра, взятая из воздуха, — самая дорогая
    ошибка агента, и она не может компенсироваться ничем."""
    return _numbers(plan.reply) - _numbers(sources_text)


def grounding_score(plan: TurnPlan, sources_text: str) -> tuple[float, tuple[str, ...]]:
    """Насколько текст ответа опирается на показанные карточки.

    Три механические проверки, каждая — треть оценки. Это не понимание смысла, но именно эти
    три вещи агент нарушал в реальных диалогах: называл цифру, которой нет в источнике; ссылался
    на источник задним числом; утверждал факт вообще без источника."""
    reasons: list[str] = []
    checks = []

    unbacked = _numbers(plan.reply) - _numbers(sources_text)
    checks.append(not unbacked)
    if unbacked:
        reasons.append(f"числа не подтверждены источником: {', '.join(sorted(unbacked))}")

    checks.append(bool(plan.source_ids))
    if not plan.source_ids:
        reasons.append("факт заявлен без ссылки на карточку знаний")

    # Ответ на вопрос обязан опираться хотя бы на одну карточку: «ответил, но не знаю откуда»
    # — это и есть выдумка, которую нельзя показывать клиенту.
    covered = not plan.answered or bool(plan.source_ids)
    checks.append(covered)
    if not covered:
        reasons.append("вопрос отмечен отвеченным, но источник не назван")

    return (sum(1 for ok in checks if ok) / len(checks)), tuple(reasons)


def assess(plan: TurnPlan, *, retrieval: float, sources_text: str = "",
           threshold: float | None = None) -> Verdict:
    """Пропустить ход к клиенту или отдать человеку.

    `retrieval` — реальный скор поиска по базе знаний (0..1): насколько уверенно нашлись
    карточки под вопрос клиента. Это единственная часть оценки, которую модель не контролирует
    вовсе, поэтому она весит больше остальных."""
    limit = THRESHOLD if threshold is None else float(threshold)
    retrieval = max(0.0, min(1.0, float(retrieval)))

    if plan.wants_human:
        return Verdict(False, 0.0, retrieval, 0.0, plan.confidence,
                       (f"модель сама попросила человека: {plan.handoff_reason}",))

    if not states_facts(plan):
        # Разговорный ход: здороваемся, уточняем, подтверждаем. Проверять нечего.
        return Verdict(True, 1.0, retrieval, 1.0, plan.confidence, (), checked=False)

    grounding, reasons = grounding_score(plan, sources_text)
    score = W_RETRIEVAL * retrieval + W_GROUNDING * grounding + W_SELF * plan.confidence

    invented = unbacked_numbers(plan, sources_text)
    if invented:
        return Verdict(False, score, retrieval, grounding, plan.confidence,
                       reasons + (f"вето: числа не подтверждены источником "
                                  f"({', '.join(sorted(invented))})",))
    if score < limit:
        reasons = reasons + (f"уверенность {score:.2f} ниже порога {limit:.2f}",)
        return Verdict(False, score, retrieval, grounding, plan.confidence, reasons)
    return Verdict(True, score, retrieval, grounding, plan.confidence, reasons)


def escalation_of(raw: str, exc: Exception) -> str:
    """Причина передачи человеку, когда ход не разобран. Пишется в карточку, не клиенту."""
    head = " ".join(str(raw or "").split())[:160]
    return f"ответ модели вне контракта ({exc}); начало ответа: {head or 'пусто'}"
