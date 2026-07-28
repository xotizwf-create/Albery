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
