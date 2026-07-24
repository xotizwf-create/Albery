"""Путь лида ЦЕЛИКОМ — контракт воронки ИУ.

Владелец 24.07.2026: «я устал править по одной правке, каждый раз как будто хожу по кругу»
(96 инженерных задач за 11 дней). Причина не в самих правилах: каждое закрыто своим тестом.
Ломались СТЫКИ между ними — новая защита молча выключала прежнее поведение. Живой пример того
же дня: агент начал заводить сделку с первого сообщения, и из-за этого замолчал сторож анкеты —
три отдельных условия («агент уже писал по сделке», список этапов, отметка по номеру сделки)
каждое поодиночке выглядело правильным.

Здесь один прогон ведёт клиента по всему пути на НАСТОЯЩЕМ коде — заменены только выходы
наружу (CRM, Telegram, модель) — и проверяет инварианты воронки. Любая будущая правка, которая
разорвёт цепочку на любом шаге, уронит этот тест, а не дойдёт до клиента.
"""
from __future__ import annotations

import json

import pytest

DOC = """Условия ИУ — текст для клиента

Всё ниже строки агент отправляет ДОСЛОВНО.

--- ТЕКСТ КЛИЕНТУ ---

Индивидуальные условия снижают комиссию до 12% и дают приоритет в выдаче.

Стоимость — 30 000 ₽ в месяц, первый месяц бесплатно."""

LABELS = {
    "UF_CRM_1784297026": "Ссылка на магазин / бренд WB",
    "UF_CRM_1784297137": "Категории товара",
    "UF_CRM_1784297181": "Оборот на WB сейчас, ₽/мес.",
}

LEAD = {"id": 555, "username": "novyi_lead", "first_name": "Сергей"}


class Portal:
    """Мини-CRM в памяти: воронка 16, поля анкеты, карточки людям.

    Отвечает в тех же форматах, что живые инструменты Albery, — весь код воронки работает
    настоящий, подменена только граница с Битриксом."""

    def __init__(self):
        self.deals: dict[int, dict] = {}
        self.escalations: list[str] = []
        self._next_id = 100

    # --- то, что вызывает агент -------------------------------------------------------------
    def call(self, tool: str, args: dict) -> dict:
        if tool == "create_crm_deal":
            return self._create(args)
        if tool == "update_crm_deal":
            return self._update(args)
        if tool == "get_crm_deal":
            return {"deal": self.deals[int(args["deal_id"])]}
        if tool == "delete_crm_deal":
            self.deals.pop(int(args["deal_id"]), None)
            return {"deleted": True}
        if tool == "list_crm_lead_contacts":
            return {"contacts": [{"deal_id": d["deal_id"],
                                  "username": d["custom_fields"].get("UF_CRM_1784296997", "")}
                                 for d in self.deals.values()]}
        if tool == "notify_iu_group":
            self.escalations.append(str(args.get("text") or ""))
            return {"sent": True, "message_id": len(self.escalations)}
        raise AssertionError(f"сценарий не ожидал вызова {tool}")

    def _create(self, args: dict) -> dict:
        self._next_id += 2
        deal_id = self._next_id
        self.deals[deal_id] = {
            "deal_id": deal_id, "title": args.get("title", ""),
            "stage_id": args.get("stage") or "C16:NEW",
            "custom_fields": dict(args.get("custom_fields") or {}),
        }
        return {"deal_id": deal_id}

    def _update(self, args: dict) -> dict:
        deal = self.deals[int(args["deal_id"])]
        if args.get("stage"):
            deal["stage_id"] = args["stage"]
        deal["custom_fields"].update(args.get("custom_fields") or {})
        return {"updated": True}

    # --- то, что делает «клиент» ------------------------------------------------------------
    def fill_the_form(self, username: str, fields: dict) -> int:
        """CRM-форма создаёт СВОЮ сделку — так устроена анкета Битрикса."""
        return self._create({"title": 'Заполнение CRM-формы "Индивидуальные условия"',
                             "stage": "C16:NEW",
                             "custom_fields": {"UF_CRM_1784296997": username, **fields}})["deal_id"]

    def deals_of(self, username: str) -> list[dict]:
        return [d for d in self.deals.values()
                if d["custom_fields"].get("UF_CRM_1784296997", "").lstrip("@").lower()
                == username.lower()]


class Chat:
    """Всё, что реально ушло клиенту в Telegram."""

    def __init__(self):
        self.messages: list[str] = []

    def html(self, uid, html, plain):
        self.messages.append(plain)
        return True, ""

    def account(self, uid, text, parse_mode=""):
        self.messages.append(text)
        return True, ""

    @property
    def last(self) -> str:
        return self.messages[-1] if self.messages else ""


@pytest.fixture
def funnel(monkeypatch, tmp_path):
    """Воронка целиком: настоящий код агента + подменённые выходы наружу."""
    import tg_agent as tg
    from mcp import context_server as cs

    portal, chat = Portal(), Chat()

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "business": {"C1": {"user_id": 871}},
        "contacts": {LEAD["username"]: {"id": LEAD["id"], "username": LEAD["username"],
                                        "name": LEAD["first_name"]}},
    }), encoding="utf-8")
    monkeypatch.setattr(tg, "STATE_PATH", state_file)
    monkeypatch.setattr(tg, "load_state",
                        lambda: json.loads(state_file.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg, "save_state",
                        lambda s: state_file.write_text(json.dumps(s, ensure_ascii=False),
                                                        encoding="utf-8"))
    # Кэш лидов — модульный: без сброса соседний тест увидел бы чужую воронку.
    monkeypatch.setattr(tg, "_LEADS_CACHE", {"at": 0.0, "map": {}, "ok": False})

    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    monkeypatch.setenv("TG_LEAD_INVITE", "1")
    monkeypatch.setattr(tg, "mcp_call", lambda tool, args: portal.call(tool, args))
    monkeypatch.setitem(cs.TOOLS, "get_crm_deal",
                        {"handler": lambda a: portal.call("get_crm_deal", a)})
    monkeypatch.setitem(cs.TOOLS, "update_crm_deal",
                        {"handler": lambda a: portal.call("update_crm_deal", a)})
    monkeypatch.setitem(cs.TOOLS, "list_company_files",
                        {"handler": lambda a: {"files": [{"name": "Условия ИУ — текст для клиента",
                                                          "google_file_id": "doc-1"}]}})
    monkeypatch.setitem(cs.TOOLS, "get_company_file", {"handler": lambda a: {"content": DOC}})
    monkeypatch.setattr(tg, "_deal_field_labels", lambda: dict(LABELS))

    monkeypatch.setattr(tg, "send_html", chat.html)
    monkeypatch.setattr(tg, "send_as_account", chat.account)
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg, "react", lambda *a, **k: None)
    monkeypatch.setattr(tg, "chat_history", lambda *a, **k: "")
    # Отметка исходящих — по реально отправленному: тогда «первый контакт» в стенде
    # означает ровно то же, что на проде.
    monkeypatch.setattr(tg, "_dialog_out_watermark", lambda d: len(chat.messages))
    monkeypatch.setattr(tg, "_out_messages_after", lambda d, s: 0)

    said: list[str] = []

    def model(prompt, session, toolsets=None):
        """Модель, которая СОБЛЮДАЕТ правила: проверяем свой скелет, а не сочинительство LLM.

        Решение принимаем по СЛОВАМ КЛИЕНТА, а не по промпту: в промпт входят правила, где
        слово «условия» встречается всегда."""
        text = (said[-1] if said else "").lower()
        if any(w in text for w in ("услови", "комисси", "тариф", "дрр", "цен")):
            return tg.TERMS_REQUEST_MARKER
        if any(w in text for w in ("верно", "да", "ок")):
            # Худший случай, ровно как на проде 24.07.2026 у Александра: на подтверждение
            # анкеты модель ТОЖЕ вернула маркер условий. Скелет обязан это пережить.
            return tg.TERMS_REQUEST_MARKER
        return "Здравствуйте! Чем помочь?"

    monkeypatch.setattr(tg, "hermes_answer", model)

    def client_says(text: str) -> None:
        said.append(text)
        tg.maybe_autoreply({"business_connection_id": "C1",
                            "chat": {"id": LEAD["id"], "type": "private"},
                            "from": dict(LEAD), "text": text})

    return type("Funnel", (), {"tg": tg, "portal": portal, "chat": chat,
                               "says": staticmethod(client_says)})


def test_lead_journey_from_hello_to_a_question_beyond_the_terms(funnel):
    """Весь путь клиента одним прогоном. Каждый шаг — то, что владелец требовал отдельно."""
    tg, portal, chat = funnel.tg, funnel.portal, funnel.chat

    # 1. Просто поздоровался — это ещё не лид: поставщиков и знакомых в воронку не берём.
    funnel.says("Здравствуйте")
    assert chat.messages, "человеку отвечаем в любом случае"
    assert portal.deals == {}, "сделку по «здравствуйте» не заводим"

    # 2. Спросил про ИУ — сделка появляется СРАЗУ, до ответа, с его @username.
    funnel.says("Какие условия подключения к ИУ?")
    deals = portal.deals_of(LEAD["username"])
    assert len(deals) == 1, "сделка заводится ровно одна"
    deal = deals[0]
    assert deal["stage_id"] == tg.STAGE_CONTACTED, "ответили — значит «Связались»"

    # 3. Условия ушли ДОСЛОВНО из документа, анкета — в конце того же сообщения.
    answer = chat.last
    assert "Индивидуальные условия снижают комиссию до 12%" in answer, "текст ровно из документа"
    assert "ДОСЛОВНО" not in answer, "служебная шапка документа клиенту не уходит"
    assert tg.LEAD_FORM_URL in answer, "цель — анкета"
    assert answer.index("Индивидуальные условия") < answer.index(tg.LEAD_FORM_URL), \
        "сначала условия, приглашение — в конце"

    # 4. Клиент заполнил анкету: форма создала ВТОРУЮ сделку.
    portal.fill_the_form(LEAD["username"], {"UF_CRM_1784297026": "shop.wb.ru/seller",
                                            "UF_CRM_1784297137": "одежда",
                                            "UF_CRM_1784297181": "30000000"})
    assert len(portal.deals_of(LEAD["username"])) == 2, "дубль от формы — это факт жизни CRM"

    # 5. Сторож замечает анкету САМ: склеивает дубль и начинает сверку без сообщения клиента.
    before = len(chat.messages)
    tg._check_new_forms()
    survived = portal.deals_of(LEAD["username"])
    assert len(survived) == 1, "дубль склеен: у клиента одна сделка"
    assert survived[0]["deal_id"] == deal["deal_id"], "осталась та, которую агент вёл с начала"
    assert survived[0]["stage_id"] == tg.STAGE_FORM_DONE, "этап догнал факт: «Анкета заполнена»"
    assert survived[0]["custom_fields"]["UF_CRM_1784297137"] == "одежда", "данные анкеты перенесены"
    assert len(chat.messages) == before + 1, "сверка ушла сама, ровно одна"
    assert "Вижу анкету:" in chat.last and chat.last.endswith("Всё верно?")
    assert "30 млн" in chat.last, "цифры — из живых полей воронки"

    # 6. Повторный проход сторожа — тишина: те же данные второй раз не сверяем.
    tg._check_new_forms()
    assert "Вижу анкету:" in chat.last and len(chat.messages) == before + 1

    # 7. Клиент подтвердил анкету — разговор обязан идти дальше, а не встать в тупик.
    #    Живой случай Александра (сделка 148): на «Все верно» пришло «Уточню это у команды».
    before = len(chat.messages)
    funnel.says("Все верно")
    assert len(chat.messages) == before + 1, "на подтверждение отвечаем"
    assert tg.TERMS_ASK_HUMAN_REPLY not in chat.last, "подтверждение — не повод дёргать людей"
    assert "вопрос" in chat.last.lower(), "менеджер спрашивает, остались ли вопросы по условиям"
    assert "Индивидуальные условия снижают комиссию" not in chat.last, "документ второй раз не шлём"
    assert not portal.escalations, "людей на подтверждении не беспокоим"

    # И у самого этапа «Анкета заполнена» есть ЖИВОЙ шаг: без него агент вставал в тупик,
    # получая в промпт заглушку «Стадия C16:UC_ANKETA / ждёшь: — ».
    step = tg.funnel_step_block(deal["deal_id"], LEAD["id"])
    assert "Стадия C16:UC_ANKETA" not in step, "этап без шага — это тупик"
    assert "Сверка анкеты" in step

    # 8. Вопрос ПОВЕРХ условий (в документе такого нет) — людям, клиенту одна строка.
    before = len(chat.messages)
    funnel.says("Какой ДРР держать и как происходит управление?")
    assert tg.TERMS_ASK_HUMAN_REPLY in chat.last, "второй документ клиенту не дублируем"
    assert len(chat.messages) == before + 1
    assert portal.escalations, "вопрос обязан уйти живым людям"
    assert "дрр" in portal.escalations[-1].lower()

    # 9. Инвариант всего пути: в воронке ровно одна сделка этого человека.
    assert len(portal.deals_of(LEAD["username"])) == 1


def test_supplier_never_enters_the_funnel(funnel):
    """Инвариант: в воронке только те, кто спрашивал про ИУ."""
    for text in ("Приветствую", "Мы поставщик тканей, интересует закупка",
                 "Скиньте номер бухгалтера"):
        funnel.says(text)

    assert funnel.portal.deals == {}, "болтовня и поставщики воронку не засоряют"
    assert funnel.chat.messages, "но людям отвечаем"


def test_refilled_anketa_is_surveyed_again(funnel):
    """Клиент исправил анкету — сверяем заново: иначе агент подтвердит устаревшие данные."""
    tg, portal, chat = funnel.tg, funnel.portal, funnel.chat
    funnel.says("Какие условия подключения к ИУ?")
    portal.fill_the_form(LEAD["username"], {"UF_CRM_1784297137": "одежда",
                                            "UF_CRM_1784297181": "30000000"})
    tg._check_new_forms()
    assert "30 млн" in chat.last

    deal = portal.deals_of(LEAD["username"])[0]
    deal["custom_fields"]["UF_CRM_1784297181"] = "50000000"      # клиент поправил оборот
    tg._check_new_forms()

    assert "50 млн" in chat.last, "изменённая анкета обязана получить новую сверку"


def test_survey_waits_for_the_anketa_itself(funnel):
    """Сделка агента есть, анкеты нет — сверять нечего. Так сторож не шлёт пустых сообщений."""
    tg, chat = funnel.tg, funnel.chat
    funnel.says("Какие условия подключения к ИУ?")
    before = len(chat.messages)

    tg._check_new_forms()

    assert len(chat.messages) == before, "без данных анкеты сторож молчит"


def test_person_who_only_filled_the_form_is_greeted(funnel):
    """Живой случай (диалог 256942600, 25.07.2026): человек заполнил анкету, в чат не писал —
    и первым сообщением от компании получил голое «Вижу анкету: • Ссылка на магазин…».

    Инвариант вежливости: ПЕРВОЕ сообщение человеку всегда начинается с приветствия, кем бы оно
    ни было отправлено — моделью или кодом."""
    tg, portal, chat = funnel.tg, funnel.portal, funnel.chat
    # Человек в воронке (пришёл из анкеты), агент ему ещё ни разу не писал.
    portal.fill_the_form(LEAD["username"], {"UF_CRM_1784297137": "одежда",
                                            "UF_CRM_1784297181": "30000000"})
    state = json.loads(tg.STATE_PATH.read_text(encoding="utf-8"))
    state["invited"] = {str(LEAD["id"]): "2026-07-25T09:00:00+00:00"}
    tg.STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    tg._check_new_forms()

    assert chat.messages, "сверка должна уйти"
    assert chat.messages[0].startswith("Здравствуйте, Сергей!"), \
        "первое сообщение человеку — с приветствием и по имени"
    assert "Вижу анкету:" in chat.messages[0], "и с самой сверкой, слово в слово"
    assert chat.messages[0].rstrip().endswith("Всё верно?")
