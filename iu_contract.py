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
from dataclasses import dataclass

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
# Владелец 26.07.2026: «когда дело касается расчётов — модель должна быть уверена на 95%+».
# Деньги — единственная тема, где ошибка агента сразу стоит доверия и денег: 25.07.2026 клиент
# поправлял агента, что 44% вычитается не от суммы к перечислению, а от продаж.
# Владелец 28.07.2026: «нужно, чтобы уверенность была 65%». Отдельный строгий порог для
# расчётов убран — он уводил к человеку обычные вопросы про цены и сроки. Защита от выдумок
# осталась на вете: цифра обязана быть из базы, из слов клиента или выводиться из них.
CALC_THRESHOLD = float(os.getenv("IU_CALC_THRESHOLD", "0.65") or 0.65)

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
# Запрос информации от клиента. Нужен, чтобы отличить «Здравствуйте» от вопроса: порог
# уверенности охраняет факты, а не приветствия.
_QUESTION_WORD_RE = re.compile(
    r"(?<![а-яё])(?:что|кто|где|когда|зачем|почему|как|каков\w*|кака\w*|каки\w*|какой|сколько)"
    r"(?![а-яё])", re.I)
_INFO_REQUEST_RE = re.compile(
    r"(?<![а-яё])(?:расскаж\w*|подскаж\w*|объясн\w*|уточн\w*|интересу\w*|"
    r"хочу\s+узнать|можно\s+узнать|пришл\w*|отправ\w*|покаж\w*|посчита\w*)", re.I)

# Модель любит обрамлять JSON пояснениями и ограждениями ```json — это нормально и не должно
# считаться сбоем.
_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```", re.I)

# РАСЧЁТ, а не просто упоминание денег. Разница принципиальна: «комиссия 44%» — это цитата
# ставки из базы, и её уже защищает вето на неподтверждённые числа. «Посчитайте мою экономику»
# или «44% вычитается от суммы к перечислению» — это вывод нового числа или трактовка того, от
# чего считается, и вот здесь ошибка стоит дороже всего (спор клиента 25.07.2026).
#
# Если считать расчётом любое «%» или «руб», строгий порог 0.95 требовал бы почти идеального
# поиска на КАЖДЫЙ вопрос о цене, и самый частый вопрос воронки всегда уходил бы человеку.
_CALC_RE = re.compile(
    r"расч[её]т\w*|посчита\w*|рассчита\w*|подсчита\w*|вычита\w*|вычест\w*|"
    r"выручк\w*|прибыл\w*|маржинальн\w*|\bмаржа\b|экономик\w*|рентабельн\w*|"
    # «Выплаты», «оборот» и «доход» намеренно НЕ здесь: это существительные, которые живут в
    # обычной речи («когда приходят выплаты», «какой у вас оборот?»). Считать их расчётом значит
    # включать строгий порог на вопрос агента КЛИЕНТУ и уводить к людям нормальный разговор.
    # Расчёт опознаётся глаголами действия и производными метриками, а не упоминанием денег.
    # ДРР убран из признаков расчёта 28.07.2026: «какой ДРР нужно держать» — вопрос о
    # требованиях программы, и ответ на него в базе прямой («требований к ДРР нет»). Расчёт с
    # участием ДРР всё равно ловится глаголами и «сколько я получу» из той же строки.
    r"окупаемост\w*|"
    r"сколько\s+(?:получ\w*|остан\w*|выйд\w*|вход\w*)|"
    r"от\s+чего\s+счита\w*|как\s+счита\w*",
    re.I,
)


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


def _asks_for_information(message: str) -> bool:
    """Спросил ли клиент что-то, на что нужен факт. Приветствие — не спросил."""
    value = str(message or "")
    return bool(
        "?" in value or "？" in value
        or _QUESTION_WORD_RE.search(value)
        or _INFO_REQUEST_RE.search(value)
    )


def states_facts(plan: TurnPlan, message: str = "") -> bool:
    """Утверждает ли ход что-то о деле, или это просто разговор.

    Порог охраняет ФАКТЫ, а не беседу. Без этого «Здравствуйте! Чем помочь?» пришлось бы
    отдавать человеку — у приветствия нет ни карточек знаний, ни источников, и любой порог оно
    провалило бы. Живой консультант так себя не ведёт.

    Поле `answered` НЕ является самостоятельным признаком факта: им управляет сама модель, и на
    проде 26.07.2026 она пометила ответ на «Здравствуйте!» как ответ на вопрос — ход попал под
    порог, не имея и не могущий иметь источников, и клиент получил «уточню у команды» вместо
    приветствия. Поэтому `answered` учитывается только вместе с реальным запросом информации от
    клиента, а числа и коммерческая лексика в тексте остаются самостоятельным признаком."""
    text = plan.reply
    if _NUMBER_RE.search(text) or _FACTUAL_RE.search(text):
        return True
    return bool(plan.answered and _asks_for_information(message))


#: В ответе агента слово о расчёте ещё не значит расчёт. «Программа рассчитана на селлеров»,
#: «расчёты по договору», «налог рассчитывает селлер» — обычные формулировки базы знаний, и
#: строгий порог на них уводил к человеку простые вопросы про ОСНО, договор и оборот (разбор
#: живых диалогов 28.07.2026). Поэтому в собственном ответе расчётом считается только то, где
#: агент действительно выводит число.
_CALC_REPLY_RE = re.compile(
    r"посчита\w*|подсчита\w*|"
    # Трактовка базы («44% вычитается от суммы к перечислению») — это и есть предмет спора
    # клиента 25.07.2026: цифра та же, а смысл другой.
    r"вычита\w*|вычест\w*|"
    r"расч[её]т\s+(?:состав\w+|получ\w+|такой|будет)|"
    r"(?:итого|получается|выходит|останется)\s*[:—-]?\s*\d|"
    r"маржинальн\w*|\bмаржа\b|рентабельн\w*|окупаемост\w*",
    re.I,
)


#: Клиент не спрашивает, а возражает: «вы неправильно считаете», «это не так», «вы ошиблись».
#: Спор о деньгах — зона человека. Живой случай 25.07.2026: агент вступил в пререкание о том,
#: от чего вычитается 44%, и был неправ. Порог тут ни при чём: даже уверенный ответ в споре
#: стоит дороже, чем пауза на менеджера.
_DISPUTE_RE = re.compile(
    r"не\s*правильно|неправильно|вы\s+ошиб\w*|это\s+не\s+так|"
    r"не\s+соглас\w*|вы\s+не\s+так\s+(?:счита\w*|пиш\w*|поня\w*)|"
    r"вычита\w+\s+не\s|считаете\s+не\s",
    re.I,
)


def is_dispute(message: str) -> bool:
    """Клиент оспаривает наши цифры или трактовку — такой ход всегда уходит человеку."""

    return bool(_DISPUTE_RE.search(str(message or "")))


def is_calculation(plan: TurnPlan, message: str = "") -> bool:
    """Идёт ли в ходе речь о расчёте.

    Просьбу клиента ловим широко: «посчитайте мою экономику», «от чего считается процент»,
    «какой ДРР держать» — там ошибка стоит дороже всего (спор клиента 25.07.2026).

    В собственном ответе — узко. Цитата ставки («комиссия 44%») расчётом не считается: новых
    чисел она не выводит, а её и без того защищает вето на числа, которых нет в источниках.
    """

    if _CALC_RE.search(str(message or "")):
        return True
    return bool(_CALC_REPLY_RE.search(str(plan.reply or "")))


def threshold_for(plan: TurnPlan, message: str = "") -> float:
    """Какой порог применять к этому ходу."""
    return CALC_THRESHOLD if is_calculation(plan, message) else THRESHOLD


#: Номер пункта нумерованного списка: «1.», «2)» в начале строки. Это оформление, а не факт.
#: Владелец 29.07.2026 попросил нумеровать перечисления — и четвёртый пункт списка тут же
#: сработал как «число не подтверждено источником», хотя никакой цифры агент не утверждал.
_LIST_ORDINAL_RE = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+")


def _numbers(text: str) -> set[str]:
    """Числа в нормальном виде: «30 000» и «30000» — одно и то же.

    Нумерация пунктов списка числами ответа не считается: это разметка."""
    out = set()
    for raw in _NUMBER_RE.findall(_LIST_ORDINAL_RE.sub("", text or "")):
        digits = re.sub(r"[^\d]", "", raw)
        if digits:
            out.add(digits.lstrip("0") or "0")
    return out


#: Признаки того, что агент ведёт расчёт, а не называет условие. Только в таком ответе
#: разрешены числа, полученные арифметикой: «наша комиссия для вас 42%» — это выдумка, а
#: «при вашей ставке 6% налог составит 42 ₽» — пересчёт нашего же примера.
_EXAMPLE_CONTEXT_RE = re.compile(
    r"пример\w*|например|посчита\w*|расч[её]т\w*|состав\w+|получ\w+|итого|"
    r"при\s+ваш\w+|у\s+вас|ваш\w+\s+ставк\w*",
    re.I,
)


def _derivable(base: set[str], message_numbers: set[str]) -> set[str]:
    """Числа, которые получаются арифметикой из наших данных и слов клиента.

    Владелец 28.07.2026: «если клиент говорит, у меня 6% налог, агент просто пересчитывает из
    примера». Пересчёт неизбежно рождает новое число, поэтому запрет на любые новые числа
    делал такую гибкость невозможной. Здесь разрешается ровно то, что выводится из уже
    известных величин: проценты, доли и суммы. Ставка, взятая из воздуха, так не выводится.
    """

    values: list[float] = []
    for raw in base | message_numbers:
        try:
            values.append(float(raw))
        except ValueError:
            continue
    out: set[str] = set()
    for left in values:
        for right in values:
            for candidate in (
                left * right / 100.0,      # процент от суммы
                left - left * right / 100.0,  # сумма за вычетом процента
                left + right,
                left - right,
                left * right,
                left / right if right else 0.0,
            ):
                if candidate <= 0 or candidate != candidate:
                    continue
                out.add(str(int(round(candidate))))
    return out


def unbacked_numbers(plan: TurnPlan, sources_text: str, message: str = "") -> set[str]:
    """Числа ответа, которые не подтверждены ни базой, ни словами клиента, ни расчётом из них.

    Это ВЕТО, а не слагаемое оценки. Средневзвешенный скор позволял высокому поиску и бодрой
    само-оценке модели перевесить проваленную проверку цифр: ответ «для вас сделаем 20%» при
    источнике с 44% набирал 0.81 и уходил клиенту. Цифра, взятая из воздуха, — самая дорогая
    ошибка агента, и она не может компенсироваться ничем.

    Пересчёт под цифру клиента выдумкой не считается: он выводится из чисел базы и того, что
    назвал сам клиент, и разрешён только там, где агент действительно ведёт пример.
    """

    known = _numbers(sources_text) | _numbers(message)
    unknown = _numbers(plan.reply) - known
    if not unknown:
        return set()
    if not _EXAMPLE_CONTEXT_RE.search(plan.reply or ""):
        return unknown
    return unknown - _derivable(_numbers(sources_text), _numbers(message))


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
           threshold: float | None = None, message: str = "") -> Verdict:
    """Пропустить ход к клиенту или отдать человеку.

    `retrieval` — реальный скор поиска по базе знаний (0..1): насколько уверенно нашлись
    карточки под вопрос клиента. Это единственная часть оценки, которую модель не контролирует
    вовсе, поэтому она весит больше остальных.

    Порог выбирается по теме хода: разговор о деньгах и расчётах судится по `CALC_THRESHOLD`."""
    limit = threshold_for(plan, message) if threshold is None else float(threshold)
    retrieval = max(0.0, min(1.0, float(retrieval)))

    if plan.wants_human:
        return Verdict(False, 0.0, retrieval, 0.0, plan.confidence,
                       (f"модель сама попросила человека: {plan.handoff_reason}",))

    if is_dispute(message):
        return Verdict(False, 0.0, retrieval, 0.0, plan.confidence,
                       ("клиент спорит о наших цифрах — такой разговор ведёт человек",))

    if not states_facts(plan, message):
        # Разговорный ход: здороваемся, уточняем, подтверждаем. Проверять нечего.
        return Verdict(True, 1.0, retrieval, 1.0, plan.confidence, (), checked=False)

    grounding, reasons = grounding_score(plan, sources_text)
    score = W_RETRIEVAL * retrieval + W_GROUNDING * grounding + W_SELF * plan.confidence

    invented = unbacked_numbers(plan, sources_text, message)
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
