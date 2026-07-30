"""Один клиентский ход целиком: от сообщения до решения, что делать.

Владелец 26.07.2026: «просто заново сделаем эту систему с нуля и сделаем её работоспособной».

Было. Два почти одинаковых пути по 150 строк каждый (`reply_to_stranger` и `_autoreply_turn`)
— лестницы из проверок маркеров, regex-веток и вложенных отправок. Одно и то же правило жило в
обоих местах в двух редакциях, и правка одного молча расходилась с другим.

Стало. Один конвейер, семь шагов, каждый со своим отказом:

    фильтр входа → поиск знаний → промпт → модель → разбор контракта →
    разрешение действия → порог уверенности → фильтр выхода

Любой шаг может закончить ход передачей человеку, и это единственный способ закончить его
плохо: молчания без причины здесь нет.

Модуль чистый: ввод-вывод приходит в `Deps` функциями. Поэтому весь разговор — включая сбой
модели, инъекцию, ложное обещание и спор о цифрах — воспроизводится тестами без Telegram, CRM
и сети.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

import iu_contract
import iu_filters
import iu_funnel
import iu_knowledge
import iu_prompt

log = logging.getLogger("iu-turn")

# Что видит клиент, когда вопрос ушёл человеку. Молчать нельзя: инвариант «никогда не пропадать»
# оплачен диалогами, где люди ждали часами, не понимая, ответят ли им вообще.
ESCALATION_REPLY = os.getenv(
    "IU_ESCALATION_REPLY", "Уточню это у команды и вернусь с ответом.").strip()

# Насколько сообщение должно совпасть с прежним вопросом клиента, чтобы считаться повтором.
REPEAT_RATIO = float(os.getenv("IU_REPEAT_RATIO", "0.6") or 0.6)

# Реплика без самого вопроса требует уточнения, а не менеджера. Модель уже умеет отвечать
# правильно, но однажды вернула одновременно хороший уточняющий вопрос и техническое
# ``unresolved=["Конкретный вопрос клиента"]``. Общая логика эскалации приняла это поле за
# реальный неотвеченный вопрос и создала ложный вызов менеджера. Узкий детерминированный путь
# исключает неоднозначность, не задевая «помогите с комиссией» или «позовите менеджера».
VAGUE_HELP_REPLY = os.getenv(
    "IU_VAGUE_HELP_REPLY",
    "Конечно! Напишите, пожалуйста, с чем помочь — постараюсь разобраться.",
).strip()
_GREETING_PREFIX = (
    r"(?:(?:здравствуйте|здравствуй|добрый\s+(?:день|вечер|утро)|привет)"
    r"[\s!,.—-]*)?"
)
_VAGUE_HELP_RE = re.compile(
    rf"^\s*{_GREETING_PREFIX}(?:"
    r"помог(?:и|ите)(?:\s+мне)?|"
    r"(?:можете|сможете)\s+(?:мне\s+)?помочь|"
    r"(?:мне\s+)?нужна\s+помощь|"
    r"у\s+меня\s+(?:есть\s+)?вопрос|"
    r"можно\s+(?:задать\s+)?вопрос|"
    r"хочу\s+задать\s+вопрос"
    r")(?:\s*,?\s*пожалуйста)?[\s!?.]*$",
    re.I,
)


def is_vague_help_request(message: str) -> bool:
    """Просьба начать помощь без темы, факта или явного вызова человека."""

    return bool(_VAGUE_HELP_RE.fullmatch(str(message or "")))


@dataclass(frozen=True)
class Request:
    """Всё, что пришло снаружи на этот ход."""

    message: str
    name: str = ""
    history: str = ""
    role: str = ""
    facts: iu_funnel.DealFacts = field(default_factory=iu_funnel.DealFacts)


@dataclass(frozen=True)
class Deps:
    """Внешний мир одной структурой: модель, знания, правила фильтров, семантический скорер."""

    ask: object                                  # str -> str
    cards: tuple = ()
    rules: iu_filters.Ruleset = field(default_factory=iu_filters.Ruleset)
    rerank: object = None


@dataclass(frozen=True)
class Outcome:
    """Решение хода. Исполняет его вызывающий код, здесь только вывод."""

    reply: str = ""
    action: str = iu_contract.REPLY_ONLY
    escalate: bool = False
    reason: str = ""
    stage_move: str = ""
    sources: tuple[str, ...] = ()
    trace: dict = field(default_factory=dict)
    # Ответили ли клиенту ПО СУЩЕСТВУ. Это не то же самое, что «отправили непустой текст»:
    # «Уточню это у команды» и отказ фильтра — тоже текст, но вопрос остался открытым. Карточка
    # людям начинается именно с этого признака, и ошибка здесь означает, что сотрудник увидит
    # «клиенту отвечено» там, где клиент по-прежнему ждёт.
    answered_client: bool = False

    @property
    def silent(self) -> bool:
        return not self.reply.strip()


def is_repeat(message: str, history: str) -> bool:
    """Спрашивает ли клиент то же самое ещё раз.

    Владелец 26.07.2026: «если вопрос такой же, человек не понял, то нужно попробовать объяснить
    простым языком». Сравниваем основы слов, а не строки: «а какая комиссия?» и «так сколько
    комиссия в итоге?» — один и тот же вопрос.

    Делим на МЕНЬШЕЕ из двух множеств, а не на длину текущего сообщения. Переспрашивая, человек
    обычно добавляет слова раздражения («так сколько всё-таки…»), и деление на длину нового
    сообщения размывало совпадение до нуля — повтор переставал определяться именно тогда, когда
    он важнее всего."""
    current = iu_knowledge.stems(message)
    if not current:
        return False
    for line in str(history or "").split("\n"):
        if not line.startswith("Клиент:"):
            continue
        previous = iu_knowledge.stems(line[len("Клиент:"):])
        if not previous:
            continue
        overlap = len(current & previous) / min(len(current), len(previous))
        if overlap >= REPEAT_RATIO:
            return True
    return False


def _escalate(reason: str, *, reply: str = "", trace: dict | None = None,
              stage_move: str = "") -> Outcome:
    return Outcome(
        reply=reply if reply else ESCALATION_REPLY,
        action=iu_contract.HANDOFF,
        escalate=True,
        reason=reason,
        stage_move=stage_move,
        trace=trace or {},
    )


def handle(request: Request, deps: Deps) -> Outcome:
    """Провести один ход. Возвращает решение; отправку и CRM делает вызывающий код."""
    facts = request.facts
    # Этап мог отстать от реальности между ходами (анкета пришла, пока агент молчал).
    stage_move = iu_funnel.next_stage(facts)
    # «Не пришло, продублируйте» снимает защиту от повторной отправки: иначе она превращается
    # в отказ выслать документ, который до клиента не дошёл.
    resend = iu_funnel.resend_requested(request.message)
    trace: dict = {"stage": facts.stage, "stage_move": stage_move, "resend": resend}

    if is_vague_help_request(request.message):
        trace["conversational_clarification"] = True
        return Outcome(
            reply=VAGUE_HELP_REPLY,
            action=iu_contract.REPLY_ONLY,
            stage_move=stage_move,
            trace=trace,
        )

    # 1. Фильтр входа. Совпало — модель не зовём вовсе: дешевле и надёжнее, чем надеяться,
    # что она откажется сама.
    hit = iu_filters.check_incoming(request.message, deps.rules)
    if hit:
        trace["filtered_in"] = {"category": hit.category, "matched": hit.matched}
        # Брань — это почти всегда раздражённый живой клиент, а не хулиган: такого человека
        # должен увидеть менеджер. Политика и попытки сломать промпт — просто шум.
        needs_human = hit.category == iu_filters.PROFANITY
        return Outcome(
            reply=deps.rules.refusal,
            action=iu_contract.REPLY_ONLY,
            escalate=needs_human,
            reason=f"{hit.category}: {hit.matched}" if needs_human else "",
            stage_move=stage_move,
            trace=trace,
        )

    # 2. Знания. Модель получает базу ЦЕЛИКОМ, а не выбранную за неё карточку: подбор одной
    # карточки лексическим скором и был причиной «уточню у команды» на вопросы, ответ на
    # которые в базе есть. Порядок — по близости к вопросу, повторный вопрос берёт упрощённую
    # формулировку факта.
    repeated = is_repeat(request.message, request.history)
    found = iu_knowledge.everything(request.message, deps.cards, rerank=deps.rerank)
    sources = iu_knowledge.sources_text(found, simple=repeated)
    offered = iu_knowledge.offered_ids(found)
    retrieval = iu_knowledge.retrieval_score(found)
    human_when = iu_knowledge.human_required(found)
    trace.update({"retrieval": round(retrieval, 3), "shown": len(offered),
                  "repeated": repeated})

    # 3. Промпт.
    prompt = iu_prompt.build(iu_prompt.Context(
        message=request.message,
        name=request.name,
        role=request.role,
        stage=iu_funnel.title_of(facts),
        stage_goal=iu_funnel.goal_of(facts),
        knowledge=sources,
        offered_ids=offered,
        history=request.history,
        repeated_question=repeated,
        human_required=human_when,
        allowed_actions=iu_funnel.allowed_actions(facts, resend=resend),
    ))

    # 4. Модель. Её падение — не тишина клиенту, а передача человеку.
    try:
        raw = deps.ask(prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("модель не ответила: %s", str(exc)[:200])
        trace["model_error"] = str(exc)[:200]
        return _escalate(f"модель недоступна ({str(exc)[:120]})", trace=trace,
                         stage_move=stage_move)

    # 5. Контракт. Непонятый ответ никогда не превращается в сообщение клиенту.
    try:
        plan = iu_contract.parse(raw, offered_sources=offered)
    except iu_contract.ContractError as exc:
        trace["contract_error"] = str(exc)
        return _escalate(iu_contract.escalation_of(raw, exc), trace=trace,
                         stage_move=stage_move)

    trace.update({"action": plan.next_action, "confidence": plan.confidence,
                  "cited": list(plan.source_ids)})

    if plan.wants_human:
        return _escalate(plan.handoff_reason or "модель попросила человека",
                         trace=trace, stage_move=stage_move)

    # 6. Разрешение действия. Промпт не граница безопасности: последнее слово за воронкой.
    if not iu_funnel.may(plan.next_action, facts, resend=resend):
        trace["action_denied"] = plan.next_action
        claimed = iu_filters.claims_action(plan.reply)
        if claimed:
            # Текст уже пообещал сделанное — отдать его без действия значит соврать клиенту.
            return _escalate(
                f"модель обещала «{claimed}» действием {plan.next_action}, "
                f"недоступным на этапе {facts.stage or 'без сделки'}",
                trace=trace, stage_move=stage_move)
        plan = iu_contract.TurnPlan(
            reply=plan.reply, next_action=iu_contract.REPLY_ONLY,
            confidence=plan.confidence, intent=plan.intent, answered=plan.answered,
            unresolved=plan.unresolved, source_ids=plan.source_ids,
        )

    # 7. Безусловный запрет владельца отвечать самому сильнее любой уверенности. Условные
    # формулировки («если клиент спорит с расчётом») здесь НЕ принуждаются: выполнено ли
    # условие, видно только из сообщения клиента — это решает модель, получив условие в
    # промпте. Иначе один вопрос про комиссию уводил бы к людям каждый разговор о цене.
    always = iu_knowledge.always_human_for(found, plan.source_ids)
    if always:
        trace["human_required"] = always
        return _escalate(f"правило карточки знаний: {always}", trace=trace,
                         stage_move=stage_move)

    # 8. Порог уверенности. Опора хода — не «нашёл ли поиск карточку» (базу показали целиком),
    # а «сослался ли агент на настоящую карточку владельца». Ссылка проверяема: контракт хода
    # сверяет её со списком показанных, выдумать идентификатор нельзя. Утверждение о фактах
    # без ссылки на базу уходит человеку — это и есть замена прежнего порога поиска.
    grounded = 1.0 if plan.source_ids else 0.0
    verdict = iu_contract.assess(plan, retrieval=grounded, sources_text=sources,
                                 message=request.message)
    trace.update({"score": round(verdict.score, 3), "checked": verdict.checked,
                  "grounding": round(verdict.grounding, 3),
                  "threshold": iu_contract.threshold_for(plan, request.message)})
    if verdict.escalate:
        return _escalate("; ".join(verdict.reasons) or "уверенность ниже порога",
                         trace=trace, stage_move=stage_move)

    # 9. Фильтр выхода: модель могла написать то, чего клиент видеть не должен.
    out_hit = iu_filters.check_outgoing(plan.reply, deps.rules)
    if out_hit:
        trace["filtered_out"] = {"category": out_hit.category, "matched": out_hit.matched}
        return _escalate(f"ответ модели не прошёл фильтр ({out_hit.category}: "
                         f"{out_hit.matched})", trace=trace, stage_move=stage_move)

    # 10. Уступки. Владелец 26.07.2026: «нельзя самостоятельно предлагать скидки и тд ни в коем
    # случае, только то, что прописано в базе». Проверяем по источникам, а не по словарю: если
    # владелец сам написал про рассрочку — можно, если модель придумала — нельзя.
    gift = iu_filters.concession(plan.reply, sources)
    if gift:
        trace["concession"] = gift.matched
        return _escalate(f"модель предложила от себя «{gift.matched}» — в базе этого нет",
                         trace=trace, stage_move=stage_move)

    # 11. «Действие, потом слова»: обещание сделанного без исполненного действия.
    claimed = iu_filters.claims_action(plan.reply)
    if claimed and plan.next_action == iu_contract.REPLY_ONLY:
        trace["false_promise"] = claimed
        return _escalate(f"модель заявила «{claimed}», не выполнив действия", trace=trace,
                         stage_move=stage_move)

    # 12. Ровно один следующий шаг. Правило есть и в промпте, но послушание модели — не гарантия:
    # два вопроса подряд превращают консультацию в допрос.
    reply = iu_filters.one_next_step(plan.reply)
    if reply != plan.reply:
        trace["trimmed_steps"] = True

    return Outcome(
        # Уточняющий вопрос — полезный ответ в диалоге, но не ответ ПО СУЩЕСТВУ.
        # Для частичного ответа достаточно либо названной моделью темы, либо проверяемого
        # источника знаний; это удерживает разговор у ИИ, пока менеджер разбирает остаток.
        answered_client=bool(plan.answered or plan.source_ids),
        reply=reply,
        action=plan.next_action,
        escalate=bool(plan.unresolved),
        reason=("остались без ответа: " + "; ".join(plan.unresolved)) if plan.unresolved else "",
        stage_move=stage_move,
        sources=plan.source_ids,
        trace=trace,
    )
