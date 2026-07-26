"""Канал «открытая линия»: бот отвечает в карточке сделки, человек забирает и отдаёт управление.

Требование владельца 26.07.2026: сотрудник в любой момент перехватывает разговор и в любой
момент возвращает его боту. Здесь это зафиксировано тестами — вместе с ловушками, на которых
такой канал ломается: бот выключает сам себя своим же ответом, служебные записи линии принимает
за реплики человека, а слово «бот» от КЛИЕНТА считает командой управления.

Строки `entity_id` / `entity_data_1` взяты с живого портала (разведочный прогон 26.07.2026,
чат 2724, сделка 162).
"""
from __future__ import annotations

import contextlib

import pytest

import openline_agent as ol


LIVE_ENTITY_ID = "albery_probe|6|probe-client-1|114"
LIVE_ENTITY_DATA_1 = "Y|DEAL|162|N|N|20|1785059887|0|0|0"

CLIENT_ID = 114
OPERATOR_ID = 16
BOT_ID = 86
CHAT_ID = 2724
DEAL_ID = 162
FUNNEL = 16


class FakeBitrix:
    """Портал: отдаёт чат линии и запоминает всё, что бот попытался отправить."""

    def __init__(self, *, deal_category=FUNNEL, entity_type="LINES"):
        self.calls: list[tuple[str, dict]] = []
        self.deal_category = deal_category
        self.entity_type = entity_type

    def call(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "im.dialog.get":
            return {"result": {
                "id": CHAT_ID, "entity_type": self.entity_type,
                "entity_id": LIVE_ENTITY_ID, "entity_data_1": LIVE_ENTITY_DATA_1,
            }}
        if method == "crm.deal.get":
            return {"result": {"ID": str(DEAL_ID), "CATEGORY_ID": str(self.deal_category)}}
        return {"result": True}

    def sent_to_client(self):
        return [p for m, p in self.calls
                if m == "imbot.message.add" and str(p.get("SYSTEM", "N")).upper() != "Y"]

    def system_notes(self):
        return [p for m, p in self.calls
                if m == "imbot.message.add" and str(p.get("SYSTEM", "N")).upper() == "Y"]


class FakeDB:
    """Состояние диалогов в памяти — с той же семантикой, что и таблица openline_dialogs."""

    def __init__(self):
        self.rows: dict[int, dict] = {}

    def __call__(self):
        return self._conn()

    @contextlib.contextmanager
    def _conn(self):
        store = self

        class Cur:
            def __init__(self):
                self._row = None

            def execute(self, sql, params=None):
                params = params or ()
                low = " ".join(sql.lower().split())
                if low.startswith("select"):
                    self._row = store.rows.get(int(params[0]))
                elif "insert into openline_dialogs (chat_id, bot_active" in low:
                    chat_id, bot_active, by, reason = int(params[0]), params[1], params[2], params[3]
                    row = store.rows.setdefault(chat_id, {"chat_id": chat_id, "bot_active": True})
                    row.update({"bot_active": bool(bot_active), "control_by": by,
                                "control_reason": reason})
                else:
                    chat_id = int(params[0])
                    row = store.rows.setdefault(chat_id, {"chat_id": chat_id, "bot_active": True})
                    row.setdefault("deal_id", params[4])

            def fetchone(self):
                return self._row

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Conn:
            def cursor(self):
                return Cur()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        yield Conn()

    def bot_active(self, chat_id=CHAT_ID, default=True):
        return self.rows.get(chat_id, {}).get("bot_active", default)


def event(author_id, text="", bot_id=BOT_ID):
    return ol.Event(chat_id=CHAT_ID, author_id=author_id, text=text, message_id=1, bot_id=bot_id)


def run(bitrix, db, ev, *, brain=None, journal=None, agent_enabled=None):
    return ol.handle_event(
        ev, bitrix=bitrix, db=db,
        brain=brain or (lambda dialog, text, history: "Здравствуйте! Готов помочь по условиям."),
        journal=journal, bot_ids={BOT_ID}, funnel_category_id=FUNNEL,
        agent_enabled=agent_enabled,
    )


# --- разбор живых строк портала ----------------------------------------------------------------

def test_client_and_deal_are_read_from_the_live_chat_fields():
    ids = ol.parse_entity_id(LIVE_ENTITY_ID)
    assert ids["connector"] == "albery_probe" and ids["line_id"] == 6
    assert ids["client_user_id"] == CLIENT_ID
    crm = ol.parse_entity_data_1(LIVE_ENTITY_DATA_1)
    assert crm["crm_entity_type"] == "DEAL" and crm["crm_entity_id"] == DEAL_ID
    assert crm["session_id"] == 20


def test_a_chat_that_is_not_an_open_line_is_left_alone():
    bitrix, db = FakeBitrix(entity_type="CHAT"), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "привет"))
    assert decision.action == ol.IGNORE
    assert bitrix.sent_to_client() == []


# --- основной сценарий: бот ведёт, человек перехватывает, человек возвращает --------------------

def test_bot_answers_the_client_while_it_leads_the_dialog():
    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "Расскажите про условия"))
    assert decision.action == ol.ANSWER
    sent = bitrix.sent_to_client()
    assert len(sent) == 1 and sent[0]["DIALOG_ID"] == f"chat{CHAT_ID}"
    assert "условия" in sent[0]["MESSAGE"].lower()


def test_an_employee_message_takes_the_dialog_over_and_silences_the_bot():
    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(OPERATOR_ID, "Здравствуйте, дальше отвечу я."))
    assert decision.action == ol.TAKEOVER
    assert db.bot_active() is False
    assert bitrix.sent_to_client() == []          # перехват клиенту не показываем
    assert bitrix.system_notes()                  # но сотрудники видят пометку


def test_after_takeover_the_client_message_gets_no_bot_answer():
    bitrix, db = FakeBitrix(), FakeDB()
    run(bitrix, db, event(OPERATOR_ID, "дальше я"))
    bitrix.calls.clear()
    decision = run(bitrix, db, event(CLIENT_ID, "а когда подключение?"))
    assert decision.action == ol.IGNORE
    assert bitrix.sent_to_client() == []


@pytest.mark.parametrize("command", ["/бот", "/bot", "бот, продолжай", "Верни бота", "БОТ"])
def test_employee_returns_control_to_the_bot_with_a_command(command):
    bitrix, db = FakeBitrix(), FakeDB()
    run(bitrix, db, event(OPERATOR_ID, "дальше я"))
    decision = run(bitrix, db, event(OPERATOR_ID, command))
    assert decision.action == ol.RESUME
    assert db.bot_active() is True


def test_the_bot_answers_again_after_control_is_returned():
    bitrix, db = FakeBitrix(), FakeDB()
    run(bitrix, db, event(OPERATOR_ID, "дальше я"))
    run(bitrix, db, event(OPERATOR_ID, "/бот"))
    bitrix.calls.clear()
    decision = run(bitrix, db, event(CLIENT_ID, "так что по условиям?"))
    assert decision.action == ol.ANSWER
    assert len(bitrix.sent_to_client()) == 1


def test_the_return_command_is_never_relayed_to_the_client_as_a_message():
    bitrix, db = FakeBitrix(), FakeDB()
    run(bitrix, db, event(OPERATOR_ID, "дальше я"))
    bitrix.calls.clear()
    run(bitrix, db, event(OPERATOR_ID, "/бот"))
    assert bitrix.sent_to_client() == []


# --- ловушки, на которых такой канал ломается --------------------------------------------------

def test_the_bots_own_reply_does_not_count_as_a_human_takeover():
    """Ответ бота приходит тем же событием. Без этой проверки бот глушил бы сам себя."""
    bitrix, db = FakeBitrix(), FakeDB()
    run(bitrix, db, event(CLIENT_ID, "здравствуйте"))
    decision = run(bitrix, db, event(BOT_ID, "Здравствуйте! Готов помочь."))
    assert decision.action == ol.IGNORE
    assert db.bot_active() is True


def test_service_messages_of_the_line_are_not_a_takeover():
    """«Создана новая сделка», «Обращение направлено на …» приходят от отправителя 0."""
    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(0, "[b]Создана новая сделка[/b]"))
    assert decision.action == ol.IGNORE
    assert db.bot_active() is True


def test_the_word_bot_from_the_client_is_a_normal_message_not_a_control_command():
    """Клиент пишет «бот» — это разговор, а не пульт управления каналом."""
    bitrix, db = FakeBitrix(), FakeDB()
    run(bitrix, db, event(OPERATOR_ID, "дальше я"))
    bitrix.calls.clear()
    decision = run(bitrix, db, event(CLIENT_ID, "/бот"))
    assert decision.action == ol.IGNORE      # ведёт человек — клиент бота не включает
    assert db.bot_active() is False


def test_a_client_message_without_text_is_ignored_but_keeps_the_bot_in_charge():
    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "   "))
    assert decision.action == ol.IGNORE
    assert bitrix.sent_to_client() == []
    assert db.bot_active() is True


def test_a_deal_from_another_funnel_is_left_to_humans():
    bitrix, db = FakeBitrix(deal_category=2), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "привет"))
    assert decision.action == ol.IGNORE
    assert bitrix.sent_to_client() == []


def test_the_cabinet_switch_stops_the_bot():
    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "привет"), agent_enabled=lambda: False)
    assert decision.action == ol.IGNORE
    assert bitrix.sent_to_client() == []


def test_a_broken_model_hands_the_dialog_to_people_instead_of_going_silent():
    def broken(dialog, text, history):
        raise RuntimeError("провайдер вернул 503")

    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "когда подключите?"), brain=broken)
    assert decision.action == ol.TAKEOVER
    assert db.bot_active() is False
    assert bitrix.sent_to_client() == []       # клиенту отписки не шлём
    assert bitrix.system_notes()               # люди видят, что бот сошёл с дистанции


def test_an_empty_model_answer_also_hands_the_dialog_to_people():
    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "?"), brain=lambda dialog, text, history: "   ")
    assert decision.action == ol.TAKEOVER
    assert db.bot_active() is False
    assert bitrix.sent_to_client() == []


# --- журнал --------------------------------------------------------------------------------------

def test_both_sides_of_the_conversation_reach_the_journal():
    records = []
    bitrix, db = FakeBitrix(), FakeDB()
    run(bitrix, db, event(CLIENT_ID, "здравствуйте"),
        journal=lambda **kw: records.append(kw))
    directions = [(r["direction"], r["author"]) for r in records]
    assert ("in", "client") in directions
    assert ("out", "bot") in directions


def test_an_internal_marker_never_reaches_the_client():
    """Живой прогон 26.07.2026: клиенту ушла голая строка «ПОКАЖИ_УСЛОВИЯ».

    В Telegram-ветке маркер перехватывается и превращается в дословную отправку условий; в линии
    такого действия ещё нет, значит разговор идёт людям, а не служебные слова клиенту."""
    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "покажите условия"),
                   brain=lambda dialog, text, history: "ПОКАЖИ_УСЛОВИЯ")
    assert decision.action == ol.TAKEOVER
    assert db.bot_active() is False
    assert bitrix.sent_to_client() == []
    assert "ПОКАЖИ_УСЛОВИЯ" in bitrix.system_notes()[0]["MESSAGE"]


@pytest.mark.parametrize("answer", [
    "НУЖЕН_ЧЕЛОВЕК", "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ", "Конечно! ПОКАЖИ_УСЛОВИЯ",
])
def test_every_known_marker_is_held_back(answer):
    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "вопрос"),
                   brain=lambda dialog, text, history: answer)
    assert decision.action == ol.TAKEOVER
    assert bitrix.sent_to_client() == []


def test_the_marker_list_matches_the_brain():
    """Маркеры заданы в мозге; расхождение списков означало бы, что новый маркер утечёт клиенту."""
    import tg_agent

    assert set(ol.INTERNAL_MARKERS) == {
        tg_agent.TERMS_REQUEST_MARKER, tg_agent.ESCALATION_MARKER,
        tg_agent.SIDE_ESCALATION_MARKER,
    }


def test_the_line_bot_id_comes_from_the_environment_not_from_the_shared_state_file(monkeypatch):
    """26.07.2026: id бота линии положили в общий state-файл приложения. Приложение переписывает
    его целиком на каждом событии — ключ пропал, приложение не нашло основного бота и подставило
    себе бота линии (bot_id стал 116 вместо 24). Окружение обязано быть главнее."""
    monkeypatch.setenv("B24_OPENLINE_BOT_ID", "116")
    assert ol.bot_id({"openline_bot_id": "999"}) == "116"
    assert ol.bot_id({}) == "116"
    monkeypatch.delenv("B24_OPENLINE_BOT_ID")
    assert ol.bot_id({"openline_bot_id": "999"}) == "999"
    assert ol.bot_id({}) == ""


def test_a_broken_journal_never_blocks_the_answer():
    def broken(**kwargs):
        raise RuntimeError("БД недоступна")

    bitrix, db = FakeBitrix(), FakeDB()
    decision = run(bitrix, db, event(CLIENT_ID, "здравствуйте"), journal=broken)
    assert decision.action == ol.ANSWER
    assert len(bitrix.sent_to_client()) == 1
