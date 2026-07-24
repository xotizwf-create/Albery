"""Шаблона договора нет в базе знаний — реквизиты уходят людям (владелец, 24.07.2026).

Владелец временно удалил «Шаблон договора ИУ». Агент обязан: принять реквизиты, записать их
в сделку, сказать клиенту «передал информацию ответственному менеджеру — вернусь с обратной
связью», а в группу уведомлений отправить «Человек отправил реквизиты, нужен следующий шаг».
И не повторять всё это при каждом следующем сообщении клиента.
"""
from __future__ import annotations

import pytest

VALID_CARD = """Наименование: ООО «Настоящая фирма»
ИНН: 7707083893
КПП: 773601001
ОГРН: 1027700132195
Юридический адрес: 125009, г. Москва, ул. Тверская, д. 1
Расчетный счет (р/с): 40702810912345678901
Корреспондентский счет (к/с): 30101810400000000225
БИК: 044525225
Банк: ПАО Сбербанк
Генеральный директор: Иванов Иван Иванович"""


@pytest.fixture
def wired(monkeypatch):
    import contract
    import tg_agent as tg
    from mcp import context_server as cs

    sent, cards, deal_updates, ledger = [], [], [], []
    monkeypatch.setattr(contract, "load_template",
                        lambda: (_ for _ in ()).throw(ValueError("В базе знаний нет документа")))
    monkeypatch.setitem(cs.TOOLS, "get_crm_deal",
                        {"handler": lambda a: {"deal": {"deal_id": 82, "category_id": 16,
                                                        "custom_fields": {}}}})
    monkeypatch.setitem(cs.TOOLS, "notify_iu_group",
                        {"handler": lambda a: cards.append(a["text"]) or {"sent": True}})
    monkeypatch.setitem(cs.TOOLS, "update_crm_deal",
                        {"handler": lambda a: deal_updates.append(a) or {}})
    monkeypatch.setattr(tg, "send_html",
                        lambda uid, html, plain: sent.append((uid, plain)) or (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: ledger.append(k.get("meta") or {}))
    monkeypatch.setattr(tg, "_requisites_already_forwarded", lambda d, i: False)
    monkeypatch.setattr(tg, "contacts",
                        lambda: {"georg": {"id": 256942600, "username": "grad004",
                                           "name": "Георгий"}})
    return tg, sent, cards, deal_updates, ledger


def test_no_template_forwards_requisites_to_people(wired):
    tg, sent, cards, deal_updates, ledger = wired

    res = tg.contract_send(82, 256942600, requisites_text=VALID_CARD)

    assert res["sent"] is False and res["forwarded"] is True
    assert sent and "Передал информацию ответственному менеджеру" in sent[0][1]
    assert cards and cards[0].startswith("[b]Человек отправил реквизиты, нужен следующий шаг[/b]")
    assert "Георгий" in cards[0] and "@grad004" in cards[0] and "7707083893" in cards[0]
    assert "Скажите мне здесь" in cards[0]
    assert any(m.get("requisites_forwarded") for m in ledger), "отметка для идемпотентности"
    assert deal_updates and deal_updates[0]["custom_fields"], "реквизиты записаны в сделку"


def test_forwarding_happens_once_not_on_every_turn(wired, monkeypatch):
    """Следующие ходы (клиент спросил что-то ещё) не должны слать «передал менеджеру» снова."""
    tg, sent, cards, deal_updates, ledger = wired
    monkeypatch.setattr(tg, "_requisites_already_forwarded", lambda d, i: True)

    res = tg.contract_send(82, 256942600, requisites_text=VALID_CARD)

    assert res["forwarded"] is True
    assert sent == [] and cards == [], "повтор — это флуд и клиенту, и группе"
    assert "жди" in res["note"].lower() or "Повторно" in res["note"]


def test_broken_requisites_still_rejected_before_forwarding(wired):
    """Валидация реквизитов работает и без шаблона: фигня не уходит менеджеру как реквизиты."""
    tg, sent, cards, _, _ = wired

    res = tg.contract_send(82, 256942600,
                           requisites_text="Наименование: ООО «Ф»\nИНН: 18848844838")

    assert res["sent"] is False and "invalid" in res or res.get("missing")
    assert cards == [], "мусор не пересылаем людям как реквизиты"
