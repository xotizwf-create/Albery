"""Антиспам мониторинга обязан узнавать одну и ту же болячку в лицо.

Живой случай 16.08.2026 (владелец: «постоянно приходят уведомления о каких-то сбоях»).
Это был не поток разных аварий, а одна проблема, которую приглушение не опознавало:
подпись считалась по сырому тексту, а текст несёт изменчивый хвост — счётчик, процент,
возраст. Каждое такое изменение выглядело как НОВЫЙ набор проблем, пауза обнулялась,
и на пятиминутном расписании владелец получал до двенадцати сообщений в час.
"""
from pathlib import Path

from shared.alert_dedup import alert_signature, normalize_problem

SELFCHECK = Path("scripts/albery_selfcheck.py")


def test_counter_change_is_the_same_problem():
    """«таймауты ходов бота: 1» и «: 2» — одна болячка, а не две разные тревоги."""
    assert alert_signature(["таймауты ходов бота: 1"]) == alert_signature(["таймауты ходов бота: 2"])


def test_age_change_is_the_same_problem():
    """Возраст в тексте меняется сам по себе — назавтра это была новая подпись."""
    assert alert_signature(["Мозг агента (Hermes): успешный ход 1 дн назад"]) == \
        alert_signature(["Мозг агента (Hermes): успешный ход 2 дн назад"])


def test_order_does_not_matter():
    """Порядок проверок не является изменением состояния системы."""
    assert alert_signature(["диск / заполнен на 86%", "мало свободной памяти: 140 MB available"]) == \
        alert_signature(["мало свободной памяти: 149 MB available", "диск / заполнен на 88%"])


def test_a_genuinely_new_problem_still_changes_the_signature():
    """Приглушение не должно превратиться в глухоту: новая проблема обязана пробиться."""
    assert alert_signature(["таймауты ходов бота: 1"]) != \
        alert_signature(["таймауты ходов бота: 1", "КРИТИЧНО: PostgreSQL не отвечает на SELECT 1"])


def test_empty_set_has_empty_signature():
    assert alert_signature([]) == ""


def test_normalization_keeps_the_meaning_readable():
    assert normalize_problem("диск / заполнен на 86%") == "диск / заполнен на #%"


def test_selfcheck_rate_floor_protects_non_critical_only():
    """Пол частоты не имеет права задерживать критичные тревоги.

    Диск под завязку, упавшая база или лежащая служба обязаны уходить немедленно —
    у них своя, тридцатиминутная пауза повтора, и глушить их получасовым полом нельзя.
    """
    source = SELFCHECK.read_text(encoding="utf-8")
    assert "min_gap_s = 0 if critical else 30 * 60" in source, (
        "критичные тревоги обязаны идти мимо пола частоты"
    )
    assert "alert_signature(problems)" in source, (
        "подпись набора обязана строиться по смыслу, а не по сырому тексту"
    )
