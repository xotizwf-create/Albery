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


def test_nothing_is_moved_around_the_answer():
    """Предложения остаются там, где их написал агент.

    Раньше единственный «шаг» переезжал в конец сообщения. Перестановка меняет смысл текста,
    который модель уже выстроила по правилам оформления, и именно она уносила первый пункт
    списка требований в самый низ ответа. Порядок теперь неприкосновенен."""
    answer = "Пришлите ссылку на магазин.\n\nПодключение занимает до 3 рабочих дней."

    assert iu_filters.one_next_step(answer) == answer


def test_a_list_of_requirements_survives_intact():
    """Ответ «что от вас нужно» — самый частый шаг воронки, и он ломался целиком.

    Признаком «следующего шага» считается повелительный глагол, а из них состоит любой такой
    ответ. Прежний фильтр оставлял от нумерованного списка «1.», «2.», «3.» без текста,
    выбрасывал ссылку на анкету и финальный вопрос, а первый пункт уносил в конец сообщения."""
    answer = (
        "[b]Что нужно от вас[/b]\n"
        "1. Пришлите реквизиты компании;\n"
        "2. Укажите систему налогообложения;\n"
        "3. Подтвердите форму подписания.\n\n"
        "[b]Дальше[/b]\n"
        "После этого подготовим договор и вышлем его сюда на согласование.\n\n"
        "Скажите, вам удобнее ЭДО или бумага?"
    )

    result = iu_filters.one_next_step(answer)

    assert result == answer
    assert "1. Пришлите реквизиты компании;" in result
    assert result.rstrip().endswith("Скажите, вам удобнее ЭДО или бумага?")


def test_extra_step_is_trimmed_only_in_the_closing_block():
    """Защита от допроса осталась: лишний шаг убирается там, где стоит закрывающий вопрос."""
    answer = (
        "1. Пришлите реквизиты компании;\n"
        "2. Укажите систему налогообложения.\n\n"
        "Какой у вас оборот в месяц? Пришлите ссылку на магазин."
    )

    result = iu_filters.one_next_step(answer)

    assert "1. Пришлите реквизиты компании;" in result
    assert "2. Укажите систему налогообложения." in result
    assert result.rstrip().endswith("Какой у вас оборот в месяц?")
    assert "ссылку на магазин" not in result


def test_flat_short_answer_behaves_exactly_as_before():
    """Короткие реплики не должны внезапно поехать: у них поведение прежнее."""
    assert iu_filters.one_next_step(
        "Подключение доступно. Какой у вас вопрос? Пришлите ссылку."
    ) == "Подключение доступно. Какой у вас вопрос?"


def test_prompt_asks_for_the_owners_answer_shape():
    """Форма ответа — стандарт компании «Формат ответа», по которому отвечает Агент Албери."""
    rules = iu_prompt.RULES

    assert "Между смысловыми блоками — ПУСТАЯ строка" in " ".join(rules.split())
    assert "Полотно" in rules
    assert "Простой вопрос — простой короткий ответ" in " ".join(rules.split())
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
    rules = " ".join(iu_prompt.RULES.split())

    assert "на каждый свой блок: жирный заголовок темы" in rules
    assert "Отвечай в порядке клиента" in rules


def test_client_role_comes_from_the_client_agent_card():
    """Владелец 29.07.2026: «ответы сухие и пресные».

    Роль бралась у внутреннего «Агента по работе с ИУ» — того, что работает в группе Битрикса
    с сотрудниками. В его карточке «пиши 1–3 предложения» и «точку в конце реплики не ставь»,
    и этот текст стоит в промпте выше всех правил, то есть молча их отменял."""
    import tg_agent

    assert tg_agent.customer_agent_slug() == "iu-customer-runtime"


def test_prompt_shows_the_owners_sample_answer():
    """Пример показывает форму целиком и работает сильнее любой инструкции."""
    built = iu_prompt.build(iu_prompt.Context(message="а какая комиссия?"))

    assert "ОБРАЗЕЦ ОТВЕТА" in built
    assert "В эти 44% уже входят:" in built
    assert "1. Комиссия Wildberries;" in built


# --- стиль Агента Албери --------------------------------------------------------------------

def test_bold_from_the_agent_reaches_the_client():
    """Владелец 29.07.2026: «скопируй стиль ответа Албери и перенеси на этого агента».

    Стандарт компании «Формат ответа»: заголовки и ключевые цифры жирным BB-кодом. Раньше
    любой [b] вырезался перед отправкой, потому что бот слал чистый текст."""
    import tg_agent

    kept = tg_agent._strip_markup("[b]Комиссия[/b]\nБазовая — [b]44%[/b].", keep_bold=True)
    assert kept == "[b]Комиссия[/b]\nБазовая — [b]44%[/b]."

    rendered = tg_agent.telegram_html(kept)
    assert rendered == "<b>Комиссия</b>\nБазовая — <b>44%</b>."


def test_business_channel_still_gets_plain_text():
    """В переписке менеджерского аккаунта разметки нет — там сообщение уходит от лица человека."""
    import tg_agent

    assert tg_agent._strip_markup("[b]Комиссия[/b] 44%") == "Комиссия 44%"


def test_prompt_demands_numbered_lists_and_bold_headings():
    rules = iu_prompt.RULES

    assert "Перечисления НУМЕРУЙ" in rules
    assert "[b]Комиссия[/b]" in rules
    assert "Маркеры «—» и «•» не используй" in rules


def test_sample_answer_is_written_in_that_style():
    built = iu_prompt.build(iu_prompt.Context(message="а какая комиссия?"))

    assert "[b]Комиссия[/b]" in built
    assert "1. Комиссия Wildberries;" in built
    assert "[b]Итог[/b]" in built


# --- ссылки в сообщениях бота ---------------------------------------------------------------

def test_link_becomes_clickable_for_the_client():
    """Владелец 29.07.2026: «чтоб Калькулятор ИУ был как гиперссылка».

    Без этого клиент получал бы markdown-скобки: разметку собирала только жирная строка."""
    import tg_agent

    rendered = tg_agent.telegram_html("Считайте в [Калькуляторе ИУ](https://www.m4s.ru/calc/).")

    assert '<a href="https://www.m4s.ru/calc/">Калькуляторе ИУ</a>' in rendered
    assert "[" not in rendered


def test_cyrillic_url_is_percent_encoded():
    """Адрес калькулятора кириллический. Неразобранный адрес Telegram отбивает целиком —
    клиент не получает НИЧЕГО, поэтому кодирование обязательно."""
    import tg_agent

    rendered = tg_agent.telegram_html("[Калькулятор ИУ](https://www.m4s.ru/Калькулятор/)")

    assert "%D0%9A" in rendered
    assert "Калькулятор/" not in rendered.split("href=")[1].split('"')[1]


def test_model_cannot_smuggle_a_link_tag():
    """Разметку собираем мы: тег из текста модели остаётся текстом."""
    import tg_agent

    rendered = tg_agent.telegram_html('<a href="https://evil.example">жми</a>')

    assert "&lt;a href=" in rendered
    assert "<a href=" not in rendered


def test_calculator_reply_is_a_sentence_with_a_link():
    import iu_client_bot

    assert "[Калькуляторе ИУ](" in iu_client_bot.CALCULATOR_REPLY
    assert "Посчитать свою выгоду" in iu_client_bot.CALCULATOR_REPLY


def test_join_reply_carries_the_personal_link():
    import iu_client_bot

    body = iu_client_bot.join_reply("https://www.m4s.ru/iu/abc")

    assert "[Заполнить анкету](https://www.m4s.ru/iu/abc)" in body
    assert "менеджер с Вами свяжется" in body


def test_already_filled_answer_does_not_offer_the_form_again():
    """Владелец 29.07.2026: «если для его айди заполнено — бот скажет, вы уже заполнили».

    И показывает данные из САМОЙ сделки: «вот ваши данные» из его же формулировки."""
    import iu_client_bot

    assert "Анкета уже получена" in iu_client_bot.JOIN_FILLED
    assert "что нужно изменить" in iu_client_bot.JOIN_FILLED
    assert "http" not in iu_client_bot.JOIN_FILLED
