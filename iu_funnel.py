"""Воронка ИУ как данные: этап — это факт, а не мнение модели.

Владелец 26.07.2026: «Новый клиент — Согласование условий — Анкета — Подписание договора —
Договор подписан — дальше как есть». Он же: «агент должен двигать сделку по воронке исходя из
переписки, опять же, опираясь только на факты».

Два требования выглядят противоречиво, но противоречия нет. Переписка определяет, ЧТО агент
делает (высылает условия, даёт анкету, собирает договор). Этап меняется от РЕЗУЛЬТАТА этого
действия, а не от слов клиента. «Я заполнил анкету» не двигает сделку — двигают данные анкеты,
появившиеся в полях сделки.

Почему так строго. Раньше этап двигался по факту доставки ответа («ответили — значит
связались») и по regex-веткам поверх текста клиента. Состояние сделки расходилось с реальностью,
а агент строил на нём следующую реплику — и выглядел как человек, не понимающий, что происходит.

Модель не может подвинуть этап вовсе: в контракте хода (`iu_contract.TurnPlan`) нет поля этапа.
Она предлагает действие, а переход считает этот модуль по снимку проверяемых фактов.

Идентификаторы этапов переопределяются окружением: при пересоздании стадий в Битриксе меняется
окружение, а не код.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import iu_contract

# --- этапы -------------------------------------------------------------------------------
# «Связались» (C16:CONTACTED) владелец убрал 26.07.2026. Пока сделки с него не перенесены в
# Битриксе, он читается как «Новый клиент»: молча потерять сделку хуже, чем лишний этап.
STAGE_NEW = os.getenv("CRM_STAGE_NEW", "C16:NEW").strip()
STAGE_CONTACTED_LEGACY = os.getenv("CRM_STAGE_CONTACTED", "C16:CONTACTED").strip()
STAGE_TERMS = os.getenv("CRM_STAGE_TERMS", "C16:S84294149").strip()
STAGE_FORM = os.getenv("CRM_STAGE_FORM_DONE", "C16:UC_ANKETA").strip()
# Идентификаторы двух последних этапов ОБЯЗАНЫ быть проверены по живому Битриксу до деплоя:
# значения ниже — заготовка, а не факт.
STAGE_SIGNING = os.getenv("CRM_STAGE_SIGNING", "C16:UC_SIGNING").strip()
STAGE_SIGNED = os.getenv("CRM_STAGE_SIGNED", "C16:UC_SIGNED").strip()


@dataclass(frozen=True)
class DealFacts:
    """Снимок проверяемых фактов сделки. Ничего из этого нельзя вывести из слов клиента."""

    stage: str = ""
    terms_delivered: bool = False      # документ условий реально доставлен
    form_filled: bool = False          # данные анкеты есть в полях сделки
    contract_sent: bool = False        # PDF собран и доставлен
    contract_signed: bool = False      # подписание подтверждено человеком или ЭДО


@dataclass(frozen=True)
class Stage:
    """Этап воронки: чем подтверждается вход и что агенту делать дальше."""

    id: str
    title: str
    goal: str
    reached: object                     # DealFacts -> bool
    actions: tuple[str, ...]


CHAIN: tuple[Stage, ...] = (
    Stage(
        id=STAGE_NEW,
        title="Новый клиент",
        goal="клиент увидел условия ИУ",
        reached=lambda f: True,
        actions=(iu_contract.REPLY_ONLY, iu_contract.SEND_TERMS,
                 iu_contract.SEND_FORM, iu_contract.HANDOFF),
    ),
    Stage(
        id=STAGE_TERMS,
        title="Согласование условий",
        goal="клиент заполнил анкету",
        reached=lambda f: f.terms_delivered,
        actions=(iu_contract.REPLY_ONLY, iu_contract.SEND_TERMS,
                 iu_contract.SEND_FORM, iu_contract.HANDOFF),
    ),
    Stage(
        id=STAGE_FORM,
        title="Анкета",
        goal="договор собран и отправлен на согласование",
        reached=lambda f: f.form_filled,
        actions=(iu_contract.REPLY_ONLY, iu_contract.SEND_CONTRACT, iu_contract.HANDOFF),
    ),
    Stage(
        id=STAGE_SIGNING,
        title="Подписание договора",
        goal="договор подписан обеими сторонами",
        reached=lambda f: f.contract_sent,
        actions=(iu_contract.REPLY_ONLY, iu_contract.HANDOFF),
    ),
    Stage(
        id=STAGE_SIGNED,
        title="Договор подписан",
        goal="дальше сделку ведут существующие этапы: счёт, оплата, подключение",
        reached=lambda f: f.contract_signed,
        actions=(iu_contract.REPLY_ONLY, iu_contract.HANDOFF),
    ),
)

_BY_ID = {stage.id: stage for stage in CHAIN}
_ORDER = {stage.id: i for i, stage in enumerate(CHAIN)}


def normalize(stage_id: str) -> str:
    """Привести этап к тому, что понимает цепочка. Убранный «Связались» = «Новый клиент»."""
    value = str(stage_id or "").strip()
    if value == STAGE_CONTACTED_LEGACY:
        return STAGE_NEW
    return value


def position(stage_id: str) -> int:
    """Место этапа в цепочке; -1 — этап за её пределами (счёт, подключение, отказ)."""
    return _ORDER.get(normalize(stage_id), -1)


def stage_of(stage_id: str) -> Stage | None:
    return _BY_ID.get(normalize(stage_id))


def earned_stage(facts: DealFacts) -> Stage:
    """Самый дальний этап, вход на который ПОДТВЕРЖДЁН фактами."""
    earned = CHAIN[0]
    for stage in CHAIN:
        if stage.reached(facts):
            earned = stage
    return earned


def next_stage(facts: DealFacts) -> str:
    """На какой этап перевести сделку. Пусто — переводить не нужно.

    Назад не двигаем никогда: за пределами нашей цепочки сделку ведут люди и другие этапы
    (счёт, оплата, подключение, отказ), и вернуть её оттуда в «Анкету» значит сломать чужую
    работу."""
    current = position(facts.stage)
    if current < 0 and facts.stage:
        return ""
    earned = earned_stage(facts)
    return earned.id if _ORDER[earned.id] > max(current, -1) else ""


def allowed_actions(facts: DealFacts) -> tuple[str, ...]:
    """Что модели разрешено предложить на текущем этапе.

    Список сужается фактами: если условия уже доставлены, отправлять их снова незачем —
    повторная отправка того же документа была одной из главных жалоб."""
    stage = stage_of(facts.stage) or earned_stage(facts)
    actions = list(stage.actions)
    if facts.terms_delivered and iu_contract.SEND_TERMS in actions:
        actions.remove(iu_contract.SEND_TERMS)
    if facts.form_filled and iu_contract.SEND_FORM in actions:
        actions.remove(iu_contract.SEND_FORM)
    if facts.contract_sent and iu_contract.SEND_CONTRACT in actions:
        actions.remove(iu_contract.SEND_CONTRACT)
    return tuple(actions)


def goal_of(facts: DealFacts) -> str:
    """Что нужно для следующего этапа — эта строка идёт в промпт как ориентир, не как скрипт."""
    stage = stage_of(facts.stage) or earned_stage(facts)
    return stage.goal


def title_of(facts: DealFacts) -> str:
    stage = stage_of(facts.stage) or earned_stage(facts)
    return stage.title


def may(action: str, facts: DealFacts) -> bool:
    """Разрешено ли действие. Последний рубеж перед исполнением: промпт не граница безопасности."""
    return action in allowed_actions(facts)
