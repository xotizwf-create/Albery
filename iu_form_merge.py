# -*- coding: utf-8 -*-
"""Анкета приклеивается к карточке человека, а не заводит вторую.

Владелец 29.07.2026: «человек заходит в тг-бота — его лид попадает в Битрикс; потом он
заполняет форму, и лид автоматически переходит в стадию „Анкета“ вместе с анкетой; лид и
анкета соединяются в одну сделку».

Почему это отдельный сторож, а не проверка перед созданием. Сделку из анкеты создаёт САМ
Битрикс — код Albery в этот момент не спрашивают. Предотвратить появление второй карточки
нельзя, можно только забрать из неё данные и убрать её саму. Зато карточка в этот момент
пустая: ей секунды от роду, в ней нет ни истории, ни переписки, ни этапов, — поэтому «слияние»
здесь это перенос шести полей и контакта, а не разбор двух живых сделок.

Кого с кем склеивать, решают три признака по убыванию надёжности:
  1. `utm_content` с нашим токеном — его проставил бот, клиент к нему не прикасался;
  2. Telegram-username из анкеты — его печатает клиент, поэтому бывает в любом регистре и с «@»;
  3. ничего не совпало — НЕ ТРОГАЕМ. Лишняя карточка переживаема, потерянная заявка нет.

Слой решения (`match_target`, `fields_to_copy`, `next_stage_for`) чистый: на входе данные, на
выходе решение. Поэтому вся логика склейки проверяется тестами без Битрикса и без базы.
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger("iu-form-merge")

CATEGORY_ID = int(os.getenv("IU_FUNNEL_CATEGORY_ID", "16") or 16)
#: Стадия, на которую переезжает карточка по факту заполнения анкеты.
STAGE_ANKETA = os.getenv("IU_STAGE_ANKETA", "C16:UC_ANKETA").strip()
#: Стадия «Новый клиент»: только с неё двигаем вперёд. Ушедшую дальше карточку возвращать
#: назад нельзя — этап отражает то, что уже произошло.
STAGE_NEW = os.getenv("IU_STAGE_NEW", "C16:NEW").strip()

#: Поля анкеты. Порядок как в форме: Telegram, магазин, категории, оборот сейчас, план.
FORM_FIELDS = tuple(
    code.strip() for code in os.getenv(
        "IU_FORM_FIELDS",
        "UF_CRM_1784296997,UF_CRM_1784297026,UF_CRM_1784297137,"
        "UF_CRM_1784297181,UF_CRM_1784297221",
    ).split(",") if code.strip()
)

#: Сколько формовых сделок просматривать за проход. Больше не нужно: сторож ходит часто.
SCAN_LIMIT = int(os.getenv("IU_FORM_MERGE_SCAN", "30") or 30)
#: Выключатель на случай, если сторож начнёт вести себя не так.
ENABLED = os.getenv("IU_FORM_MERGE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}

_TG_MARKER_RE = re.compile(r"\[tg:(\d+)\]")


def telegram_id_in_title(title: str) -> int:
    """Telegram id из названия карточки. 0 — маркера нет."""
    match = _TG_MARKER_RE.search(str(title or ""))
    return int(match.group(1)) if match else 0


def clean_username(value) -> str:
    """Username в сравнимом виде: без «@», без ссылки, в нижнем регистре."""
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://t\.me/", "", text)
    return text.lstrip("@").strip()


#: Название карточки, которую заводит CRM-форма. Опознаём и по нему тоже: СПИСОК сделок
#: Битрикса не отдаёт служебные поля (`SOURCE_ID`, `UTM_*`) — они видны только в карточке
#: целиком. Первый живой прогон 29.07.2026 не нашёл ни одной анкеты именно поэтому.
_FORM_TITLE_RE = re.compile(r"заполнени\w*\s+crm[- ]?форм", re.I)


def is_form_deal(deal) -> bool:
    """Сделка родилась из CRM-формы, а не из переписки."""
    fields = (deal or {}).get("fields") or {}
    if str(fields.get("SOURCE_ID") or "").upper() == "WEBFORM":
        return True
    return bool(_FORM_TITLE_RE.search(str((deal or {}).get("title") or "")))


def token_of(deal) -> str:
    """Наш токен из метки сделки. Чужая рекламная метка даёт пустую строку."""
    import iu_form_link

    fields = (deal or {}).get("fields") or {}
    return iu_form_link.token_from_mark(fields.get("UTM_CONTENT"))


def fields_to_copy(form_deal, target_deal) -> dict:
    """Какие поля анкеты перенести. Уже заполненное в карточке не затираем.

    Затирать нельзя: в карточке из переписки может лежать то, что человек уточнил менеджеру
    голосом, и анкета, заполненная позже и небрежно, не должна это стирать."""
    source = (form_deal or {}).get("custom_fields") or {}
    target = (target_deal or {}).get("custom_fields") or {}
    out = {}
    for code in FORM_FIELDS:
        value = source.get(code)
        if value in (None, "", []):
            continue
        if target.get(code) in (None, "", []):
            out[code] = value
    return out


def next_stage_for(target_deal) -> str:
    """Куда двигать карточку. Пусто — не двигать.

    Вперёд и только с «Нового клиента»: карточка, ушедшая на согласование условий, из-за
    анкеты откатываться назад не должна."""
    current = str((target_deal or {}).get("stage_id") or "").strip()
    return STAGE_ANKETA if current == STAGE_NEW else ""


def match_target(form_deal, candidates, *, telegram_id: int = 0) -> tuple[int, str]:
    """К какой карточке приклеить анкету. Возвращает `(deal_id, чем опознали)`.

    `telegram_id` — уже разрешённый по токену человек (0, если токена не было).
    `candidates` — карточки той же воронки, кроме самой формовой.

    Из нескольких подходящих берётся САМАЯ РАННЯЯ: в ней история переписки, а поздние — как
    раз те дубли, ради которых всё это и делается."""
    if telegram_id:
        exact = [d for d in candidates
                 if telegram_id_in_title(d.get("title")) == telegram_id]
        if exact:
            return min(int(d["deal_id"]) for d in exact), "token"

    username = clean_username(
        ((form_deal or {}).get("custom_fields") or {}).get(FORM_FIELDS[0])
        if FORM_FIELDS else "")
    if username:
        by_name = []
        for deal in candidates:
            in_field = clean_username(
                (deal.get("custom_fields") or {}).get(FORM_FIELDS[0]))
            in_title = f"@{username}" in str(deal.get("title") or "").lower()
            if in_field == username or in_title:
                by_name.append(int(deal["deal_id"]))
        if by_name:
            return min(by_name), "username"

    return 0, "none"


# --- исполнение ------------------------------------------------------------------------------

def already_merged(conn, form_deal_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM iu_form_merges WHERE form_deal_id = %s", (int(form_deal_id),))
        return cur.fetchone() is not None


def remember(conn, *, form_deal_id: int, target_deal_id: int, telegram_id: int,
             matched_by: str, deleted: bool, note: str = "", payload=None) -> None:
    """Записать склейку. Снимок удалённой карточки хранится целиком: удаление необратимо."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO iu_form_merges (form_deal_id, target_deal_id, telegram_id,
                                        matched_by, deleted_form, note, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (form_deal_id) DO NOTHING
            """,
            (int(form_deal_id), target_deal_id or None, telegram_id or None, matched_by,
             bool(deleted), note[:500] or None,
             json.dumps(payload, ensure_ascii=False, default=str) if payload else None),
        )


def run_once(*, crm, conn, delete_form: bool = True) -> dict:
    """Один проход сторожа. Возвращает счётчики — их видно в журнале.

    `crm` — объект с методами `list_deals`, `get_deal`, `update_deal`, `comment`,
    `delete_deal`. Он подменяется в тестах, поэтому здесь нет ни Битрикса, ни сети."""
    import iu_form_link

    seen = {"scanned": 0, "merged": 0, "skipped": 0, "unmatched": 0}
    if not ENABLED:
        return seen

    deals = crm.list_deals(category_id=CATEGORY_ID, limit=SCAN_LIMIT)
    forms = [d for d in deals if is_form_deal(d)]
    candidates = [d for d in deals if not is_form_deal(d)]
    seen["scanned"] = len(forms)

    for listed in forms:
        form_id = int(listed["deal_id"])
        if already_merged(conn, form_id):
            seen["skipped"] += 1
            continue

        # Карточку читаем целиком: в списке Битрикс не отдаёт ни `SOURCE_ID`, ни метку —
        # только по ней и можно понять, чья это заявка.
        form = crm.get_deal(form_id)
        token = token_of(form)
        telegram_id = 0
        if token:
            row = iu_form_link.resolve(conn, token)
            telegram_id = int((row or {}).get("telegram_id") or 0)

        target_id, matched_by = match_target(form, candidates, telegram_id=telegram_id)
        if not target_id:
            # Ничего не совпало: карточку не трогаем вовсе. Лишняя карточка переживаема,
            # потерянная заявка — нет. Оператор разберёт её руками.
            seen["unmatched"] += 1
            log.info("анкета %s не опознана — оставляем оператору", form_id)
            continue

        target = crm.get_deal(target_id)
        payload = form
        updates = fields_to_copy(form, target)
        stage = next_stage_for(target)
        if updates or stage:
            crm.update_deal(target_id, custom_fields=updates or None, stage_id=stage or None)
        crm.comment(target_id, _merge_note(form_id, matched_by, updates, stage))

        deleted = False
        if delete_form:
            try:
                crm.delete_deal(form_id)
                deleted = True
            except Exception as exc:  # noqa: BLE001 — данные уже перенесены, дубль переживём
                log.warning("карточка анкеты %s не удалена: %s", form_id, str(exc)[:200])

        if token:
            iu_form_link.burn(conn, token, deal_id=form_id)
        remember(conn, form_deal_id=form_id, target_deal_id=target_id,
                 telegram_id=telegram_id, matched_by=matched_by, deleted=deleted,
                 payload=payload)
        seen["merged"] += 1
        log.info("анкета %s приклеена к %s (%s)", form_id, target_id, matched_by)

    return seen


def _merge_note(form_deal_id: int, matched_by: str, updates: dict, stage: str) -> str:
    how = {"token": "по персональной ссылке из бота",
           "username": "по Telegram-нику из анкеты"}.get(matched_by, matched_by)
    lines = [f"Клиент заполнил анкету — данные перенесены сюда ({how})."]
    if updates:
        lines.append(f"Заполнено полей: {len(updates)}.")
    if stage:
        lines.append("Этап переведён на «Анкета».")
    lines.append(f"Исходная карточка анкеты {form_deal_id} удалена как дубль.")
    return " ".join(lines)
