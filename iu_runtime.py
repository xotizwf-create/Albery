"""Внешний мир для конвейера ИУ: знания, правила фильтров, факты сделки, исполнение решения.

`iu_turn` намеренно ничего не знает про Telegram, CRM и базу — поэтому весь разговор
проверяется тестами. Здесь живёт вторая половина: чтение того, что нужно конвейеру, и
исполнение того, что он решил.

Разделение важно не ради красоты. Раньше решение и его исполнение были перемешаны внутри
150-строчных функций: чтобы проверить «а что будет, если модель предложит договор на первом
сообщении», приходилось поднимать Telegram и CRM — и никто этого не делал. Теперь решение
проверяется без внешнего мира, а здесь остаётся тонкий слой, который делает ровно то, что
сказано в `Outcome`.

Импорты `tg_agent` — внутри функций: он импортирует этот модуль, и на верхнем уровне вышла бы
петля. Тот же приём уже используется в `terms_text`.
"""
from __future__ import annotations

import logging
import os
import time

import iu_contract
import iu_embeddings
import iu_filters
import iu_funnel
import iu_knowledge
import iu_turn

log = logging.getLogger("iu-runtime")

# Документ владельца с фактами ИУ. Правится на Google Drive, синк подхватывает изменения сам.
KNOWLEDGE_DOC_NAME = os.getenv(
    "IU_KNOWLEDGE_DOC", "Что отвечать лидам — вопросы и ответы").strip()

_CARDS_TTL_S = float(os.getenv("IU_CARDS_TTL_S", "300") or 300)
_cards_cache: dict = {"at": 0.0, "cards": ()}
# Векторы карточек переживают перечитывание документа: они кэшируются по хэшу содержимого,
# поэтому правка одной карточки не тянет пересчёт всего корпуса.
_vector_store: dict = {}


def knowledge_cards(force: bool = False) -> tuple:
    """Карточки фактов из документа владельца. Сбой чтения — пустой список, а не исключение.

    Пустой список означает, что агент честно передаст вопрос человеку. Это правильное
    поведение при недоступной базе: выдумывать ответ хуже, чем позвать людей."""
    now = time.time()
    if not force and now - float(_cards_cache["at"]) < _CARDS_TTL_S and _cards_cache["cards"]:
        return _cards_cache["cards"]
    try:
        from mcp import context_server as cs

        files = cs.TOOLS["list_company_files"]["handler"]({"limit": 300})
        wanted = KNOWLEDGE_DOC_NAME.casefold()
        match = next((f for f in (files.get("files") or files.get("items") or [])
                      if wanted in str(f.get("name") or "").casefold()
                      and f.get("google_file_id")), None)
        if not match:
            log.warning("документ знаний «%s» не найден", KNOWLEDGE_DOC_NAME)
            _cards_cache.update({"at": now, "cards": ()})
            return ()
        res = cs.TOOLS["get_company_file"]["handler"](
            {"google_file_id": match["google_file_id"]})
        raw = str(res.get("content") or res.get("text") or "")
        cards = iu_knowledge.parse_cards(raw)
    except Exception:  # noqa: BLE001 — без знаний агент зовёт людей, но не падает
        log.warning("документ знаний не прочитан", exc_info=True)
        return _cards_cache["cards"]

    approved = iu_knowledge.approved(cards)
    drafts = iu_knowledge.drafts(cards)
    log.info("знания ИУ: %d карточек готово, %d в черновике", len(approved), len(drafts))
    _cards_cache.update({"at": now, "cards": cards})
    return cards


def ruleset() -> iu_filters.Ruleset:
    """Фильтры: список запрещённых фраз владельца + встроенные категории."""
    import funnel_scenario

    try:
        raw = funnel_scenario.blocked_phrases()
    except Exception:  # noqa: BLE001 — без списка работают встроенные категории
        log.warning("список запрещённых фраз не прочитан", exc_info=True)
        raw = ""
    return iu_filters.Ruleset(phrases=iu_filters.parse_phrases(raw))


def semantic_scorer():
    """Смысловой поиск, если есть чем считать векторы. Иначе работает лексика."""
    if not iu_embeddings.available():
        return None
    return iu_embeddings.Index(store=_vector_store).rerank


def deal_facts(deal_id, telegram_id) -> iu_funnel.DealFacts:
    """Снимок проверяемых фактов сделки. Ничего из этого не выводится из слов клиента.

    `contract_signed` пока всегда False: поля «договор подписан» в воронке нет, и придумывать
    его нельзя — на этап «Договор подписан» сделку переводит человек, подтвердивший подписание."""
    import tg_agent

    terms_delivered = bool(tg_agent._terms_already_sent(telegram_id))
    if not deal_id:
        return iu_funnel.DealFacts(terms_delivered=terms_delivered)
    try:
        deal = tg_agent._deal_for_watch(int(deal_id)) or {}
    except Exception:  # noqa: BLE001 — CRM недоступна: работаем по тому, что знаем точно
        log.warning("сделка %s не прочитана", deal_id, exc_info=True)
        return iu_funnel.DealFacts(terms_delivered=terms_delivered)
    return iu_funnel.DealFacts(
        stage=str(deal.get("STAGE_ID") or "").strip(),
        terms_delivered=terms_delivered,
        form_filled=bool(tg_agent.anketa_block(deal)),
        contract_sent=bool(deal.get(tg_agent.CONTRACT_FILE_FIELD)),
    )


# Пауза перед повтором после сбоя провайдера модели: 500/503 обычно живут секунды.
MODEL_RETRY_PAUSE_S = float(os.getenv("TG_MODEL_RETRY_PAUSE_S", "8") or 8)


def _ask_with_retry(prompt: str, uid) -> str:
    """Один ход модели с одним повтором.

    Hermes зовётся подпроцессом, и провайдер регулярно отдаёт 500/503 на секунды. Без повтора
    каждый такой всплеск превращался бы в передачу человеку — клиент ждал бы живого менеджера
    там, где хватило бы восьми секунд паузы."""
    import tg_agent

    try:
        return tg_agent.customer_hermes_answer(prompt, f"tg-iu-{uid}")
    except Exception as exc:  # noqa: BLE001
        log.warning("ход по %s не удался (%s) — повтор через %.0f c",
                    uid, str(exc)[:160], MODEL_RETRY_PAUSE_S)
    time.sleep(MODEL_RETRY_PAUSE_S)
    return tg_agent.customer_hermes_answer(prompt, f"tg-iu-{uid}")


def about_our_product(outcome) -> bool:
    """Оказался ли разговор про наш продукт — по фактам хода, а не по regex поверх текста.

    Признак: нашлась карточка знаний либо агенту разрешили выслать условия или анкету. Раньше
    решение «заводить ли сделку» принимал реестр правил по словам клиента, и вопрос «сколько
    стоит доставка?» открывал сделку в воронке ИУ."""
    if outcome is None:
        return False
    return bool(
        outcome.sources
        or float(outcome.trace.get("retrieval") or 0) > 0
        or outcome.action in (iu_contract.SEND_TERMS, iu_contract.SEND_FORM)
    )


def stage_after_turn(deal_id, telegram_id, outcome) -> str:
    """Куда перевести только что заведённую сделку. Считается по фактам, уже ставшим правдой."""
    if outcome is None or not deal_id:
        return ""
    return iu_funnel.next_stage(deal_facts(deal_id, telegram_id))


def run_turn(author: dict, texts: list[str], deal_id: int | None = None):
    """Провести и исполнить один клиентский ход.

    Возвращает `(доставлено, решение хода)`: вызывающему коду нужен не только факт отправки, но
    и то, о чём был разговор — по нему решается, заводить ли сделку."""
    import tg_agent

    uid = author.get("id")
    message = "\n".join(t for t in texts if (t or "").strip())
    if not message:
        return False, None

    facts = deal_facts(deal_id, uid)
    request = iu_turn.Request(
        message=message,
        name=(author.get("first_name") or "").strip(),
        history=tg_agent.chat_history(tg_agent.MANAGER_CHANNEL, uid, texts),
        role=tg_agent.channel_role_prompt(tg_agent.MANAGER_CHANNEL),
        facts=facts,
    )
    deps = iu_turn.Deps(
        ask=lambda prompt: _ask_with_retry(prompt, uid),
        cards=knowledge_cards(),
        rules=ruleset(),
        rerank=semantic_scorer(),
    )

    outcome = iu_turn.handle(request, deps)
    return _execute(author, texts, deal_id, facts, outcome), outcome


def _execute(author: dict, texts: list[str], deal_id, facts, outcome) -> bool:
    """Исполнить решение хода: отправка, действие, эскалация, этап, журнал."""
    import tg_agent

    uid = author.get("id")
    message = "\n".join(texts)
    body = outcome.reply
    asset_sent = ""
    # Длинный документ, не влезающий в одно сообщение, уходит отдельным. Telegram режет по
    # лимиту молча, и склейка «ответ + условия» отдала бы клиенту оборванный документ.
    extra = ""

    # Действие исполняется ДО отправки текста: правило владельца «действие, потом слова».
    # Если действие не удалось, клиент не получит обещания того, чего не произошло.
    if outcome.action == iu_contract.SEND_TERMS:
        try:
            terms = tg_agent.terms_text()
        except Exception as exc:  # noqa: BLE001
            log.warning("документ условий недоступен: %s", str(exc)[:200])
            tg_agent.escalate_to_human(
                author, f"клиент спросил про условия, а документ не готов: {str(exc)[:150]}",
                message)
            return False
        joined = f"{body}\n\n{terms}"
        limit = tg_agent.TELEGRAM_SAFE_TEXT_LIMIT
        if len(joined) <= limit:
            body = joined
        elif len(terms) <= limit:
            extra = terms
        else:
            # Документ не помещается даже в отдельное сообщение. Резать его нельзя: клиент
            # получил бы оборванные условия и считал бы их полными. Отдаём людям.
            log.warning("документ условий длиннее лимита Telegram (%d) — клиенту не отправлен",
                        len(terms))
            tg_agent.escalate_to_human(
                author, "документ условий не помещается в сообщение Telegram — отправьте его "
                        "клиенту сами", message)
            return False
        asset_sent = "условия"
    elif outcome.action == iu_contract.SEND_FORM:
        # Кулдаун приглашения: одну и ту же ссылку нельзя слать каждый ход. Проверяем ДО
        # отправки, иначе анкета превращается в навязчивую рассылку.
        if not tg_agent.LEAD_FORM_URL:
            log.warning("анкета не отправлена: адрес формы не задан")
        elif tg_agent._invite_already_sent(uid):
            log.info("анкету %s уже приглашали недавно — второй раз не шлём", uid)
        else:
            tail = tg_agent.FORM_TAIL.format(url=tg_agent.LEAD_FORM_URL)
            if len(body) + len(tail) <= tg_agent.TELEGRAM_SAFE_TEXT_LIMIT:
                body = f"{body}{tail}"
            else:
                extra = tail
            asset_sent = "анкета"
    elif outcome.action == iu_contract.SEND_CONTRACT:
        try:
            tg_agent.contract_send(int(deal_id or 0), uid)
            asset_sent = "договор"
        except Exception as exc:  # noqa: BLE001
            log.warning("договор не собран: %s", str(exc)[:200])
            tg_agent.escalate_to_human(
                author, f"клиент просит договор, сборка не удалась: {str(exc)[:150]}", message)
            return False

    plain = tg_agent._strip_markup(body)
    ok, err = tg_agent.send_html(uid, tg_agent.as_html(body), plain)
    # Отметку «доставлено» ставим ТОЛЬКО реально дошедшему документу: иначе оборванная или
    # неотправленная отправка навсегда закрыла бы клиенту доступ к условиям и анкете.
    asset_ok = ok
    if ok and extra:
        asset_ok, extra_err = tg_agent.send_html(
            uid, tg_agent.as_html(extra), tg_agent._strip_markup(extra))
        if not asset_ok:
            log.warning("вторая часть (%s) не доставлена: %s", asset_sent, extra_err[:120])
            asset_sent = ""
    if asset_ok and outcome.action == iu_contract.SEND_TERMS:
        tg_agent._mark_terms_sent(uid)
    if asset_ok and outcome.action == iu_contract.SEND_FORM and asset_sent:
        tg_agent._mark_invited(uid)

    if outcome.escalate:
        tg_agent.escalate_to_human(author, outcome.reason or "нужен ответ человека", message,
                                   answered=not outcome.silent)

    # Этап двигаем только по фактам и только после доставки: «отправили» — это факт, а
    # «собирались отправить» — нет.
    move = outcome.stage_move
    if asset_ok and outcome.action == iu_contract.SEND_TERMS:
        after = iu_funnel.DealFacts(
            stage=facts.stage, terms_delivered=True, form_filled=facts.form_filled,
            contract_sent=facts.contract_sent, contract_signed=facts.contract_signed)
        move = iu_funnel.next_stage(after) or move
    if move and deal_id:
        tg_agent._move_deal_stage(deal_id, move, "Этап подтверждён фактами переписки.")

    for text in texts:
        tg_agent.journal(tg_agent.MANAGER_CHANNEL, uid, "in", text, kind="lead_chat",
                         user=author)
    tg_agent.journal(
        tg_agent.MANAGER_CHANNEL, uid, "out",
        plain if ok else f"{plain}\n\n[не доставлено: {err}]",
        kind="lead_chat", user=author, status="ok" if ok else "error",
        meta={"deal_id": deal_id, "escalated": bool(outcome.escalate),
              "action": outcome.action, "asset": asset_sent, **outcome.trace},
    )
    log.info("ход по %s: %s%s%s", uid, outcome.action,
             f" (+{asset_sent})" if asset_sent else "",
             " → людям" if outcome.escalate else "")
    return ok
