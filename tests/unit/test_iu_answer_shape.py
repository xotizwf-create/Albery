"""Ответ клиенту сохраняет вид, в котором его написал агент.

Владелец 28.07.2026 показал образец «шикарного ответа»: прямой ответ первой строкой, под ним
список того, что входит в комиссию, отдельно — что удерживается сверх неё, в конце вывод.
Прежний фильтр «один следующий шаг» склеивал такой ответ в сплошной абзац: список из четырёх
пунктов приезжал клиенту одной строкой. Здесь закреплено, что структура доходит до человека,
а защита «не больше одного шага» при этом остаётся.
"""
from __future__ import annotations

import iu_filters
import iu_prompt

OWNER_SAMPLE = """Базовая комиссия по программе составляет 44% от суммы реализации товара.

В эти 44% уже входят:
— комиссия Wildberries;
— логистика, включая обратную;
— хранение и приёмка;
— наша агентская комиссия.

Отдельно удерживается:
— эквайринг, ориентировочно 2%;
— рекламные расходы за счёт селлера.

Наша агентская комиссия отдельно не выделяется — она уже включена в 44%."""


def test_structured_answer_keeps_its_lines():
    result = iu_filters.one_next_step(OWNER_SAMPLE)

    assert result.count("\n") >= 8, result
    assert "— хранение и приёмка;" in result
    assert "В эти 44% уже входят:" in result
    assert result.startswith("Базовая комиссия по программе составляет 44%")


def test_structured_answer_still_keeps_only_one_next_step():
    answer = OWNER_SAMPLE + "\n\nКакой у вас оборот в месяц? Пришлите ссылку на магазин."

    result = iu_filters.one_next_step(answer)

    assert result.count("?") == 1
    assert "Пришлите ссылку" not in result
    assert result.rstrip().endswith("Какой у вас оборот в месяц?")
    assert "— хранение и приёмка;" in result


def test_the_single_step_moves_below_the_answer():
    answer = "Пришлите ссылку на магазин.\n\nПодключение занимает до 3 рабочих дней."

    result = iu_filters.one_next_step(answer)

    assert result.startswith("Подключение занимает")
    assert result.rstrip().endswith("Пришлите ссылку на магазин.")


def test_flat_short_answer_behaves_exactly_as_before():
    """Короткие реплики не должны внезапно поехать: у них поведение прежнее."""
    assert iu_filters.one_next_step(
        "Подключение доступно. Какой у вас вопрос? Пришлите ссылку."
    ) == "Подключение доступно. Какой у вас вопрос?"


def test_prompt_asks_for_the_owners_answer_shape():
    rules = iu_prompt.RULES

    assert "Первой строкой — прямой ответ" in rules
    assert "с «— » в начале" in rules
    assert "написано сплошной строкой" in rules
    assert "Простой вопрос — простой короткий ответ" in rules
    # Требование знаков препинания: агент писал строки без точек, и ответ обрывался
    # на полуслове. Пробелы в правиле переносятся, поэтому сверяем по словам.
    assert "Знаки препинания обязательны" in rules
    assert "КАЖДОЕ предложение" in rules
    assert "через точку с запятой" in " ".join(rules.split())


# --- разметка в Telegram --------------------------------------------------------------------

def test_headings_become_bold_for_telegram():
    """Владелец 29.07.2026: «непонятно, на какие вопросы он дал ответ».

    Бот шлёт текст без разметки, поэтому девять ответов подряд читались одной простынёй.
    Строка-заголовок опознаётся по двоеточию и выделяется при отправке."""
    import tg_agent

    rendered = tg_agent.telegram_html(
        "Перенос карточек:\nСоздаём новые карточки.\n\nДоступ к кабинету:\nПосле оплаты."
    )

    assert "<b>Перенос карточек:</b>" in rendered
    assert "<b>Доступ к кабинету:</b>" in rendered
    assert "Создаём новые карточки." in rendered


def test_model_cannot_smuggle_tags_through_the_formatting():
    """Разметку собираем сами: всё, что написала модель, экранируется."""
    import tg_agent

    rendered = tg_agent.telegram_html("<b>жирный</b> и <a href='x'>ссылка</a>\nИтог:")

    assert "&lt;b&gt;жирный&lt;/b&gt;" in rendered
    assert "<a href" not in rendered
    assert "<b>Итог:</b>" in rendered


def test_long_sentence_with_a_colon_is_not_a_heading():
    """Заголовок — короткая строка. Иначе жирным станет половина ответа."""
    import tg_agent

    line = ("Порядок расчётов, отчётности, обязательства сторон и все прочие условия работы "
            "фиксируются в договоре при подключении:")
    rendered = tg_agent.telegram_html(line)

    assert "<b>" not in rendered


def test_prompt_requires_a_heading_per_question():
    rules = iu_prompt.RULES

    assert "ОТДЕЛЬНОЙ строкой-заголовком" in rules
    assert "заканчивается двоеточием" in rules
