"""Слишком длинный промпт не должен ронять ход агента.

20.07.2026, агент-юрист, диалог 16: два хода подряд упали за 0,3 секунды с
«[Errno 7] Argument list too long: 'hermes'». Промпт уходит аргументом командной строки, а
Linux не даёт одному аргументу больше 128 КБ — контекст с вшитыми документами перевалил лимит,
и процесс вообще не стартовал. Пользователь видел «Что-то пошло не так на моей стороне».
"""
from __future__ import annotations

import pytest


@pytest.fixture
def fit(app_module):
    import b24bot

    return b24bot._b24_fit_prompt


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def test_short_prompt_is_untouched(fit):
    parts = ["Роль агента", "История диалога", "Текущее сообщение пользователя:\nПривет"]
    assert fit(parts) == "\n\n".join(parts)


def test_oversized_prompt_is_trimmed_under_the_limit(fit):
    huge = "Текст договора. " * 20000          # ~320 КБ в UTF-8
    parts = ["Роль агента", huge, "Текущее сообщение пользователя:\nСколько токенов?"]

    out = fit(parts, limit=100_000)

    assert _bytes(out) <= 100_000, "промпт обязан уложиться в лимит запуска"


def test_current_user_message_survives_intact(fit):
    """Вопрос пользователя терять нельзя — иначе агент ответит не на то."""
    huge = "Старая переписка. " * 20000
    question = "Текущее сообщение пользователя:\nПодготовь претензию по складу в Электростали"
    parts = ["Роль", huge, question]

    out = fit(parts, limit=60_000)

    assert question in out
    assert _bytes(out) <= 60_000


def test_truncation_is_announced_to_the_agent(fit):
    """Агент должен знать, что текст урезан, и уметь дочитать его инструментом."""
    parts = ["Роль", "Документ. " * 20000, "Текущее сообщение пользователя:\nчто там?"]

    out = fit(parts, limit=50_000)

    assert "контекст сокращён" in out
    assert "get_attachment_text" in out


def test_even_a_single_giant_message_fits(fit):
    """Крайний случай: само сообщение пользователя больше лимита."""
    parts = ["Текущее сообщение пользователя:\n" + "а" * 200000]

    out = fit(parts, limit=40_000)

    assert _bytes(out) <= 40_000


def test_empty_and_blank_parts_are_safe(fit):
    assert fit([]) == ""
    assert fit(["", "   ", None]) == ""


def test_real_world_shape_stays_under_linux_arg_limit(fit):
    """Сумма блоков как в проде: роль + история + два документа + вопрос."""
    parts = [
        "Роль агента-юриста",
        "История этого диалога:\n" + ("реплика " * 5000),
        "ТВОЙ ПОСЛЕДНИЙ СОЗДАННЫЙ ДОКУМЕНТ:\n" + ("текст договора " * 8000),
        "ВЛОЖЕНИЯ:\n" + ("текст претензии " * 8000),
        "Текущее сообщение пользователя:\nСколько токенов сейчас контекстное окно тут?",
    ]

    out = fit(parts)

    assert _bytes(out) <= 128 * 1024, "иначе execve снова упадёт с E2BIG"
    assert "Сколько токенов" in out
