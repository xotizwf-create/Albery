"""Зеркало Авито тянет только рабочие переписки, а не весь личный ящик владельца.

Живой случай 20.08.2026. Выход в Авито — личный аккаунт владельца, и зеркало на каждом
обходе отправляло в Albery ВСЕ чаты подряд: аренду квартир, продавцов подписок, репетитора
по курсовой, чужие номера телефонов. В кабинете это видят сотрудники — то есть личная
переписка утекала в рабочую систему просто потому, что у зеркала не было границы.

Второе следствие того же дефекта: чистить кабинет было бессмысленно. Удалённые разговоры
возвращались на ближайшем обходе через двадцать секунд, потому что зеркало не помнит,
что их выбросили, — оно просто присылает всё заново.

Граница проходит по принадлежности, а не по тексту: наш разговор — тот, который завела
сама система («написать первым» и его сшивка), либо тот, который уже заведён в базе.
Всё остальное отбивается на входе и в базу не попадает.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def avito(app_module):
    import avito_channel

    return avito_channel


class _NoDb:
    """Поддельное соединение: запись проходит вхолостую, чтобы тест шёл без базы."""

    def __enter__(self): return self
    def __exit__(self, *_e): return False
    def transaction(self): return self
    def cursor(self): return self
    def execute(self, *_a, **_k): return None
    def fetchone(self): return None
    def fetchall(self): return []


@pytest.fixture()
def mirror(avito, monkeypatch):
    """Общая обвязка: источник и сшивка заглушены, приём разговора под наблюдением."""
    seen: dict[str, object] = {"ensured": [], "stored": 0}

    monkeypatch.setattr(avito, "pg_connect", lambda: _NoDb())
    monkeypatch.setattr(avito.store, "ensure_source", lambda *a, **k: None)
    monkeypatch.setattr(avito.store, "ensure_conversation",
                        lambda **kw: seen["ensured"].append(kw) or {"id": 900})
    monkeypatch.setattr(avito, "stitch_outreach_conversation", lambda **kw: None)
    monkeypatch.setattr(avito.store, "find_conversation", lambda **kw: None)
    return seen


def _chuzhoy(**over):
    """Живая строка из ящика владельца: аренда квартиры, к работе отношения не имеет."""
    payload = {
        "account": "main",
        "external_chat_id": "u2i-lichnyy-arenda",
        "display_name": "Этажи Казань Аренда",
        "messages": [{"external_message_id": "m1", "text": "Здравствуйте! Остались вопросы?",
                      "author_type": "client"}],
    }
    payload.update(over)
    return payload


def test_a_stranger_chat_never_reaches_the_cabinet(avito, mirror, monkeypatch):
    """Главный дефект: личный чат заводил разговор в рабочей системе."""
    monkeypatch.delenv("AVITO_MIRROR_ONLY_KNOWN", raising=False)

    answer = avito.ingest_inbound(_chuzhoy())

    assert answer["skipped"] == "outside_mirror_scope"
    assert answer["stored_messages"] == 0
    assert not mirror["ensured"], "разговор заводить было нельзя"


def test_the_reply_to_our_outreach_is_let_through(avito, mirror, monkeypatch):
    """Продавец генератора ответил на наше «Ещё продаёте?» — это наша переписка."""
    monkeypatch.setattr(avito, "stitch_outreach_conversation",
                        lambda **kw: {"action": "adopted", "conversation_id": 648,
                                      "external_chat_id": "u2i-nastoyaschiy"})

    answer = avito.ingest_inbound(_chuzhoy(external_chat_id="u2i-nastoyaschiy",
                                           listing={"id": "4297041572",
                                                    "title": "Генератор бензиновый sdmo LX6000"}))

    assert "skipped" not in answer
    assert mirror["ensured"], "сшитый разговор обязан пройти"


def test_an_already_known_chat_keeps_flowing(avito, mirror, monkeypatch):
    """Сшивка срабатывает один раз; дальше разговор узнают по тому, что он уже заведён."""
    monkeypatch.setattr(avito.store, "find_conversation",
                        lambda **kw: {"id": 648, "state_version": 3})

    answer = avito.ingest_inbound(_chuzhoy(external_chat_id="u2i-nastoyaschiy"))

    assert "skipped" not in answer
    assert mirror["ensured"], "известный разговор обязан пройти"


def test_the_known_chat_is_looked_up_for_this_account_and_channel(avito, mirror, monkeypatch):
    """Спрашиваем базу адресно: чужой источник не должен считаться «нашим» разговором."""
    asked: dict[str, object] = {}
    monkeypatch.setattr(avito.store, "find_conversation",
                        lambda **kw: asked.update(kw) or None)

    avito.ingest_inbound(_chuzhoy())

    assert asked["source_key"] == avito.SOURCE_KEY
    assert asked["business_connection_id"] == "main"
    assert asked["external_chat_id"] == "u2i-lichnyy-arenda"


def test_the_gate_can_be_opened_back_up(avito, mirror, monkeypatch):
    """Отключаемо: если однажды понадобится полное зеркало, это переменная, а не правка кода."""
    monkeypatch.setenv("AVITO_MIRROR_ONLY_KNOWN", "0")

    answer = avito.ingest_inbound(_chuzhoy())

    assert "skipped" not in answer
    assert mirror["ensured"], "при открытом шлюзе проходит всё"


def test_a_skipped_chat_does_not_look_like_a_failure(avito, mirror, monkeypatch):
    """Воркер читает ответ и печатает сводку — пропуск обязан быть штатным исходом, не ошибкой."""
    answer = avito.ingest_inbound(_chuzhoy())

    assert answer["received"] == 1
    assert answer["stored_messages"] == 0
    assert answer["stitched"] is None
    assert answer["conversation_id"] is None
