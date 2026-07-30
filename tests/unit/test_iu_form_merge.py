"""Анкета приклеивается к карточке человека, а не заводит вторую.

Владелец 29.07.2026 (со скриншотом канбана, где на него одного висят две карточки): «человек
заходит в тг-бота — его лид попадает в Битрикс; потом он заполняет форму; лид и анкета
соединяются в одну сделку». Уточнение 30.07.2026: подтверждённая анкета переводит сделку
из «Нового клиента» в «Анкету», а дальше этап меняют только человек или ИИ.

Живой случай, на котором всё и проверяется: #266 «Лид Telegram [tg:1451982360] — Александр
Никитенко · @alexxandrn» в 15:26:48 и #268 «Заполнение CRM-формы …» в 15:27:15 с меткой
`utm_content=tg-vfX9MPrcYdNeJwOZ`.
"""
from __future__ import annotations

import iu_form_merge as merge

TOKEN = "vfX9MPrcYdNeJwOZ"

FORM_DEAL = {
    "deal_id": 268,
    "title": 'Заполнение CRM-формы "Индивидуальная настройка от 17 июля"',
    "stage_id": "C16:NEW",
    "fields": {"SOURCE_ID": "WEBFORM", "UTM_CONTENT": f"tg-{TOKEN}", "CONTACT_ID": "18"},
    "custom_fields": {
        "UF_CRM_1784296997": "@alexxandrn",
        "UF_CRM_1784297026": "Teat",
        "UF_CRM_1784297137": "1321",
        "UF_CRM_1784297181": "3000",
        "UF_CRM_1784297221": "30000",
    },
}

TG_DEAL = {
    "deal_id": 266,
    "title": "Лид Telegram [tg:1451982360] — Александр Никитенко · @alexxandrn",
    "stage_id": "C16:NEW",
    "fields": {},
    "custom_fields": {"UF_CRM_1784296997": "alexxandrn"},
}


class FakeCrm:
    def __init__(self, deals):
        self.deals = {int(d["deal_id"]): dict(d) for d in deals}
        self.updated: list[tuple] = []
        self.comments: list[tuple] = []
        self.deleted: list[int] = []

    def list_deals(self, *, category_id, limit):
        """Как отдаёт Битрикс: в СПИСКЕ служебных полей нет.

        Первый живой прогон 29.07.2026 не нашёл ни одной анкеты именно поэтому: `SOURCE_ID`
        и `UTM_CONTENT` видны только в карточке целиком, а тест этого не воспроизводил."""
        return [{k: v for k, v in d.items() if k != "fields"} for d in self.deals.values()]

    def get_deal(self, deal_id):
        return dict(self.deals[int(deal_id)])

    def update_deal(self, deal_id, *, custom_fields=None, stage_id=None):
        self.updated.append((int(deal_id), custom_fields, stage_id))

    def comment(self, deal_id, text):
        self.comments.append((int(deal_id), text))

    def delete_deal(self, deal_id):
        self.deleted.append(int(deal_id))
        self.deals.pop(int(deal_id), None)


class FakeConn:
    """Память сторожа: журнал склеек и таблица токенов."""

    def __init__(self, telegram_id=None, token=TOKEN):
        self.merges: dict[int, dict] = {}
        self.telegram_id = telegram_id
        self.token = token
        self.burned: list[str] = []

    def cursor(self):
        return _Cur(self)


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        text = " ".join(sql.split())
        if text.startswith("SELECT 1 FROM iu_form_merges"):
            self._result = (1,) if int(params[0]) in self.conn.merges else None
        elif text.startswith("INSERT INTO iu_form_merges"):
            self.conn.merges[int(params[0])] = {
                "target": params[1], "telegram_id": params[2], "matched_by": params[3],
                "deleted": params[4], "payload": params[6],
            }
            self._result = None
        elif text.startswith("SELECT * FROM iu_form_tokens"):
            self._result = ({"token": self.conn.token, "telegram_id": self.conn.telegram_id}
                            if self.conn.telegram_id and params[0] == self.conn.token else None)
        elif text.startswith("UPDATE iu_form_tokens SET used_at"):
            self.conn.burned.append(params[1])
            self._result = {"token": params[1]}
        else:  # pragma: no cover
            raise AssertionError(f"неожиданный запрос: {text[:70]}")

    def fetchone(self):
        return self._result


# --- опознание -------------------------------------------------------------------------------

def test_token_wins_over_everything():
    target, how = merge.match_target(FORM_DEAL, [TG_DEAL], telegram_id=1451982360)

    assert (target, how) == (266, "token")


def test_username_works_when_the_mark_did_not_travel():
    """Клиент открыл анкету не по ссылке — метки нет, но ник он вписал сам."""
    form = {**FORM_DEAL, "fields": {"SOURCE_ID": "WEBFORM"}}

    assert merge.match_target(form, [TG_DEAL]) == (266, "username")


def test_username_matching_survives_the_at_sign_and_case():
    """В анкете «@alexxandrn», в карточке «alexxandrn» — это один человек."""
    assert merge.clean_username("@AlexxandrN") == "alexxandrn"
    assert merge.clean_username("https://t.me/alexxandrn") == "alexxandrn"
    assert merge.clean_username(None) == ""


def test_nothing_matched_means_hands_off():
    """Лишняя карточка переживаема, потерянная заявка — нет."""
    stranger = {**TG_DEAL, "deal_id": 300,
                "title": "Лид Telegram [tg:999] — Кто-то · @someone",
                "custom_fields": {"UF_CRM_1784296997": "someone"}}

    assert merge.match_target({**FORM_DEAL, "fields": {}, "custom_fields": {}},
                              [stranger]) == (0, "none")


def test_the_earliest_card_wins():
    """В ранней карточке история переписки; поздние — как раз дубли."""
    late = {**TG_DEAL, "deal_id": 290}

    assert merge.match_target(FORM_DEAL, [late, TG_DEAL], telegram_id=1451982360)[0] == 266


# --- что переносим ---------------------------------------------------------------------------

def test_filled_values_are_not_overwritten():
    """В карточке может лежать то, что человек уточнил менеджеру голосом."""
    target = {**TG_DEAL, "custom_fields": {"UF_CRM_1784297026": "настоящий магазин"}}

    copied = merge.fields_to_copy(FORM_DEAL, target)

    assert "UF_CRM_1784297026" not in copied
    assert copied["UF_CRM_1784297137"] == "1321"


def test_confirmed_form_moves_only_a_new_client_to_the_form_stage(monkeypatch):
    monkeypatch.setattr(merge, "FORM_STAGE_TRANSITION_ENABLED", True)

    assert merge.next_stage_for(TG_DEAL) == "C16:UC_ANKETA"
    assert merge.next_stage_for({**TG_DEAL, "stage_id": "C16:S84294149"}) == ""
    assert merge.next_stage_for({**TG_DEAL, "stage_id": "C16:NDA"}) == ""


def test_form_stage_transition_has_an_emergency_switch(monkeypatch):
    monkeypatch.setattr(merge, "FORM_STAGE_TRANSITION_ENABLED", False)

    assert merge.next_stage_for(TG_DEAL) == ""
    assert merge.next_stage_for({**TG_DEAL, "stage_id": "C16:S84294149"}) == ""


# --- полный проход ---------------------------------------------------------------------------

def test_live_case_merges_and_moves_new_client_to_form_stage(monkeypatch):
    monkeypatch.setattr(merge, "FORM_STAGE_TRANSITION_ENABLED", True)
    crm = FakeCrm([FORM_DEAL, TG_DEAL])
    conn = FakeConn(telegram_id=1451982360)

    stats = merge.run_once(crm=crm, conn=conn)

    assert stats["merged"] == 1
    deal_id, custom, stage = crm.updated[0]
    assert deal_id == 266
    assert custom["UF_CRM_1784297026"] == "Teat"
    assert stage == "C16:UC_ANKETA"
    assert crm.deleted == [268]
    assert conn.burned == [TOKEN]
    assert "анкету" in crm.comments[0][1]
    assert "Этап переведён на «Анкета»" in crm.comments[0][1]


def test_form_never_rolls_back_a_deal_already_advanced_by_human_or_ai(monkeypatch):
    monkeypatch.setattr(merge, "FORM_STAGE_TRANSITION_ENABLED", True)
    advanced = {**TG_DEAL, "stage_id": "C16:NDA"}
    crm = FakeCrm([FORM_DEAL, advanced])
    conn = FakeConn(telegram_id=1451982360)

    stats = merge.run_once(crm=crm, conn=conn)

    assert stats["merged"] == 1
    assert crm.updated[0][0] == 266
    assert crm.updated[0][2] is None
    assert "Этап переведён" not in crm.comments[0][1]


def test_a_second_pass_changes_nothing():
    """Сторож ходит часто и видит одну заявку много раз — перенос обязан случиться один раз."""
    crm = FakeCrm([FORM_DEAL, TG_DEAL])
    conn = FakeConn(telegram_id=1451982360)
    merge.run_once(crm=crm, conn=conn)

    crm.deals[268] = dict(FORM_DEAL)  # как будто удаление не сработало
    stats = merge.run_once(crm=crm, conn=conn)

    assert stats["merged"] == 0
    assert stats["skipped"] == 1
    assert len(crm.updated) == 1


def test_unmatched_form_is_left_untouched():
    crm = FakeCrm([{**FORM_DEAL, "fields": {"SOURCE_ID": "WEBFORM"},
                    "custom_fields": {}}])
    conn = FakeConn()

    stats = merge.run_once(crm=crm, conn=conn)

    assert stats["unmatched"] == 1
    assert crm.deleted == []
    assert crm.updated == []


def test_deletion_is_recorded_with_a_snapshot():
    """Удаление сделки необратимо — содержимое обязано остаться у нас."""
    crm = FakeCrm([FORM_DEAL, TG_DEAL])
    conn = FakeConn(telegram_id=1451982360)

    merge.run_once(crm=crm, conn=conn)

    saved = conn.merges[268]
    assert saved["deleted"] is True
    assert "UF_CRM_1784297181" in saved["payload"]


def test_failed_deletion_still_records_the_merge():
    """Данные уже перенесены: не записать склейку значит перенести их второй раз."""
    class Stubborn(FakeCrm):
        def delete_deal(self, deal_id):
            raise RuntimeError("Битрикс отказал")

    crm = Stubborn([FORM_DEAL, TG_DEAL])
    conn = FakeConn(telegram_id=1451982360)

    stats = merge.run_once(crm=crm, conn=conn)

    assert stats["merged"] == 1
    assert conn.merges[268]["deleted"] is False


def test_form_deal_is_recognised_without_service_fields():
    """Список сделок Битрикса не отдаёт SOURCE_ID — узнаём анкету и по названию."""
    listed = {k: v for k, v in FORM_DEAL.items() if k != "fields"}

    assert merge.is_form_deal(listed) is True
    assert merge.is_form_deal({"title": "Лид Telegram [tg:1] — Кто-то"}) is False
