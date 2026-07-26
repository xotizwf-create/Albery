"""Разговор с незнакомцем в личке аккаунта компании.

Требование владельца: агент должен вести себя как живой консультант. Здороваются — здоровается
в ответ. Спрашивают — сначала отвечает по утверждённым данным. Ответа нет — не выдумывает, а
передаёт вопрос менеджеру. Анкета появляется только после явной готовности клиента, а не как
обязательная подпись под первым сообщением.

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
    """Перехват сообщений в группу Битрикса «Работа с ИУ».

    Ловим только notify_iu_group: через mcp_call идут и другие инструменты (напр.
    add_deal_comment — зеркалирование переписки в ленту сделки), и им тут не место."""
    box = []

    def fake(tool, args):
        if tool == "notify_iu_group":
            box.append({"tool": tool, **args})
        return {"sent": True, "message_id": 27698}

    monkeypatch.setattr(tg, "mcp_call", fake)
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






def test_false_terms_marker_on_off_topic_sends_neither_document_nor_form(
        tg, sent, monkeypatch):
    _brain(tg, monkeypatch, tg.TERMS_REQUEST_MARKER)
    monkeypatch.setattr(
        tg, "terms_text",
        lambda: (_ for _ in ()).throw(AssertionError("документ ИУ здесь открывать нельзя")),
    )

    tg.maybe_autoreply(_msg(text="Сколько стоит доставка документов?"))

    assert sent, "ошибка классификации модели не должна оставлять человека без ответа"
    assert "30 000 ₽" not in sent[0]["text"]
    assert tg.LEAD_FORM_URL not in sent[0]["text"]




















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






def test_escalation_card_is_short_without_the_chat_dump(tg, sent, to_group, monkeypatch):
    """Владелец 24.07.2026: карточки с простынёй «О чём говорили» превратились во флуд —
    полезна только верхняя часть. Переписку агент группы достаёт сам (get_telegram_dialog,
    подключён 23.07.2026), поэтому в карточке остаются: вопрос, клиент, чего нет в базе,
    и как ответить."""
    _brain(tg, monkeypatch, "НУЖЕН_ЧЕЛОВЕК: что отвечать про договор")
    _journal_rows(tg, monkeypatch, [("in", "ИНН 7704123456, ООО «Альфа Трейд»"),
                                    ("out", "Реквизиты получил")])

    tg.maybe_autoreply(_msg(text="Вы пришлёте мне договор сюда?"))

    card = to_group[0]["text"]
    assert "О чём говорили в чате с клиентом" not in card, "простыня переписки — флуд"
    assert "Вы пришлёте мне договор сюда?" in card, "суть вопроса остаётся"
    assert "Скажите мне здесь" in card, "инструкция, как ответить клиенту, остаётся"
    assert "———" not in card








def test_bitrix_report_format_is_not_pushed_into_the_chat(tg, sent, monkeypatch):
    """Универсальные инструкции написаны под отчёты в Битриксе и несут BB-коды; в Telegram
    они дошли бы до клиента мусором (жалоба владельца 14.07.2026)."""
    tg._INSTR_CACHE.update({"at": 0.0, "text": ""})
    seen = _brain(tg, monkeypatch)

    tg.maybe_autoreply(_msg(text="Здравствуйте"))

    assert "Стандартный формат ответа" not in seen[0]




def _journal_rows(tg, monkeypatch, rows):
    """Подменяем чтение журнала переписки — историю агент берёт оттуда."""
    monkeypatch.setattr(tg, "chat_history",
                        lambda bot, dialog_id, current_text="", limit=12:
                        "\n".join(f"{'Клиент' if d == 'in' else 'Ты'}: {t}" for d, t in rows))






def test_current_message_is_not_shown_twice(tg, sent, monkeypatch):
    """Входящее уже попало в журнал: в истории оно выглядело бы как повтор клиента."""
    seen = _brain(tg, monkeypatch)
    monkeypatch.setattr(tg, "_db", lambda: (_ for _ in ()).throw(RuntimeError("нет БД")))

    tg.maybe_autoreply(_msg(text="привет"))

    assert "О чём вы уже говорили" not in seen[0], "пустая история не должна попадать в промпт"




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
    _brain(tg, monkeypatch, "Помогу подключиться.")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (False, "сеть"))

    tg.maybe_autoreply(_msg(text="Хочу подключиться к ИУ"))

    assert tg._invite_already_sent(999) is False




def test_reply_is_plain_text_without_markup(tg, sent, monkeypatch):
    """HTML-режим сломался бы на любом < или & из ответа модели — шлём чистый текст."""
    _brain(tg, monkeypatch, "**Здравствуйте!** Чем помочь?")

    tg.maybe_autoreply(_msg())

    assert "**" not in sent[0]["text"]


# --- разбор сбоя 24.07.2026 (живой клиент 5195962532) ----------------------------------------





def test_model_form_link_is_removed_without_customer_consent(tg, sent, monkeypatch):
    _brain(tg, monkeypatch,
           "Здравствуйте! [Заполнить анкету](https://b24-9qcm4m.bitrix24site.ru/)")

    tg.maybe_autoreply(_msg(text="Здравствуйте"))

    assert "b24-9qcm4m.bitrix24site.ru" not in sent[0]["text"]
    assert "анкет" not in sent[0]["text"].lower()




@pytest.mark.parametrize(
    "model_answer",
    [
        "Вы можете подключиться и заполнить анкету.",
        "Для подключения можно использовать анкету.",
        "Начать можно с анкеты.",
        "Доступна анкета для подключения.",
    ],
)
def test_model_form_offer_bypasses_are_removed_on_a_greeting(
        tg, sent, monkeypatch, model_answer):
    _brain(tg, monkeypatch, model_answer)

    tg.maybe_autoreply(_msg(text="Здравствуйте"))

    assert sent
    assert "анкет" not in sent[0]["text"].lower()
    assert tg.LEAD_FORM_URL not in sent[0]["text"]




def test_openline_service_messages_are_not_treated_as_client_words(tg, sent, monkeypatch):
    """Автоответы Открытой линии (Wazzup) прилетают в тот же чат. 24.07.2026 агент «услышал»
    от клиента «Добро пожаловать в Открытую линию» и отвечал роботу соседнего канала."""
    seen = _brain(tg, monkeypatch, "ответ")

    tg.maybe_autoreply(_msg(username="griaznov.d", uid=555,
                            text="Добро пожаловать в Открытую линию компании !\n"
                                 "Вам ответит первый освободившийся оператор."))

    assert seen == [], "на служебное сообщение чужого канала ход не запускается"
    assert sent == []


def test_openline_noise_is_recognised(tg):
    assert tg._is_openline_noise("Здравствуйте! Спасибо, что написали. Мы скоро ответим.")
    assert tg._is_openline_noise("Добро пожаловать в Открытую линию компании !")
    assert not tg._is_openline_noise("Здравствуйте, какие условия подключения к ИУ?")
