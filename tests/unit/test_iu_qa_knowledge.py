from __future__ import annotations

# Раздел «Вопрос — ответ» из документа владельца: агент отвечает по готовым карточкам.

import iu_knowledge

# Фрагмент живого документа (28.07.2026) — вместе со служебной шапкой синка и опечаткой
# в нумерации, которую владелец оставил в шестом пункте.
DOCUMENT = """Источник: https://docs.google.com/document/d/118nP2/edit?usp=drivesdk
Обновлено в Google Drive: 2026-07-28T11:33:51.590Z
Тип: application/vnd.google-apps.document

1. Какая комиссия будет у Вас? Сколько я буду получать? Какая комиссия ВБ?

Ответ: Базовая комиссия по программе составляет 44% от суммы реализации товара. В неё входят
комиссия Wildberries, логистика, включая обратную, хранение и приёмка, а также наша агентская
комиссия.

Эквайринг составляет ориентировочно 2% и удерживается дополнительно.

2. От какой суммы считается процент?

Ответ: Процент рассчитывается от суммы реализации товара (До СПП), а не от суммы, которую
Wildberries перечисляет после своих удержаний.

6. 6. Можно ли рассчитать экономику по конкретному товару?

Ответ: Да, ориентировочный расчёт делает менеджер.

Расчёт показывает ориентир и не является гарантией дохода.

8. Сколько занимает подключение?

Ответ: Ориентировочный срок подключения — до 3 рабочих дней.

Основные этапы:

- согласование условий и подписание договора;

- проверка документов на товар.
"""


def test_numbered_questions_become_cards():
    cards = iu_knowledge.parse_qa(DOCUMENT)

    assert len(cards) == 4
    titles = [card.title for card in cards]
    assert titles[1] == "От какой суммы считается процент?"
    # Служебная шапка синка карточкой не становится.
    assert not any("Обновлено в Google Drive" in title for title in titles)


def test_answer_keeps_its_whole_body():
    cards = {card.title: card for card in iu_knowledge.parse_qa(DOCUMENT)}
    подключение = cards["Сколько занимает подключение?"]

    assert "до 3 рабочих дней" in подключение.answer
    # Списки и пояснения — часть ответа, обрывать их нельзя.
    assert "согласование условий" in подключение.answer
    assert "проверка документов на товар" in подключение.answer


def test_several_wordings_of_one_question_become_aliases():
    """Владелец пишет вопрос так, как его задают клиенты — иногда сразу тремя способами."""

    card = iu_knowledge.parse_qa(DOCUMENT)[0]

    assert card.title == "Какая комиссия будет у Вас?"
    assert "Сколько я буду получать?" in card.aliases
    assert "Какая комиссия ВБ?" in card.aliases


def test_duplicated_numbering_does_not_leak_into_the_question():
    cards = [card.title for card in iu_knowledge.parse_qa(DOCUMENT)]

    assert "Можно ли рассчитать экономику по конкретному товару?" in cards
    assert not any(title.startswith("6.") for title in cards)


def test_cards_are_approved_and_findable():
    cards = iu_knowledge.parse_qa(DOCUMENT)

    assert all(card.approved for card in cards)
    found = iu_knowledge.search("сколько вы берёте комиссии", cards)
    assert found, "вопрос клиента обязан находить карточку раздела"
    assert "комиссия" in found[0].card.title.casefold()


def test_exact_wording_matches_the_alias():
    cards = iu_knowledge.parse_qa(DOCUMENT)

    found = iu_knowledge.search("Какая комиссия ВБ?", cards)

    assert found and found[0].score >= 0.9, "дословный вопрос владельца должен совпадать точно"


def test_answer_without_a_question_is_ignored():
    cards = iu_knowledge.parse_qa("Ответ: болтается без вопроса")

    assert cards == ()


def test_empty_answer_stays_a_draft():
    cards = iu_knowledge.parse_qa("1. Есть ли гарантии?\n\nОтвет:\n\n2. Что ещё?\n\nОтвет: Да.")

    by_title = {card.title: card for card in cards}
    assert by_title["Есть ли гарантии?"].approved is False
    assert by_title["Что ещё?"].approved is True


def test_runtime_reads_both_documents(monkeypatch):
    """Раздел «Вопрос — ответ» встаёт рядом с карточками, а не вместо них."""

    import sys
    from types import SimpleNamespace

    import iu_runtime

    documents = {
        "doc-cards": "### Комиссия\nОтвет: Единая комиссия 44%.\n",
        "doc-qa": DOCUMENT,
    }
    tools = {
        "list_company_files": {"handler": lambda _args: {"files": [
            {"name": iu_runtime.KNOWLEDGE_DOC_NAME, "google_file_id": "doc-cards"},
            {"name": iu_runtime.QA_DOC_NAME, "google_file_id": "doc-qa"},
        ]}},
        "get_company_file": {"handler": lambda args: {"content": documents[args["google_file_id"]]}},
    }
    monkeypatch.setitem(sys.modules, "mcp.context_server", SimpleNamespace(TOOLS=tools))
    monkeypatch.setitem(sys.modules, "mcp", SimpleNamespace(context_server=SimpleNamespace(TOOLS=tools)))

    cards = iu_runtime.knowledge_cards(force=True)

    titles = [card.title for card in cards]
    assert "Комиссия" in titles, "карточки основного документа никуда не делись"
    assert "От какой суммы считается процент?" in titles, "вопросы раздела подключены"


def test_missing_qa_document_does_not_break_the_rest(monkeypatch):
    import sys
    from types import SimpleNamespace

    import iu_runtime

    tools = {
        "list_company_files": {"handler": lambda _args: {"files": [
            {"name": iu_runtime.KNOWLEDGE_DOC_NAME, "google_file_id": "doc-cards"},
        ]}},
        "get_company_file": {"handler": lambda _args: {"content": "### Комиссия\nОтвет: 44%.\n"}},
    }
    monkeypatch.setitem(sys.modules, "mcp.context_server", SimpleNamespace(TOOLS=tools))
    monkeypatch.setitem(sys.modules, "mcp", SimpleNamespace(context_server=SimpleNamespace(TOOLS=tools)))

    cards = iu_runtime.knowledge_cards(force=True)

    assert [card.title for card in cards] == ["Комиссия"]


# Фрагмент документа владельца от 28.07.2026, второй список вопросов. Ответы под вопросами
# написаны просто текстом — БЕЗ слова «Ответ:». Прежний разбор давал по ним пустые карточки,
# они считались черновиками, и агент отвечал «в базе нет информации» на вопросы, ответ на
# которые владелец уже написал. Между списками стоит ненумерованный раздел «Кто мы?».
PLAIN_DOCUMENT = """1. Можно ли перенести существующие карточки товаров и остатки в новый кабинет?

Сейчас перенос карточек и остатков не рассматривается: технически этот процесс сопряжён со сложностями.

Для начала работы рекомендуем создать новые карточки товаров в кабинете и вести продажи с нуля.

2. От чьего юридического лица клиент закупает товар у поставщиков?

Клиент закупает товар самостоятельно, от своего юридического лица.

3. Как устроена базовая экономика: 56%, эквайринг, реклама и СПП?

Базовая логика расчётов следующая:

- клиент получает 56% от суммы реализации до СПП;

- отдельно удерживается эквайринг — ориентировочно около 2%.

Кто мы?

Мы — партнёр, который сопровождает подключение селлеров к действующему кабинету Wildberries.

Кабинет работает более 7 лет, оборот за прошлый год — около 3 млрд рублей.
"""


def _by_title(cards, title):
    return next(card for card in cards if card.title == title)


def test_answer_without_the_word_otvet_is_still_an_answer():
    """Жалоба владельца 28.07.2026: «агент очень плохо ищет, а ChatGPT справился идеально».

    ChatGPT показывали документ целиком, а агент видел только карточки со словом «Ответ:» —
    16 дописанных владельцем вопросов были для него пустыми черновиками."""
    cards = iu_knowledge.parse_qa(PLAIN_DOCUMENT)
    approved = iu_knowledge.approved(cards)

    titles = [card.title for card in approved]
    assert "Можно ли перенести существующие карточки товаров и остатки в новый кабинет?" in titles
    assert "От чьего юридического лица клиент закупает товар у поставщиков?" in titles

    card = _by_title(approved, "От чьего юридического лица клиент закупает товар у поставщиков?")
    assert "от своего юридического лица" in card.answer


def test_plain_answer_keeps_its_whole_body():
    cards = iu_knowledge.parse_qa(PLAIN_DOCUMENT)

    card = _by_title(cards, "Как устроена базовая экономика: 56%, эквайринг, реклама и СПП?")
    assert "56% от суммы реализации до СПП" in card.answer
    assert "эквайринг" in card.answer


def test_unnumbered_section_becomes_its_own_card():
    """«Кто мы?» раньше прилипал к ответу на предыдущий вопрос: агент цитировал одну карточку,
    а рассказывал факты из другой."""
    cards = iu_knowledge.parse_qa(PLAIN_DOCUMENT)

    about = _by_title(cards, "Кто мы?")
    assert "более 7 лет" in about.answer
    assert "3 млрд" in about.answer

    economics = _by_title(cards, "Как устроена базовая экономика: 56%, эквайринг, реклама и СПП?")
    assert "7 лет" not in economics.answer


def test_the_word_otvet_still_works_as_before():
    cards = iu_knowledge.parse_qa(DOCUMENT)

    card = _by_title(cards, "Какая комиссия будет у Вас?")
    assert card.answer.startswith("Базовая комиссия")
    assert "Ответ:" not in card.answer
