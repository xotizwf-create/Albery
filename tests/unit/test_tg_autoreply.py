"""Агент сам отвечает лидам в личке аккаунта компании.

Схема, подтверждённая на живом человеке 22.07.2026: лид пишет на @AlberyAIManager →
бизнес-подключение ловит сообщение → контакт записывается сам → агент отвечает ОТ ЛИЦА
аккаунта. Так закрывается воронка «Партнёрская программа WB» без участия менеджера.

Главная опасность — зацикливание: исходящие самого аккаунта приходят тем же апдейтом, и без
фильтра агент отвечал бы сам себе бесконечно.
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
    # Отвечаем только лидам воронки — здесь проверяется сам механизм автоответа, поэтому
    # собеседник считается лидом. Сам белый список проверяется в test_tg_lead_whitelist.
    monkeypatch.setattr(tg_agent, "crm_lead_usernames", lambda force=False: {"lead": 82})
    monkeypatch.setattr(
        tg_agent,
        "_deal_for_watch",
        lambda deal_id: {
            "id": deal_id,
            "stage_id": "C16:S84294149",
            "custom_fields": {},
        },
    )
    # Пауза-добор пачки в тестах — символическая, иначе каждый тест ждал бы живые секунды.
    monkeypatch.setattr(tg_agent, "_REPLY_DEBOUNCE_S", 0.02)
    return tg_agent


def _incoming(uid=1451982360, text="Здравствуйте, интересуют условия", **kw):
    msg = {"business_connection_id": "C1", "chat": {"id": uid, "type": "private"},
           "from": {"id": uid, "username": "lead", "first_name": "Дмитрий"}, "text": text}
    msg.update(kw)
    return msg








def test_crm_form_status_outage_suppresses_the_form_fail_closed(tg, monkeypatch):
    sent, invited = [], []
    monkeypatch.setattr(tg, "hermes_answer",
                        lambda p, s, toolsets=None: "Помогу подключиться.")
    monkeypatch.setattr(tg, "_deal_has_form", lambda deal_id: None)
    monkeypatch.setattr(tg, "_mark_invited", lambda uid: invited.append(uid))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)
    monkeypatch.setattr(
        tg, "send_as_account",
        lambda uid, text, parse_mode="": sent.append(text) or (True, ""),
    )

    tg.maybe_autoreply(_incoming(text="Хочу подключиться к ИУ"))

    assert sent and all(tg.LEAD_FORM_URL not in text for text in sent)
    assert invited == []


def test_own_outgoing_messages_never_trigger_a_reply(tg, monkeypatch):
    """Самое опасное: иначе агент отвечает сам себе по кругу."""
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (True, ""))

    tg.maybe_autoreply(_incoming(uid=8715335144))  # id самого владельца аккаунта

    assert calls == [], "на своё же исходящее отвечать нельзя"


def test_bots_are_ignored(tg, monkeypatch):
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")

    msg = _incoming()
    msg["from"]["is_bot"] = True
    tg.maybe_autoreply(msg)

    assert calls == []


def test_group_chats_are_ignored(tg, monkeypatch):
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")

    tg.maybe_autoreply(_incoming(chat={"id": -100, "type": "group"}))

    assert calls == []


def test_empty_message_is_ignored(tg, monkeypatch):
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: calls.append(1) or "ответ")

    tg.maybe_autoreply(_incoming(text="   "))

    assert calls == []


def test_autoreply_is_off_by_default(tg, monkeypatch):
    monkeypatch.delenv("TG_BUSINESS_AUTOREPLY", raising=False)
    assert tg.business_autoreply_enabled() is False

    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    assert tg.business_autoreply_enabled() is True


def test_incoming_still_registers_contact_when_autoreply_off(tg, monkeypatch):
    """Справочник должен пополняться независимо от автоответа."""
    monkeypatch.delenv("TG_BUSINESS_AUTOREPLY", raising=False)

    tg.handle_business_message(_incoming(uid=777))

    assert tg.find_contact("777")["id"] == 777


def test_brain_failure_does_not_crash_the_pipeline(tg, monkeypatch):
    def boom(p, s, toolsets=None):
        raise RuntimeError("мозг недоступен")

    monkeypatch.setattr(tg, "hermes_answer", boom)
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (True, ""))

    tg.maybe_autoreply(_incoming())  # не должно бросить исключение


def test_answer_is_sent_as_plain_text(tg, monkeypatch):
    """В мессенджере разметка выглядит мусором."""
    sent = {}
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s, toolsets=None: "**Жирный** текст")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (sent.update(text=t), (True, ""))[1])

    tg.maybe_autoreply(_incoming())

    assert "**" not in sent["text"]


def _shared_journal(tg, monkeypatch):
    """Общий журнал на оба процесса: инструмент (в MCP приложения) и tg-агент видят одно и то же.

    В телах модели-заглушки инструмент имитируется записью 'out' в этот же журнал — ровно как
    send_terms/send_contract пишут в telegram_bot_messages в соседнем процессе."""
    ledger: list[dict] = []

    def fake_journal(bot, dialog_id, direction, text, **kw):
        ledger.append({"id": len(ledger) + 1, "dialog_id": str(dialog_id),
                       "direction": direction, "status": kw.get("status", "ok"),
                       "meta": kw.get("meta") or {}})

    def watermark(dialog_id):
        ids = [r["id"] for r in ledger
               if r["dialog_id"] == str(dialog_id) and r["direction"] == "out"]
        return max(ids) if ids else 0

    def out_after(dialog_id, since_id):
        if since_id < 0:
            return 0
        return sum(1 for r in ledger
                   if r["dialog_id"] == str(dialog_id) and r["direction"] == "out"
                   and r["status"] == "ok" and r["id"] > since_id
                   and str(r["meta"].get("escalated") or "") != "true")

    monkeypatch.setattr(tg, "journal", fake_journal)
    monkeypatch.setattr(tg, "_dialog_out_watermark", watermark)
    monkeypatch.setattr(tg, "_out_messages_after", out_after)
    return ledger






# --- пачка сообщений подряд = один человечный ответ (владелец, 23.07.2026) --------------------



def test_debounce_window_slides_from_the_last_message(tg, monkeypatch):
    """Владелец 24.07.2026: отсчёт паузы — от ПОСЛЕДНЕГО сообщения. Клиент пишет с паузами
    меньше окна дольше самого окна — агент молчит и потом отвечает на всё разом."""
    import threading
    import time as _time

    prompts = []
    monkeypatch.setattr(tg, "_REPLY_DEBOUNCE_S", 0.1)
    monkeypatch.setattr(tg, "hermes_answer",
                        lambda p, s, toolsets=None: prompts.append(p) or "ок")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (True, ""))

    threads = []
    for txt in ("раз", "два", "три"):
        threads.append(threading.Thread(target=tg.maybe_autoreply, args=(_incoming(text=txt),)))
        threads[-1].start()
        _time.sleep(0.07)      # паузы меньше окна, суммарно (0.14) дольше окна (0.1)
    for t in threads:
        t.join(timeout=5)

    assert len(prompts) == 1, "окно должно сдвигаться каждым сообщением — ход один"
    assert all(w in prompts[0] for w in ("раз", "два", "три"))


def test_message_after_the_turn_starts_goes_to_the_next_turn(tg, monkeypatch):
    """Сообщение, пришедшее когда пачка уже забрана, не теряется — его берёт следующий ход."""
    import threading
    import time as _time

    prompts = []

    def slow_brain(p, s, toolsets=None):
        prompts.append(p)
        _time.sleep(0.2)       # ход «думает» — в это время клиент пишет ещё
        return "ответ"

    monkeypatch.setattr(tg, "_REPLY_DEBOUNCE_S", 0.02)
    monkeypatch.setattr(tg, "hermes_answer", slow_brain)
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (True, ""))

    t1 = threading.Thread(target=tg.maybe_autoreply, args=(_incoming(text="первое"),))
    t1.start()
    _time.sleep(0.12)          # пауза-добор прошла, ход уже думает
    t2 = threading.Thread(target=tg.maybe_autoreply, args=(_incoming(text="второе"),))
    t2.start()
    t1.join(timeout=5); t2.join(timeout=5)

    assert len(prompts) == 2, "второе сообщение обязано получить свой ход"
    assert "первое" in prompts[0] and "второе" in prompts[1]








def test_fresh_deal_is_picked_up_without_waiting_for_cache(tg, monkeypatch):
    """Клиент заполнил анкету — сделка появилась сию секунду, а кэш лидов живёт 5 минут.
    23.07.2026 агент из-за этого говорил с готовым лидом как с незнакомцем. При промахе и
    несвежем кэше список обязан перечитаться из CRM немедленно."""
    import time as _time

    calls = []

    def fake_leads(force=False):
        calls.append(force)
        return {"newlead": 95} if force else {}

    monkeypatch.setattr(tg, "crm_lead_usernames", fake_leads)
    monkeypatch.setitem(tg._LEADS_CACHE, "at", _time.time() - 120)   # кэш старше минуты

    assert tg.lead_deal_for_username("newlead") == 95
    assert True in calls, "при промахе обязан быть force-перечит CRM"


def test_stranger_incoming_is_journaled_even_when_narration_suppressed(tg, monkeypatch):
    """23.07.2026: инструмент отправил условия, реплика модели погашена — и сообщение клиента
    ИСЧЕЗЛО из журнала: в кабинете условия выглядели отправленными «из ниоткуда»."""
    ledger = _shared_journal(tg, monkeypatch)
    monkeypatch.setattr(tg, "crm_lead_usernames", lambda force=False: {})   # в воронке нет
    monkeypatch.setattr(tg, "crm_leads_reachable", lambda: True)
    monkeypatch.setenv("TG_LEAD_INVITE", "1")
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (True, ""))

    def brain(prompt, session, toolsets=None):
        tg.journal(tg.MANAGER_CHANNEL, 1451982360, "out", "Условия дословно… Есть вопросы?",
                   meta={"terms": True})
        return "Условия отправили вам сюда."

    monkeypatch.setattr(tg, "hermes_answer", brain)
    tg.maybe_autoreply(_incoming(text="Да, всё верно, присылайте"))

    ins = [r for r in ledger if r["direction"] == "in"]
    assert ins, "входящее клиента обязано остаться в журнале, даже когда реплика погашена"
