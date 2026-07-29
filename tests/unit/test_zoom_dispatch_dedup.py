"""Один человек — одна карточка на созвон.

Инцидент 29.07.2026: по созвону «Итоги созвона 29.07, 10:58 - 11:32» Анастасия Андрусяк
получила ДВЕ одинаковые задачи — 2314 и 2318, отпечаток описания совпадает (e6e689c89e).
Остальные участники получили по одной.

Причина не в деплое и не в двойном запуске крона: все пять задач созвона созданы за три
секунды одним прогоном (08:55:01–08:55:03 UTC). Она попала в получатели дважды — под своим
именем (bitrix_user_id 42) и как «Анастасия Докучаева» без id, а алиас оргструктуры
(«анастасия докучаева» → «Анастасия Андрусяк», живёт с 30.05.2026) сводит их в одного
человека 42.

Списки оперативных карточек и персональных итогов строятся независимо и каждый дедуплицирует
себя сам. На стыке защиты не было: `build_zoom_combined_dispatch` склеивал их как есть и
дедуплицировал только список получателей, но не сами карточки.
"""
from __future__ import annotations


def _dedupe(cards):
    """Та же логика, что в `build_zoom_combined_dispatch`, — на чистых данных."""
    seen = set()
    out = []
    for card in cards:
        recipient = card.get("recipient") if isinstance(card.get("recipient"), dict) else None
        user_id = recipient.get("user_id") if recipient else None
        if user_id is not None:
            if user_id in seen:
                continue
            seen.add(user_id)
        out.append(card)
    return out


ANDRUSYAK = {"name": "Анастасия Андрусяк", "user_id": 42}
STROGONOV = {"name": "Дмитрий Александрович Строгонов", "user_id": 38}


def test_one_person_gets_one_card_even_under_two_names():
    """Живой случай: она же под именем «Докучаева» резолвится в того же человека 42."""
    cards = [
        {"card_kind": "operational", "recipient": ANDRUSYAK, "title": "Итоги созвона"},
        {"card_kind": "participant_report", "recipient": dict(ANDRUSYAK),
         "title": "Итоги созвона"},
    ]

    result = _dedupe(cards)

    assert len(result) == 1
    assert result[0]["card_kind"] == "operational", "остаться должна карточка с задачами"


def test_other_recipients_are_untouched():
    cards = [
        {"card_kind": "operational", "recipient": ANDRUSYAK},
        {"card_kind": "participant_report", "recipient": STROGONOV},
        {"card_kind": "participant_report", "recipient": dict(ANDRUSYAK)},
    ]

    result = _dedupe(cards)

    assert [c["recipient"]["user_id"] for c in result] == [42, 38]


def test_card_without_recipient_is_not_swallowed():
    """Карточка без получателя — не повод её терять: пусть дойдёт и будет видна."""
    cards = [{"card_kind": "operational"}, {"card_kind": "operational"}]

    assert len(_dedupe(cards)) == 2


def test_the_real_call_shape_gives_four_cards_not_five():
    """Форма того самого созвона: 5 карточек на 4 человек → должно остаться 4."""
    cards = [
        {"card_kind": "operational", "recipient": {"name": "Артур", "user_id": 28}},
        {"card_kind": "participant_report", "recipient": STROGONOV},
        {"card_kind": "participant_report", "recipient": ANDRUSYAK},
        {"card_kind": "participant_report", "recipient": {"name": "Оксана", "user_id": 32}},
        {"card_kind": "participant_report", "recipient": dict(ANDRUSYAK)},
    ]

    result = _dedupe(cards)

    assert len(result) == 4
    assert sorted(c["recipient"]["user_id"] for c in result) == [28, 32, 38, 42]
