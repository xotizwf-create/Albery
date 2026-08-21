"""Вход в Авито переживает потерю компьютера: слепок сессии лежит на сервере.

Браузер обязан работать с домашнего адреса — датацентровый Авито не пускает. Но пока
профиль браузера на машине человека был ЕДИНСТВЕННОЙ копией входа, любая переустановка
системы означала капчу и код из SMS заново, а до тех пор канал молчал.

Слепок сессии равносилен доступу к аккаунту, поэтому в базу он кладётся только
зашифрованным: дамп базы, попавший не в те руки, не должен отдавать чужой Авито.
Открытым текстом не сохраняем никогда — лучше отказ, чем тихо лежащий ключ от аккаунта.
"""
from __future__ import annotations

import json

import pytest

STATE = {
    "cookies": [{"name": "sessid", "value": "секрет-сессии", "domain": ".avito.ru", "path": "/"}],
    "origins": [{"origin": "https://www.avito.ru",
                 "localStorage": [{"name": "auth", "value": "токен-входа"}]}],
}


@pytest.fixture()
def avito(app_module):
    import avito_channel

    return avito_channel


@pytest.fixture()
def key(monkeypatch):
    from cryptography.fernet import Fernet

    value = Fernet.generate_key().decode()
    monkeypatch.setenv("AVITO_SESSION_KEY", value)
    return value


class _Store:
    """Поддельная база: держит один слепок и отдаёт его обратно, как настоящая."""

    def __init__(self):
        self.row = None
        self.saved_plaintext = None

    def __enter__(self): return self
    def __exit__(self, *_e): return False
    def transaction(self): return self
    def cursor(self): return self

    def execute(self, sql, params=()):
        statement = " ".join(str(sql).split())
        if statement.startswith("INSERT INTO avito_sessions"):
            self.row = {"payload": params[1], "saved_at": "сейчас", "avito_user_id": params[3]}
            self.saved_plaintext = params[1]
        self._select = statement.startswith("SELECT payload")

    def fetchone(self):
        if getattr(self, "_select", False):
            return self.row
        return {"slug": "main", "saved_at": "сейчас", "avito_user_id": "198797068"}


def test_the_session_never_reaches_the_database_in_the_clear(avito, key, monkeypatch):
    """Главный инвариант: в базе лежит шифртекст, а не куки."""
    store = _Store()
    monkeypatch.setattr(avito, "pg_connect", lambda: store)

    avito.save_session_state("main", STATE, avito_user_id="198797068")

    blob = bytes(store.saved_plaintext)
    assert b"sessid" not in blob
    assert "секрет-сессии".encode("utf-8") not in blob
    assert "токен-входа".encode("utf-8") not in blob


def test_a_saved_session_comes_back_intact(avito, key, monkeypatch):
    """Восстановление обязано отдать ровно то, что положили, — иначе вход не поднимется."""
    store = _Store()
    monkeypatch.setattr(avito, "pg_connect", lambda: store)

    avito.save_session_state("main", STATE)
    restored = avito.load_session_state("main")

    assert restored["state"] == STATE


def test_without_a_key_nothing_is_stored_at_all(avito, monkeypatch):
    """Лучше честный отказ, чем ключ от аккаунта, тихо лежащий открытым текстом."""
    monkeypatch.delenv("AVITO_SESSION_KEY", raising=False)
    monkeypatch.setattr(avito, "pg_connect", lambda: pytest.fail("до базы дойти было нельзя"))

    with pytest.raises(avito.SessionKeyMissing):
        avito.save_session_state("main", STATE)


def test_a_changed_key_does_not_pass_for_a_valid_session(avito, key, monkeypatch):
    """Ключ сменили — слепок больше не наш. Молча отдать мусор нельзя."""
    from cryptography.fernet import Fernet

    store = _Store()
    monkeypatch.setattr(avito, "pg_connect", lambda: store)
    avito.save_session_state("main", STATE)

    monkeypatch.setenv("AVITO_SESSION_KEY", Fernet.generate_key().decode())

    assert avito.load_session_state("main") is None


def test_an_absent_session_is_not_an_error(avito, key, monkeypatch):
    """Аккаунт заведён, но входа ещё не было — это штатное «пусто», а не сбой."""
    store = _Store()
    monkeypatch.setattr(avito, "pg_connect", lambda: store)

    assert avito.load_session_state("main") is None


def test_the_stored_blob_is_valid_json_under_the_key(avito, key, monkeypatch):
    """Слепок должен расшифровываться в JSON: иначе восстановление упадёт у человека."""
    from cryptography.fernet import Fernet

    store = _Store()
    monkeypatch.setattr(avito, "pg_connect", lambda: store)
    avito.save_session_state("main", STATE)

    decrypted = Fernet(key.encode()).decrypt(bytes(store.saved_plaintext))

    assert json.loads(decrypted.decode("utf-8")) == STATE
