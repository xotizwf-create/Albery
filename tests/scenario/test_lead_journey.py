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
    "UF_CRM_1784297181": "Оборот на WB сейчас, млн ₽/мес.",
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
        if "ИСТОЧНИКИ" in prompt and "ВОПРОСЫ" in prompt:
            # Разбор вопросов по источникам: про комиссию в документе есть, про ДРР — нет.
            rows = []
            for line in prompt.split("ВОПРОСЫ:")[1].strip().splitlines():
                q = line.split(".", 1)[-1].strip()
                if "комисси" in q.lower():
                    rows.append({"вопрос": q, "ответ": "Комиссия 12% — и в неё уже входит "
                                                       "приоритет в выдаче.", "источник": "условия"})
                else:
                    rows.append({"вопрос": q, "ответ": "НЕТ_ОТВЕТА", "источник": ""})
            import json as _json
            return _json.dumps(rows, ensure_ascii=False)
        """Модель, которая СОБЛЮДАЕТ правила: проверяем свой скелет, а не сочинительство LLM.

        Решение принимаем по СЛОВАМ КЛИЕНТА, а не по промпту: в промпт входят правила, где
        слово «условия» встречается всегда."""
        text = (said[-1] if said else "").lower()
        if "хочу подключ" in text:
            return "Помогу подключиться."
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




def test_supplier_never_enters_the_funnel(funnel):
    """Инвариант: в воронке только те, кто спрашивал про ИУ."""
    for text in ("Приветствую", "Мы поставщик тканей, интересует закупка",
                 "Скиньте номер бухгалтера"):
        funnel.says(text)

    assert funnel.portal.deals == {}, "болтовня и поставщики воронку не засоряют"
    assert funnel.chat.messages, "но людям отвечаем"




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


# --- путь до денег: реквизиты → договор → подписание → счёт → подключение --------------------

def test_money_path_never_loses_the_next_step(funnel):
    """Владелец 25.07.2026: логика уже в бою, значит путь до денег обязан быть под тестом.

    Проверяем не отдельные шаги, а ЦЕПОЧКУ: на каждом этапе агент знает, чего ждёт и что делает,
    называет конкретное действие или инструмент, и нигде не сваливается в запасной сценарий.
    Считается настоящим funnel_next_step — тем же, что уходит агенту в промпт."""
    tg = funnel.tg
    R, N, S = tg.CONTRACT_REQUISITES_FIELD, tg.CONTRACT_NUMBER_FIELD, tg.SIGNING_FIELD

    # (этап, что уже собрано в сделке, что обязано прозвучать в шаге)
    path = [
        ("C16:S84294149", {}, "send_terms"),                       # условия ещё не отправляли
        ("C16:S84294149", {R: "ИНН 7704123456, ООО «Ромашка»"}, "send_contract"),
        ("C16:NDA", {R: "ИНН 7704123456", N: "25.07.2026"}, "create_bitrix_task"),
        ("C16:UC_SGZRVS", {R: "ИНН", N: "25.07.2026"}, "notify_client_when_task_done"),
        ("C16:PREPAYMENT_INVOIC", {R: "ИНН", N: "25.07.2026"}, "бухгалтер"),
        ("C16:EXECUTING", {R: "ИНН", N: "25.07.2026"}, "C16:S84294150"),
        ("C16:UC_YA6VN0", {R: "ИНН", N: "25.07.2026"}, "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ"),
        ("C16:S84294150", {R: "ИНН", N: "25.07.2026"}, "C16:CONNECTED"),
        ("C16:CONNECTED", {R: "ИНН", N: "25.07.2026"}, "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ"),
    ]

    for stage, fields, expected in path:
        step = tg.funnel_next_step({"deal_id": 200, "stage_id": stage, "custom_fields": fields})

        assert "шаг не описан" not in step["step"], f"{stage}: этап без шага — это тупик"
        assert step["need"] and step["need"] != "—", f"{stage}: агент не знает, чего ждёт"
        assert expected in step["action"], (
            f"{stage}: в шаге нет «{expected}» — действие потеряно. Шаг: {step['action'][:120]}")


def test_payment_is_never_confirmed_by_the_client_alone(funnel):
    """Деньги подтверждает бухгалтер. «Я оплатил» — не деньги на счету, и стадию это не двигает."""
    tg = funnel.tg
    step = tg.funnel_next_step({"deal_id": 200, "stage_id": "C16:PREPAYMENT_INVOIC",
                                "custom_fields": {}})

    assert "не деньги на счету" in step["action"]
    assert "бухгалтер" in step["need"].lower()


def test_contract_step_demands_requisites_first(funnel):
    """Договор не собирается из воздуха: сначала реквизиты, и агент об этом сказан прямо."""
    tg = funnel.tg
    without = tg.funnel_next_step({"deal_id": 200, "stage_id": "C16:S84294149",
                                   "custom_fields": {}}, terms_sent_to_client=True)

    assert "реквизит" in without["action"].lower(), "агент обязан сначала попросить реквизиты"

    with_req = tg.funnel_next_step({"deal_id": 200, "stage_id": "C16:S84294149",
                                    "custom_fields": {tg.CONTRACT_REQUISITES_FIELD: "ИНН"}})
    assert "send_contract" in with_req["action"], "реквизиты есть — собираем договор"


def test_every_live_stage_of_the_funnel_has_a_step(funnel):
    """Инвариант охвата: ни один этап живой воронки не оставляет агента без инструкций.

    Список этапов — тот же, что в Битриксе на 25.07.2026. Добавили этап в CRM и не описали шаг —
    падает здесь, а не у клиента."""
    tg = funnel.tg
    stages = ("C16:NEW", "C16:CONTACTED", "C16:UC_ANKETA", "C16:S84294149", "C16:NDA",
              "C16:UC_SGZRVS", "C16:PREPAYMENT_INVOIC", "C16:EXECUTING", "C16:UC_YA6VN0",
              "C16:S84294150", "C16:CONNECTED", "C16:WON", "C16:NOT_FIT", "C16:LOSE",
              "C16:APOLOGY")
    fallback = []
    for stage in stages:
        step = tg.funnel_next_step({"deal_id": 200, "stage_id": stage, "custom_fields": {}})
        if "шаг не описан" in step["step"]:
            fallback.append(stage)

    assert fallback == [], f"этапы без шага: {fallback}"


def test_closed_deals_are_not_pushed_further(funnel):
    """Клиент отказался — агент не дожимает. Это про репутацию: навязчивость дороже сделки.

    Все четыре закрывающих этапа работали по запасному сценарию до 25.07.2026."""
    tg = funnel.tg
    for stage in ("C16:NOT_FIT", "C16:LOSE"):
        step = tg.funnel_next_step({"deal_id": 200, "stage_id": stage, "custom_fields": {}})
        assert "НЕ дожимай" in step["action"], stage
        assert "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ" in step["action"], f"{stage}: вернуть в работу решают люди"

    won = tg.funnel_next_step({"deal_id": 200, "stage_id": "C16:WON", "custom_fields": {}})
    assert "ничего не продавай" in won["action"]

    apology = tg.funnel_next_step({"deal_id": 200, "stage_id": "C16:APOLOGY",
                                   "custom_fields": {}})
    assert "по своей инициативе не пишем" in apology["action"]
