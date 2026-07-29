from __future__ import annotations

# Гибкость в цифрах (владелец 28.07.2026): клиент называет свою ставку — агент пересчитывает
# пример из базы под неё. Выдумывать ставки при этом по-прежнему нельзя.

import iu_contract as c

SOURCES = """[как-определяется-налоговая-база] Как определяется налоговая база?
Для управленческого расчёта можно ориентироваться на сумму реализации за вычетом СПП.
Например: реализация 1 000 ₽, СПП 30% — ориентировочная база составит 700 ₽.
Для селлера на УСН «Доходы» 5% ориентировочный налог с такой базы — 35 ₽.

[какая-комиссия-будет-у-вас] Какая комиссия будет у Вас?
Базовая комиссия по программе составляет 44% от суммы реализации товара.
"""


def _plan(reply: str) -> c.TurnPlan:
    return c.TurnPlan(reply=reply, next_action=c.REPLY_ONLY, confidence=0.9,
                      source_ids=("как-определяется-налоговая-база",))


def test_client_rate_is_recalculated_from_our_example():
    """«У меня УСН 6%» → агент считает 700 × 6% = 42 ₽ по нашему же примеру."""

    plan = _plan("По нашему примеру: реализация 1 000 ₽, СПП 30% — база 700 ₽. "
                 "При вашей ставке 6% налог составит 42 ₽.")

    assert c.unbacked_numbers(plan, SOURCES, message="у меня налог 6% усн") == set()


def test_invented_rate_is_still_vetoed():
    """Выдуманная ставка не выводится ни из базы, ни из слов клиента."""

    plan = _plan("Для вас сделаем комиссию 20% вместо 44%.")

    assert "20" in c.unbacked_numbers(plan, SOURCES, message="а можно скидку?")


def test_number_named_by_the_client_is_allowed():
    plan = _plan("Вы назвали оборот 30 000 000 ₽ — это выше нашего минимума 3 000 000 ₽.")

    assert c.unbacked_numbers(
        plan, SOURCES + "минимум 3 000 000 ₽", message="оборот 30 000 000") == set()


def test_derived_number_needs_an_example_context():
    """Свободная цифра без расчёта остаётся выдумкой, даже если случайно выводится."""

    plan = _plan("Наша комиссия для вас будет 42%.")

    assert c.unbacked_numbers(plan, SOURCES, message="у меня 6%") != set()


def test_single_threshold_for_every_turn():
    """Владелец 28.07.2026: уверенность одна — 65%, отдельного порога для расчётов нет."""

    plan = _plan("Расчёт составит 42 ₽ при вашей ставке 6%.")

    assert c.threshold_for(plan, "посчитайте мою экономику") == c.THRESHOLD
    assert c.THRESHOLD == 0.65


def test_calculation_still_needs_grounding():
    """Порог один, но опора на источник осталась обязательной."""

    plan = c.TurnPlan(reply="Вы получите примерно 540 000 ₽.", next_action=c.REPLY_ONLY,
                      confidence=0.99)
    verdict = c.assess(plan, retrieval=0.9, sources_text=SOURCES,
                       message="сколько я получу с миллиона?")

    assert verdict.allowed is False


def test_prompt_allows_recalculation_and_demands_examples():
    """Правила промпта должны разрешать то, что теперь разрешает контракт."""

    import iu_prompt

    rules = iu_prompt.RULES
    assert "Ничего не считай сам" not in rules
    assert "УСН 6%" in rules, "пересчёт под ставку клиента должен быть назван прямо"
    assert "неприкосновенны" in rules, "наши условия менять по-прежнему нельзя"
    assert "конкретном примере" in rules, "владелец просил обязательные примеры"


def test_client_disputing_our_numbers_always_goes_to_a_human():
    """Спор о деньгах — зона человека, независимо от уверенности (живой случай 25.07.2026)."""

    plan = _plan("44% вычитается от суммы к перечислению.")

    for message in ("вы неправильно считаете, 44% вычитается от продаж",
                    "вы ошиблись в расчёте",
                    "это не так, комиссия другая"):
        verdict = c.assess(plan, retrieval=0.95, sources_text=SOURCES, message=message)
        assert verdict.allowed is False, message

    # Обычный вопрос спором не считается.
    ok = c.assess(plan, retrieval=0.9, sources_text=SOURCES, message="а от чего считается 44%?")
    assert ok.allowed is True


def test_list_numbering_is_not_a_claimed_number():
    """Владелец 29.07.2026 попросил нумеровать перечисления — и номер пункта тут же сработал
    как «число не подтверждено источником»: живой прогон уводил к человеку ответ, в котором
    агент не называл ни одной цифры от себя."""
    import iu_contract as c

    plan = c.TurnPlan(
        reply=("Для подключения нужны:\n"
               "1. Согласование условий и подписание договора;\n"
               "2. Документы на товар и бренд;\n"
               "3. Доступы к кабинету;\n"
               "4. Настройка «Честного знака»."),
        next_action=c.REPLY_ONLY,
        confidence=0.9,
        source_ids=("подключение",),
    )

    assert c.unbacked_numbers(plan, "Для подключения нужны документы и доступы.") == set()


def test_a_real_invented_number_is_still_caught_in_a_numbered_list():
    import iu_contract as c

    plan = c.TurnPlan(
        reply="Условия:\n1. Комиссия 20% вместо 44%;\n2. Подключение бесплатно.",
        next_action=c.REPLY_ONLY,
        confidence=0.9,
        source_ids=("комиссия",),
    )

    assert "20" in c.unbacked_numbers(plan, "Единая комиссия 44% от суммы реализации.")


def test_client_question_numbers_in_bold_headings_are_not_facts():
    """Отвечая на список вопросов, агент нумерует блоки номерами САМОГО клиента.

    Живой прогон 29.07.2026: «[b]4–5. Управление карточками и ценами[/b]» дал «числа не
    подтверждены источником: 4, 6, 8, 9» и срезал треть опоры ответа, в котором агент не
    назвал от себя ни одной цифры."""
    import iu_contract as c

    plan = c.TurnPlan(
        reply=("[b]1. Перенос карточек[/b]\nПеренос не рассматривается.\n\n"
               "[b]4–5. Управление карточками и ценами[/b]\nДоступ после оплаты.\n\n"
               "[b]9. Экономика[/b]\nВы получаете 56% от суммы реализации."),
        next_action=c.REPLY_ONLY,
        confidence=0.9,
        source_ids=("экономика",),
    )
    sources = "Клиенту остаётся 56% от суммы реализации до СПП."

    assert c.unbacked_numbers(plan, sources) == set()
    grounding, reasons = c.grounding_score(plan, sources)
    assert grounding == 1.0, reasons


def test_a_decimal_number_keeps_its_whole_part():
    """«1.5 млн» — это число, а не пункт списка: пробел после точки обязателен."""
    import iu_contract as c

    plan = c.TurnPlan(reply="1.5 млн ₽ оборота.", next_action=c.REPLY_ONLY,
                      confidence=0.9, source_ids=("оборот",))

    assert c.unbacked_numbers(plan, "Программа рассчитана на оборот от 3 млн ₽.") == {"15"}
