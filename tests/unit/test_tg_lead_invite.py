"""Незнакомцу в личке аккаунта агент вручает анкету.

Требование владельца 22.07.2026: пишет человек, которого нет в воронке («хочу подключить
систему» или что угодно своё) — аккаунт сам отправляет приветствие со ссылкой на CRM-форму и
просит вернуться в чат. Заполнив анкету, человек создаёт сделку, его username попадает в белый
список — и дальше его ведёт агент как лида.

Главная опасность — спам: без дедупликации приглашение уходило бы на КАЖДОЕ сообщение
незнакомца, и аккаунт компании выглядел бы как автоответчик, сорвавшийся с цепи.
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
                            {"uid": uid, "text": text, "parse_mode": parse_mode}) or (True, ""))
    return box


def _msg(username="ivan_novy", uid=999, text="Здравствуйте, хочу подключить систему", **kw):
    msg = {"business_connection_id": "C1", "chat": {"id": uid, "type": "private"},
           "from": {"id": uid, "username": username, "first_name": "Иван"}, "text": text}
    msg.update(kw)
    return msg


def test_stranger_receives_the_form_link(tg, sent):
    tg.maybe_autoreply(_msg())

    assert len(sent) == 1
    assert sent[0]["uid"] == 999
    assert tg.LEAD_FORM_URL in sent[0]["text"], "без ссылки на анкету сообщение бесполезно"


def test_invite_is_sent_only_once(tg, sent):
    """Иначе на каждое сообщение незнакомца летит одна и та же простыня."""
    tg.maybe_autoreply(_msg())
    tg.maybe_autoreply(_msg(text="а сколько стоит?"))
    tg.maybe_autoreply(_msg(text="ну что там?"))

    assert len(sent) == 1


def test_invite_asks_to_come_back_to_the_chat(tg, sent):
    """Без возврата в чат сделка не свяжется с перепиской."""
    tg.maybe_autoreply(_msg())

    assert "верн" in sent[0]["text"].lower()


def test_lead_gets_a_real_answer_not_the_invite(tg, sent, monkeypatch):
    """Тому, кто анкету уже заполнил, предлагать её снова — оскорбительно."""
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: "Здравствуйте! Уточните ваш оборот.")

    tg.maybe_autoreply(_msg(username="griaznov.d", uid=555))

    assert len(sent) == 1
    assert tg.LEAD_FORM_URL not in sent[0]["text"]
    assert "оборот" in sent[0]["text"]


def test_brain_is_not_called_for_strangers(tg, sent, monkeypatch):
    """Незнакомцу идёт готовый текст: модель не должна ни сочинять, ни жечь токены."""
    calls = []
    monkeypatch.setattr(tg, "hermes_answer", lambda p, s: calls.append(1) or "ответ")

    tg.maybe_autoreply(_msg())

    assert calls == []


def test_invite_can_be_switched_off(tg, sent, monkeypatch):
    monkeypatch.delenv("TG_LEAD_INVITE", raising=False)

    tg.maybe_autoreply(_msg())

    assert sent == []
    assert tg.lead_invite_enabled() is False


def test_owner_own_messages_never_get_the_invite(tg, sent):
    """Исходящие самого аккаунта приходят тем же апдейтом — приглашать себя нельзя."""
    tg.maybe_autoreply(_msg(uid=8715335144, username="alberyaimanager"))

    assert sent == []


def test_bots_and_groups_get_nothing(tg, sent):
    bot = _msg(uid=777)
    bot["from"]["is_bot"] = True
    tg.maybe_autoreply(bot)

    tg.maybe_autoreply(_msg(uid=778, chat={"id": -100, "type": "group"}))

    assert sent == []


def test_failed_delivery_is_not_marked_as_sent(tg, monkeypatch):
    """Иначе одна сетевая ошибка молча лишает человека приглашения навсегда."""
    monkeypatch.setattr(tg, "send_as_account", lambda uid, t, parse_mode="": (False, "сеть"))

    assert tg.invite_stranger(999) is False
    assert tg._invite_already_sent(999) is False


def test_invite_repeats_after_the_cooldown(tg, sent, monkeypatch):
    """Написал снова спустя месяцы — уместно напомнить про анкету."""
    tg.maybe_autoreply(_msg())
    assert len(sent) == 1

    monkeypatch.setattr(tg, "_INVITE_COOLDOWN_S", 0.0)
    tg.maybe_autoreply(_msg(text="всё ещё интересно"))

    assert len(sent) == 2


def test_corrupt_invite_timestamp_does_not_cause_a_repeat(tg, sent):
    state = json.loads(tg.STATE_PATH.read_text(encoding="utf-8"))
    state["invited"] = {"999": "не дата"}
    tg.STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    tg.maybe_autoreply(_msg())

    assert sent == []


def test_invite_is_sent_as_formatted_html(tg, sent):
    """Оформление — часть первого впечатления от компании."""
    tg.maybe_autoreply(_msg())

    assert sent[0]["parse_mode"] == "HTML"
    assert "<b>" in sent[0]["text"]


def test_invite_html_is_well_formed(tg, sent):
    """Незакрытый тег — и Telegram отклонит сообщение целиком."""
    tg.maybe_autoreply(_msg())

    text = sent[0]["text"]
    assert text.count("<b>") == text.count("</b>")
    assert "**" not in text and "__" not in text, "markdown в HTML-режиме отображается как мусор"
