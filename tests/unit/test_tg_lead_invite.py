"""Разговор с незнакомцем в личке аккаунта компании.

Требование владельца 22.07.2026: агент должен вести себя как живой человек. Здороваются —
здоровается в ответ. Спрашивают — отвечает по базе знаний воронки (папка Google Drive «База
знаний — Партнёрская программа WB»). Ответа в базе нет — НЕ выдумывает, а передаёт вопрос
живому менеджеру. Плюс при первом контакте даёт ссылку на анкету, чтобы человек стал лидом.

Две главные опасности:
1. выдуманные условия и цены — клиенту пообещают то, чего компания не даёт;
2. спам анкетой — без дедупликации она уходила бы в каждом сообщении.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def tg(monkeypatch, tmp_path):
    import tg_agent

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"business": {"C1": {"user_id": 8715335144}}}), encoding="utf-8")
    monkeypatch.setattr(tg_agent, "STATE_PATH", state_file)
    monkeypatch.setattr(tg_agent, "BUSINESS_LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(tg_agent, "load_state", lambda: json.loads(state_file.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg_agent, "save_state",
                        lambda s: state_file.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8"))
    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    monkeypatch.setenv("TG_LEAD_INVITE", "1")
    # CRM доступна, в воронке один лид — все остальные считаются незнакомцами.
    tg_agent._LEADS_CACHE.update({"at": 0.0, "map": {}, "ok": True})
    monkeypatch.setattr(tg_agent, "crm_lead_usernames", lambda force=False: {"griaznov.d": 82})
    return tg_agent


@pytest.fixture
def sent(tg, monkeypatch):
    box = []
    monkeypatch.setattr(tg, "send_as_account",
                        lambda uid, text, parse_mode="": box.append(
                            {"uid": uid, "text": text, "mode": parse_mode}) or (True, ""))
    return box


@pytest.fixture
def to_group(tg, monkeypatch):
    """Перехват сообщений в группу Битрикса «Работа с ИУ»."""
    box = []
    monkeypatch.setattr(tg, "mcp_call",
                        lambda tool, args: box.append({"tool": tool, **args})
                        or {"sent": True, "message_id": 27698})
    return box


@pytest.fixture
def to_human(tg, monkeypatch):
    """Перехват запасного канала — личка владельца в Telegram."""
    box = []
    monkeypatch.setattr(tg, "api", lambda method, **p: box.append(p) or {"message_id": 1})
    monkeypatch.setenv("TG_ESCALATION_CHAT_ID", "8715335144")
    return box


def _brain(tg, monkeypatch, answer="Здравствуйте! Чем могу помочь?"):
    seen = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: seen.append(p) or answer)
    return seen


def _msg(username="ivan_novy", uid=999, text="Здравствуйте", **kw):
    msg = {"business_connection_id": "C1", "chat": {"id": uid, "type": "private"},
           "from": {"id": uid, "username": username, "first_name": "Иван"}, "text": text}
    msg.update(kw)
    return msg


def test_greeting_gets_a_human_greeting_back(tg, sent, monkeypatch):
    _brain(tg, monkeypatch, "Здравствуйте! Чем могу помочь?")

    tg.maybe_autoreply(_msg(text="Здравствуйте"))

    assert len(sent) == 1
    assert "Чем могу помочь" in sent[0]["text"]


def test_first_reply_carries_the_form_link(tg, sent, monkeypatch):
    _brain(tg, monkeypatch)

    tg.maybe_autoreply(_msg())

    assert tg.LEAD_FORM_URL in sent[0]["text"]


def test_form_link_is_offered_only_once(tg, sent, monkeypatch):
    """Анкета — приглашение, а не подпись под каждым сообщением."""
    _brain(tg, monkeypatch)

    tg.maybe_autoreply(_msg(text="Здравствуйте"))
    tg.maybe_autoreply(_msg(text="а сколько стоит?"))
    tg.maybe_autoreply(_msg(text="ну что там?"))

    assert len(sent) == 3, "разговор продолжается"
    assert sum(tg.LEAD_FORM_URL in m["text"] for m in sent) == 1


def test_agent_is_told_to_use_the_knowledge_base(tg, sent, monkeypatch):
    seen = _brain(tg, monkeypatch)

    tg.maybe_autoreply(_msg(text="какие у вас условия?"))

    assert "search_company_knowledge" in seen[0]
    assert "Партнёрская программа WB" in seen[0]


def test_unknown_question_goes_to_the_iu_group(tg, sent, to_group, monkeypatch):
    """Сердце требования: не знаешь — спроси людей в группе, а не сочиняй условия."""
    _brain(tg, monkeypatch, "НУЖЕН_ЧЕЛОВЕК: спрашивает про комиссию для маркетплейса")

    tg.maybe_autoreply(_msg(text="Какая у вас комиссия?"))

    assert len(to_group) == 1, "вопрос должен уйти в группу «Работа с ИУ»"
    assert to_group[0]["tool"] == "notify_iu_group"
    card = to_group[0]["text"]
    assert "Пользователь задал вопрос:" in card
    assert "что мне на него ответить" in card.lower()
    assert "Какая у вас комиссия?" in card, "людям нужен исходный текст клиента"
    assert "@ivan_novy" in card and "999" in card, "без telegram id ответ передать некому"
    assert "ответь, что" in card, "в группе должно быть видно, как ответить агенту"


def test_group_failure_falls_back_to_telegram(tg, sent, to_human, monkeypatch):
    """Сбой Битрикса не должен проглотить вопрос клиента."""
    def boom(tool, args):
        raise RuntimeError("Битрикс недоступен")

    monkeypatch.setattr(tg, "mcp_call", boom)
    _brain(tg, monkeypatch, "НУЖЕН_ЧЕЛОВЕК: спрашивает про сроки")

    tg.maybe_autoreply(_msg(text="Как быстро подключите?"))

    assert len(to_human) == 1, "вопрос должен уйти хотя бы в запасной канал"
    assert "Как быстро подключите?" in to_human[0]["text"]


def test_group_answering_without_message_id_is_not_trusted(tg, sent, to_human, monkeypatch):
    """Ответ без id сообщения — не доказательство доставки: уходим в запасной канал."""
    monkeypatch.setattr(tg, "mcp_call", lambda tool, args: {"sent": False})
    _brain(tg, monkeypatch, "НУЖЕН_ЧЕЛОВЕК: вопрос")

    tg.maybe_autoreply(_msg(text="вопрос"))

    assert len(to_human) == 1


def test_client_link_is_the_public_site(tg):
    """Владелец 22.07.2026: клиентам уходит только сайт компании, внутренние адреса портала — нет."""
    assert tg.LEAD_FORM_URL.startswith("https://b24-9qcm4m.bitrix24site.ru")
    assert "/pub/form/" not in tg.LEAD_FORM_URL
    assert "/crm/" not in tg.LEAD_FORM_URL


def test_form_invite_is_a_clickable_link(tg, sent, monkeypatch):
    """В Битриксе ссылка приходит подписью [URL=…]…[/URL]; в Telegram должно быть так же."""
    _brain(tg, monkeypatch)

    tg.maybe_autoreply(_msg(text="Здравствуйте"))

    assert sent[0]["mode"] == "HTML", "без parse_mode ссылка останется голым адресом"
    assert f'<a href="{tg.LEAD_FORM_URL}">Заполнить анкету</a>' in sent[0]["text"]


def test_model_links_become_clickable_too(tg):
    """Мозг пишет ссылки по-человечески — [подпись](адрес); клиент должен получить подпись."""
    out = tg.as_html("Смотрите [условия работы](https://example.com/x) — там всё есть")

    assert '<a href="https://example.com/x">условия работы</a>' in out


def test_stray_angle_brackets_do_not_break_the_message(tg):
    """Любой < или & из ответа мозга иначе сломал бы HTML-режим, и клиент не получил бы ничего."""
    out = tg.as_html("оборот < 5 млн & растёт")

    assert "&lt; 5 млн &amp; растёт" in out


def test_broken_markup_still_reaches_the_client(tg, monkeypatch):
    """Разметка косметическая: молчание из-за неудачного символа хуже сообщения без ссылки."""
    tries = []

    def flaky(uid, text, parse_mode=""):
        tries.append({"text": text, "mode": parse_mode})
        return (False, "can't parse entities") if parse_mode else (True, "")

    monkeypatch.setattr(tg, "send_as_account", flaky)
    ok, _ = tg.send_html(999, "<a href='x'>битая</a>", "обычный текст")

    assert ok and len(tries) == 2
    assert tries[1]["mode"] == "" and tries[1]["text"] == "обычный текст"


def test_client_gets_nothing_while_the_question_goes_to_people(tg, sent, to_group, monkeypatch):
    """Требование владельца 22.07.2026: никаких «уточню у коллег и вернусь».

    Отписка обещает ответ, которого у агента нет, и клиент считает минуты. Правильно —
    промолчать в чате и немедленно принести вопрос людям."""
    _brain(tg, monkeypatch, "НУЖЕН_ЧЕЛОВЕК: спрашивает про сроки")

    tg.maybe_autoreply(_msg(text="Как быстро подключите?"))

    assert sent == [], "клиенту не должно уйти ничего — ни отписки, ни анкеты"
    assert len(to_group) == 1, "вопрос обязан уйти людям в тот же момент"


def test_lead_question_also_goes_to_people_silently(tg, sent, to_group, monkeypatch):
    """Дыра до 22.07.2026: маркер обрабатывался только у незнакомцев, и ЛИД воронки
    получал служебную строку «НУЖЕН_ЧЕЛОВЕК: …» прямо в чат."""
    _brain(tg, monkeypatch, "НУЖЕН_ЧЕЛОВЕК: спрашивает про комиссию")

    tg.maybe_autoreply(_msg(username="griaznov.d", uid=555, text="Какая комиссия?"))

    assert sent == [], "лид тем более не должен видеть служебный маркер"
    assert len(to_group) == 1 and "Какая комиссия?" in to_group[0]["text"]


def test_escalation_card_says_the_client_is_still_waiting(tg, sent, to_group, monkeypatch):
    """Сотрудник должен с первой строки понять, что человек сидит без ответа."""
    _brain(tg, monkeypatch, "НУЖЕН_ЧЕЛОВЕК: спрашивает про сроки")

    tg.maybe_autoreply(_msg(text="Как быстро подключите?"))

    card = to_group[0]["text"]
    assert "ждёт ответа" in card and "НИЧЕГО не отвечено" in card
    assert "уже отвечено" not in card, "старое обещание клиенту снято — не вводим людей в заблуждение"


def test_escalation_failure_still_leaves_the_client_unanswered(tg, sent, monkeypatch):
    """Сбой доставки не повод выдумывать ответ клиенту: он ждёт человека, а не отписку."""
    monkeypatch.setattr(tg, "api", lambda method, **p: (_ for _ in ()).throw(RuntimeError("нет сети")))
    monkeypatch.setenv("TG_ESCALATION_CHAT_ID", "8715335144")
    _brain(tg, monkeypatch, "НУЖЕН_ЧЕЛОВЕК: что-то непонятное")

    tg.maybe_autoreply(_msg(text="вопрос"))

    assert sent == []


def test_formatting_rules_are_delivered_to_the_agent(tg, sent, monkeypatch):
    """Оформление подключено инструкцией в кабинете и обязано доходить до модели.

    Через start_here надеяться нельзя: у агента один ход на ответ клиенту, и «забыл спросить»
    означает слипшееся сообщение."""
    tg._INSTR_CACHE.update({"at": 0.0, "text": ""})
    seen = _brain(tg, monkeypatch)

    tg.maybe_autoreply(_msg(text="Здравствуйте"))

    assert "Оформление сообщений клиенту в Telegram" in seen[0]
    assert "ПУСТАЯ строка" in seen[0], "правило про воздух между блоками должно дойти дословно"
    assert "Анкета — не пропуск в разговор" in seen[0], "правила общения тоже обязаны дойти"
    # К агенту подключены и объёмные инструкции по работе в системе: разговорные обязаны
    # стоять раньше них, иначе лимит промпта обрежет именно их.
    block = seen[0].split("ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ОФОРМЛЕНИЯ", 1)[-1]
    assert block.lstrip().startswith("— подключены владельцем, следуй им буквально:\n# Общение в переписке")


def test_bitrix_report_format_is_not_pushed_into_the_chat(tg, sent, monkeypatch):
    """Универсальные инструкции написаны под отчёты в Битриксе и несут BB-коды; в Telegram
    они дошли бы до клиента мусором (жалоба владельца 14.07.2026)."""
    tg._INSTR_CACHE.update({"at": 0.0, "text": ""})
    seen = _brain(tg, monkeypatch)

    tg.maybe_autoreply(_msg(text="Здравствуйте"))

    assert "Стандартный формат ответа" not in seen[0]


def test_answer_and_side_question_go_together(tg, sent, to_group, monkeypatch):
    """Есть что ответить по существу, но нет конкретики: клиент получает ответ, люди — вопрос.

    Молчать здесь нельзя — новый лид остался бы совсем без ответа (владелец 22.07.2026)."""
    _brain(tg, monkeypatch, "Расскажу, как всё устроено: сначала смотрим категорию\n"
                            "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ: какая комиссия и сроки подключения")

    tg.maybe_autoreply(_msg(text="Что за ИУ и сколько стоит?"))

    assert len(sent) == 1, "клиент должен получить ответ по существу"
    assert "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ" not in sent[0]["text"], "служебная строка клиенту не уходит"
    assert "сначала смотрим категорию" in sent[0]["text"]
    assert len(to_group) == 1 and "комиссия и сроки" in to_group[0]["text"]
    assert "Клиенту отвечено по существу" in to_group[0]["text"], \
        "сотрудник должен понимать, что человек не сидит в тишине"


def _journal_rows(tg, monkeypatch, rows):
    """Подменяем чтение журнала переписки — историю агент берёт оттуда."""
    monkeypatch.setattr(tg, "chat_history",
                        lambda bot, dialog_id, current_text="", limit=12:
                        "\n".join(f"{'Клиент' if d == 'in' else 'Ты'}: {t}" for d, t in rows))


def test_agent_sees_what_was_already_said(tg, sent, monkeypatch):
    """Жалоба владельца 22.07.2026: агент поздоровался ВТОРОЙ раз, будто видит человека впервые.

    Причина — каждый ход был чистым листом: в промпт уходило только последнее сообщение."""
    seen = _brain(tg, monkeypatch)
    _journal_rows(tg, monkeypatch, [("in", "Здравствуйте!"),
                                    ("out", "Здравствуйте! Какой у вас вопрос?")])

    tg.maybe_autoreply(_msg(text="Расскажите про Вашу систему ИУ, хочу подключить"))

    assert "О чём вы уже говорили в этом чате:" in seen[0]
    assert "Ты: Здравствуйте! Какой у вас вопрос?" in seen[0], "агент должен видеть, что уже здоровался"


def test_current_message_is_not_shown_twice(tg, sent, monkeypatch):
    """Входящее уже попало в журнал: в истории оно выглядело бы как повтор клиента."""
    seen = _brain(tg, monkeypatch)
    monkeypatch.setattr(tg, "_db", lambda: (_ for _ in ()).throw(RuntimeError("нет БД")))

    tg.maybe_autoreply(_msg(text="привет"))

    assert "О чём вы уже говорили" not in seen[0], "пустая история не должна попадать в промпт"


def test_lead_is_answered_as_a_lead_not_as_a_stranger(tg, sent, monkeypatch):
    """Тому, кто анкету заполнил, предлагать её снова — оскорбительно."""
    seen = _brain(tg, monkeypatch, "Здравствуйте! Уточните ваш оборот.")

    tg.maybe_autoreply(_msg(username="griaznov.d", uid=555, text="привет"))

    assert tg.LEAD_FORM_URL not in sent[0]["text"]
    assert "№82" in seen[0], "агент должен знать номер сделки"


def test_conversation_can_be_switched_off(tg, sent, monkeypatch):
    monkeypatch.delenv("TG_LEAD_INVITE", raising=False)
    _brain(tg, monkeypatch)

    tg.maybe_autoreply(_msg())

    assert sent == []


def test_owner_own_messages_are_never_answered(tg, sent, monkeypatch):
    """Исходящие самого аккаунта приходят тем же апдейтом — иначе агент зациклится на себе."""
    _brain(tg, monkeypatch)

    tg.maybe_autoreply(_msg(uid=8715335144, username="alberyaimanager"))

    assert sent == []


def test_bots_and_groups_get_nothing(tg, sent, monkeypatch):
    _brain(tg, monkeypatch)
    bot = _msg(uid=777)
    bot["from"]["is_bot"] = True

    tg.maybe_autoreply(bot)
    tg.maybe_autoreply(_msg(uid=778, chat={"id": -100, "type": "group"}))

    assert sent == []


def test_undelivered_reply_does_not_burn_the_invite(tg, monkeypatch):
    """Сетевая ошибка не должна молча лишить человека анкеты навсегда."""
    _brain(tg, monkeypatch)
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (False, "сеть"))

    tg.maybe_autoreply(_msg())

    assert tg._invite_already_sent(999) is False


def test_brain_failure_leaves_no_reply_and_no_crash(tg, sent, monkeypatch):
    def boom(p, s, toolsets=None):
        raise RuntimeError("мозг недоступен")

    monkeypatch.setattr(tg, "hermes_answer", boom)

    tg.maybe_autoreply(_msg())      # не должно бросить исключение

    assert sent == []


def test_reply_is_plain_text_without_markup(tg, sent, monkeypatch):
    """HTML-режим сломался бы на любом < или & из ответа модели — шлём чистый текст."""
    _brain(tg, monkeypatch, "**Здравствуйте!** Чем помочь?")

    tg.maybe_autoreply(_msg())

    assert "**" not in sent[0]["text"]
