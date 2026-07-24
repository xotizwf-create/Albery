"""Сторож анкеты и сверка из живых полей воронки (владелец, 24.07.2026).

Требования: (1) агент сам замечает, что клиент заполнил анкету, и начинает сверку, не дожидаясь
сообщения; (2) сверка «Вижу анкету: • <поле> — <значение> … Всё верно?» строится из НАЗВАНИЙ
полей воронки — переименовали поле в CRM, сообщение поменялось само, без деплоя.
"""
from __future__ import annotations

import json

import pytest

LABELS = {
    "UF_CRM_1784297026": "Ссылка на магазин / бренд WB",
    "UF_CRM_1784297137": "Категории товара",
    "UF_CRM_1784297181": "Оборот на WB сейчас, ₽/мес.",
    "UF_CRM_1784297221": "Планируемый оборот через кабинет, ₽/мес.",
}

DEAL = {
    "deal_id": 82, "stage_id": "C16:NEW",
    "custom_fields": {
        "UF_CRM_1784297026": "Test",
        "UF_CRM_1784297137": "одежда",
        "UF_CRM_1784297181": "5000000",
        "UF_CRM_1784297221": "20000000",
    },
}


@pytest.fixture
def tg(monkeypatch, tmp_path):
    import tg_agent

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "invited": {"555": "2026-07-24T09:00:00+00:00"},
        "contacts": {"lead": {"id": 555, "username": "lead", "name": "Лид"}},
    }), encoding="utf-8")
    monkeypatch.setattr(tg_agent, "STATE_PATH", state_file)
    monkeypatch.setattr(tg_agent, "load_state",
                        lambda: json.loads(state_file.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg_agent, "save_state",
                        lambda s: state_file.write_text(json.dumps(s, ensure_ascii=False),
                                                        encoding="utf-8"))
    monkeypatch.setattr(tg_agent, "_deal_field_labels", lambda: dict(LABELS))
    return tg_agent


def test_anketa_block_uses_live_field_names(tg):
    """Формат владельца, названия — из воронки, миллионы — по-человечески."""
    block = tg.anketa_block(DEAL)

    assert block == (
        "Вижу анкету:\n\n"
        "• Ссылка на магазин / бренд WB — Test\n"
        "• Категории товара — одежда\n"
        "• Оборот на WB сейчас, ₽/мес. — 5 млн\n"
        "• Планируемый оборот через кабинет, ₽/мес. — 20 млн\n\n"
        "Всё верно?")


def test_renamed_field_changes_the_message_without_deploy(tg, monkeypatch):
    """Главное требование универсальности: владелец переименовал поле — текст сменился сам."""
    renamed = dict(LABELS, UF_CRM_1784297181="Оборот на данный момент")
    monkeypatch.setattr(tg, "_deal_field_labels", lambda: renamed)

    assert "• Оборот на данный момент — 5 млн" in tg.anketa_block(DEAL)


def test_empty_form_fields_are_not_shown(tg):
    deal = {"stage_id": "C16:NEW",
            "custom_fields": {"UF_CRM_1784297137": "одежда", "UF_CRM_1784297181": ""}}
    block = tg.anketa_block(deal)

    assert "Категории товара" in block
    assert "Оборот" not in block, "пустое поле в сверке не показываем"


def test_funnel_new_stage_carries_the_exact_block(tg):
    """Модель на сверке обязана слать дословный текст, а не пересказ."""
    st = tg.funnel_next_step(DEAL)

    assert st["step"] == "Сверка анкеты"
    assert tg.anketa_block(DEAL) in st["action"]
    assert "РОВНО это сообщение" in st["action"]


def _wire_watch(tg, monkeypatch, deal=DEAL, spoke=False):
    sent, journaled = [], []
    monkeypatch.setattr(tg, "lead_deal_for_username", lambda u: 82)
    monkeypatch.setattr(tg, "_agent_already_spoke_on_deal", lambda d, i: spoke)
    monkeypatch.setattr(tg, "_deal_for_watch", lambda i: dict(deal))
    monkeypatch.setattr(tg, "send_html",
                        lambda uid, html, plain: sent.append((uid, plain)) or (True, ""))
    monkeypatch.setattr(tg, "journal",
                        lambda *a, **k: journaled.append({"args": a, "meta": k.get("meta")}))
    return sent, journaled


def test_watch_starts_the_survey_without_waiting_for_a_message(tg, monkeypatch):
    """Анкета появилась — агент пишет сверку САМ. Ровно один раз на сделку."""
    sent, journaled = _wire_watch(tg, monkeypatch)

    tg._check_new_forms()

    assert len(sent) == 1 and sent[0][0] == 555
    assert sent[0][1].startswith("Вижу анкету:") and sent[0][1].endswith("Всё верно?")
    assert journaled and journaled[0]["meta"] == {"deal_id": 82, "anketa": True}
    assert tg.load_state()["form_surveyed"]["555"] == 82

    tg._check_new_forms()      # второй проход — тишина
    assert len(sent) == 1, "одна сделка = одна сверка"


def test_new_deal_of_same_person_fires_again(tg, monkeypatch):
    """Человек заполнил анкету повторно (новая сделка) — сторож работает снова."""
    sent, _ = _wire_watch(tg, monkeypatch)
    st = tg.load_state(); st["form_surveyed"] = {"555": 77}; tg.save_state(st)

    tg._check_new_forms()

    assert len(sent) == 1, "новая сделка (82 ≠ 77) обязана получить сверку"


def test_watch_is_silent_when_agent_already_talks_on_the_deal(tg, monkeypatch):
    """Клиент написал сам раньше сторожа — разговор уже идёт, дублировать сверку нельзя."""
    sent, _ = _wire_watch(tg, monkeypatch, spoke=True)

    tg._check_new_forms()

    assert sent == []
    assert tg.load_state()["form_surveyed"]["555"] == 82, "сделка запоминается без отправки"


def test_watch_does_not_touch_deals_past_the_survey_stage(tg, monkeypatch):
    """Сделка уже дальше сверки (условия/договор) — сторожу там делать нечего."""
    deal = dict(DEAL, stage_id="C16:NDA")
    sent, _ = _wire_watch(tg, monkeypatch, deal=deal)

    tg._check_new_forms()

    assert sent == []
    assert tg.load_state()["form_surveyed"]["555"] == 82
