"""Закрытые каналы и закрытые групповые чаты Telegram — «глаза» агента на аккаунте.

Задача владельца 05.08.2026: агент должен уметь смотреть ЗАКРЫТЫЕ каналы и закрытые групповые
чаты аккаунта @AlberyAIManager. До этой правки такой возможности не было ни в одном месте:
у закрытого чата нет @username, поэтому у него нет ни ссылки t.me/<имя>, ни веб-превью
t.me/s/<имя>, а get_tg_news читал ровно эти превью. MTProto-сессия умела только перечислить
диалоги и собрать посты каналов для недельного обзора — прочитать конкретный чат было нечем,
сослаться на закрытый чат в списке наблюдения тоже было нельзя (normalize_channel его отбрасывал).

Тесты держат телефонную линию к Telegram закрытой: клиент подменяется фейком, поэтому проверяется
именно наша логика — разметка «закрытый», поиск чата по id и по названию, московское время,
маршрут get_tg_news через сессию и внятные отказы, когда сессии нет.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# --- фейковый telethon ---------------------------------------------------------------------

class FakeEntity:
    def __init__(self, entity_id, title=None, username=None, participants_count=None,
                 first_name=None, broadcast=None, megagroup=None):
        self.id = entity_id
        if title is not None:
            self.title = title
        if first_name is not None:
            self.first_name = first_name
        self.username = username
        self.participants_count = participants_count
        if broadcast is not None:
            self.broadcast = broadcast
        if megagroup is not None:
            self.megagroup = megagroup


class FakeDialog:
    def __init__(self, entity, name, kind, unread=0, date=None):
        self.entity = entity
        self.name = name
        self.is_channel = kind in ("channel", "group_super")
        self.is_group = kind in ("group", "group_super")
        self.unread_count = unread
        self.date = date or datetime(2026, 8, 5, 7, 0, tzinfo=timezone.utc)

    @property
    def id(self):
        return self.entity.id


class FakeMessage:
    def __init__(self, message_id, text, when, sender=None, media=None, photo=None):
        self.id = message_id
        self.text = text
        self.date = when
        self.sender = sender
        self.media = media
        self.photo = photo
        self.reply_to_msg_id = None
        self.post_author = None


class FakeClient:
    def __init__(self, dialogs, messages=None):
        self._dialogs = dialogs
        self._messages = messages or {}
        self.search_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def iter_dialogs(self, limit=None):
        async def gen():
            for dialog in self._dialogs[:limit]:
                yield dialog
        return gen()

    def iter_messages(self, entity, limit=None, search=None):
        self.search_calls.append(search)
        rows = self._messages.get(entity.id, [])
        if search:
            rows = [m for m in rows if search.lower() in (m.text or "").lower()]

        async def gen():
            for message in rows[:limit]:
                yield message
        return gen()

    async def get_entity(self, name):
        raise ValueError(f"No user has {name!r} as username")


CLOSED_CHANNEL = FakeEntity(-1001111111111, title="Закрытый клуб селлеров",
                            participants_count=340, broadcast=True)
CLOSED_GROUP = FakeEntity(-1002222222222, title="Оперативка Албери",
                          participants_count=12, megagroup=True)
PUBLIC_CHANNEL = FakeEntity(-1003333333333, title="WB Новости", username="wbnews",
                            broadcast=True)
PERSON = FakeEntity(555, first_name="Дмитрий", username="griaznov_d")

DIALOGS = [
    FakeDialog(CLOSED_CHANNEL, "Закрытый клуб селлеров", "channel", unread=4),
    FakeDialog(CLOSED_GROUP, "Оперативка Албери", "group_super", unread=2),
    FakeDialog(PUBLIC_CHANNEL, "WB Новости", "channel"),
    FakeDialog(PERSON, "Дмитрий", "private"),
]

MESSAGES = {
    CLOSED_CHANNEL.id: [
        FakeMessage(31, "Комиссия WB на FBS вырастет с 1 сентября",
                    datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)),
        FakeMessage(30, "Разбор новых правил приёмки",
                    datetime(2026, 8, 4, 9, 30, tzinfo=timezone.utc)),
    ],
    CLOSED_GROUP.id: [
        FakeMessage(12, "Отгрузку перенесли на четверг",
                    datetime(2026, 8, 5, 6, 15, tzinfo=timezone.utc),
                    sender=FakeEntity(16, first_name="Александр")),
        FakeMessage(11, "", datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
                    sender=FakeEntity(30, first_name="Наталья"), media=object(), photo=object()),
    ],
    PUBLIC_CHANNEL.id: [
        FakeMessage(7, "Открытая новость про маркетплейсы",
                    datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)),
    ],
}


@pytest.fixture
def userbot(monkeypatch, tmp_path):
    import tg_userbot

    session = tmp_path / ".tg_userbot.session"
    session.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(tg_userbot, "SESSION_FILE", session)
    monkeypatch.setattr(tg_userbot, "_probe_authorized", lambda: True)
    monkeypatch.setattr(tg_userbot, "_AUTH_PROBE", {})
    client = FakeClient(DIALOGS, MESSAGES)
    monkeypatch.setattr(tg_userbot, "_client", lambda: client)
    tg_userbot.last_client = client
    return tg_userbot


@pytest.fixture
def no_session(monkeypatch, tmp_path):
    import tg_userbot

    monkeypatch.setattr(tg_userbot, "SESSION_FILE", tmp_path / "absent.session")
    monkeypatch.setattr(tg_userbot, "_AUTH_PROBE", {})
    return tg_userbot


@pytest.fixture
def half_login(monkeypatch, tmp_path):
    """Файл сессии есть, но вход не завершён — так выглядит прерванная попытка входа."""
    import tg_userbot

    session = tmp_path / ".tg_userbot.session"
    session.write_text("sqlite-but-not-signed-in", encoding="utf-8")
    monkeypatch.setattr(tg_userbot, "SESSION_FILE", session)
    monkeypatch.setattr(tg_userbot, "_probe_authorized", lambda: False)
    monkeypatch.setattr(tg_userbot, "_AUTH_PROBE", {})
    return tg_userbot


# --- перечисление чатов --------------------------------------------------------------------

class TestListing:
    def test_closed_channel_and_group_are_marked_closed(self, userbot):
        rows = {r["name"]: r for r in userbot.list_dialogs()}

        assert rows["Закрытый клуб селлеров"]["closed"] is True
        assert rows["Оперативка Албери"]["closed"] is True, "закрытая группа тоже закрыта"
        assert rows["WB Новости"]["closed"] is False, "у публичного канала есть t.me/wbnews"
        assert rows["Дмитрий"]["closed"] is False, "личная переписка — не «закрытый чат»"

    def test_only_closed_filter_returns_channels_and_groups(self, userbot):
        rows = userbot.list_dialogs(only_closed=True)

        assert {r["name"] for r in rows} == {"Закрытый клуб селлеров", "Оперативка Албери"}
        assert {r["type"] for r in rows} == {"channel", "group"}

    def test_row_carries_what_the_owner_needs_to_reference_a_closed_chat(self, userbot):
        row = next(r for r in userbot.list_dialogs() if r["name"] == "Закрытый клуб селлеров")

        assert row["id"] == -1001111111111, "id — единственный способ сослаться на закрытый чат"
        assert row["unread"] == 4 and row["participants"] == 340
        assert row["last_message_at"] == "05.08.2026 10:00", "время московское, не UTC"

    def test_kind_filter(self, userbot):
        assert {r["type"] for r in userbot.list_dialogs(kinds=["group"])} == {"group"}


# --- чтение конкретного чата ---------------------------------------------------------------

class TestReading:
    def test_closed_channel_is_read_by_id(self, userbot):
        out = userbot.read_chat("-1001111111111")

        assert out["chat"]["closed"] is True
        assert [m["text"] for m in out["messages"]] == [
            "Разбор новых правил приёмки",
            "Комиссия WB на FBS вырастет с 1 сентября",
        ], "сообщения по возрастанию времени — так их читает человек"
        assert out["messages"][-1]["date"] == "05.08.2026 13:00", "МСК = UTC+3"

    def test_closed_group_is_read_by_title_and_shows_authors(self, userbot):
        out = userbot.read_chat("Оперативка Албери")

        assert out["chat"]["id"] == -1002222222222
        assert [m["from"] for m in out["messages"]] == ["Наталья", "Александр"]
        assert out["messages"][0]["media"] == "фото", "сообщение без текста, но с картинкой — видно"

    def test_search_inside_a_closed_chat(self, userbot):
        out = userbot.read_chat("Закрытый клуб селлеров", query="комиссия")

        assert [m["id"] for m in out["messages"]] == [31]
        assert userbot.last_client.search_calls[-1] == "комиссия"

    def test_period_filter_cuts_older_messages(self, userbot, monkeypatch):
        real_now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(userbot, "datetime",
                            type("D", (), {"now": staticmethod(lambda tz=None: real_now)}))

        out = userbot.read_chat("-1001111111111", since_days=1)

        assert [m["id"] for m in out["messages"]] == [31], "вчерашний пост в сутки не входит"

    def test_unknown_chat_explains_what_to_do(self, userbot):
        with pytest.raises(LookupError) as err:
            userbot.read_chat("Канал которого нет")

        assert "list_telegram_chats" in str(err.value) or "приглашени" in str(err.value)

    def test_without_a_session_the_refusal_names_the_fix(self, no_session):
        with pytest.raises(RuntimeError) as err:
            no_session.read_chat("-1001111111111")

        assert "не подключена" in str(err.value)
        assert "tg_userbot_login" in str(err.value), "агент обязан сказать, ЧТО сделать"

    def test_unfinished_login_does_not_count_as_a_session(self, half_login):
        """telethon создаёт .session при первом же подключении — файл ещё не значит вход.

        05.08.2026 прерванная попытка входа оставила такой файл. Проверка «файл есть» после
        этого рапортовала бы о готовности, и вместо честного «сессии нет» человек получил бы
        внутреннюю ошибку на первом же запросе."""
        assert half_login.session_ready() is False

        with pytest.raises(RuntimeError) as err:
            half_login.read_chat("-1001111111111")

        assert "не подключена" in str(err.value)

    def test_authorization_probe_is_cached(self, monkeypatch, tmp_path):
        """Проверка авторизации — сетевой вызов; он не должен идти на каждый чих."""
        import tg_userbot

        session = tmp_path / ".tg_userbot.session"
        session.write_text("fake", encoding="utf-8")
        monkeypatch.setattr(tg_userbot, "SESSION_FILE", session)
        monkeypatch.setattr(tg_userbot, "_AUTH_PROBE", {})
        calls = []
        monkeypatch.setattr(tg_userbot, "_probe_authorized",
                            lambda: calls.append(1) or True)

        assert tg_userbot.session_ready() and tg_userbot.session_ready()

        assert len(calls) == 1, "второй вызов обязан прийти из кэша"


# --- источник недельного обзора ------------------------------------------------------------

class TestCollectPosts:
    def test_closed_channel_gets_into_the_digest_source(self, userbot):
        chats, problems = userbot.collect_posts(since_days=7)

        assert problems == []
        assert [c["name"] for c in chats] == ["Закрытый клуб селлеров", "WB Новости"], \
            "по умолчанию каналы, группы — только по требованию"
        assert chats[0]["closed"] is True
        assert chats[0]["posts"][0]["date"] == "04.08.2026 12:30", "старые выше, время МСК"

    def test_watchlist_can_name_a_closed_chat_by_id(self, userbot):
        chats, _ = userbot.collect_posts(only_names=["-1001111111111"], since_days=7)

        assert [c["id"] for c in chats] == [-1001111111111]

    def test_groups_join_only_when_asked(self, userbot):
        chats, _ = userbot.collect_posts(since_days=7, include_groups=True)

        assert "Оперативка Албери" in [c["name"] for c in chats]

    def test_label_tells_a_closed_chat_from_a_public_one(self, userbot):
        sections, _ = userbot.fetch_posts(since_days=7)
        labels = [label for label, _ in sections]

        assert any("закрытый канал, id -1001111111111" in label for label in labels)
        assert any("t.me/wbnews" in label for label in labels)


# --- список наблюдения ---------------------------------------------------------------------

class TestWatchlist:
    def test_closed_chat_id_is_accepted(self):
        import tg_agent

        assert tg_agent.normalize_channel("-1001111111111") == "-1001111111111"

    def test_public_names_still_work_and_junk_is_still_rejected(self):
        import tg_agent

        assert tg_agent.normalize_channel("https://t.me/wbnews") == "wbnews"
        assert tg_agent.normalize_channel("t.me/+AbCdEf") is None
        assert tg_agent.normalize_channel("не канал") is None


# --- инструменты агента --------------------------------------------------------------------

class TestMcpTools:
    def test_tools_are_registered(self, ctx):
        for name in ("list_telegram_chats", "read_telegram_chat", "join_telegram_chat"):
            assert callable(ctx.TOOLS[name]["handler"]), name

    def test_closed_chats_are_never_public(self, ctx):
        """Это всё, что видит живой человек в своём Telegram — не для клиентских коннекторов."""
        for name in ("list_telegram_chats", "read_telegram_chat", "join_telegram_chat"):
            assert name in ctx.OWNER_ONLY_TOOL_NAMES, name

    def test_descriptions_teach_the_route(self, ctx):
        listing = ctx.TOOLS["list_telegram_chats"]["description"]
        reading = ctx.TOOLS["read_telegram_chat"]["description"]

        assert "ЗАКРЫТЫЕ" in listing, "агент должен понимать, что это единственный путь к ним"
        assert "read_telegram_chat" in listing, "список -> чтение по id"
        assert "list_telegram_chats" in reading

    def test_list_tool_counts_closed_chats(self, ctx, userbot):
        out = ctx.tool_list_telegram_chats({})

        assert out["closed_count"] == 2
        assert out["by_type"] == {"channel": 2, "group": 1, "private": 1}

    def test_read_tool_returns_messages_of_a_closed_channel(self, ctx, userbot):
        out = ctx.tool_read_telegram_chat({"chat": "-1001111111111", "limit": 5})

        assert out["count"] == 2 and out["chat"]["closed"] is True
        assert "московское" in out["note"]

    def test_read_tool_requires_a_chat(self, ctx):
        with pytest.raises(ctx.McpError):
            ctx.tool_read_telegram_chat({})

    def test_missing_session_is_a_clear_answer_not_a_crash(self, ctx, no_session):
        with pytest.raises(ctx.McpError) as err:
            ctx.tool_read_telegram_chat({"chat": "-1001111111111"})

        assert "не подключена" in str(err.value)

    def test_news_tool_reads_closed_channels_through_the_session(self, ctx, userbot, monkeypatch):
        """Главный разрыв: раньше новости шли ТОЛЬКО из публичных превью t.me/s/,
        которых у закрытого канала не существует."""
        monkeypatch.setattr(ctx, "_tg_watchlist", lambda: [])

        out = ctx.tool_get_tg_news({"days": 7})

        closed = [c for c in out["channels"] if c["closed"]]
        assert [c["channel"] for c in closed] == ["Закрытый клуб селлеров"]
        assert closed[0]["chat_id"] == -1001111111111
        assert "@AlberyAIManager" in out["source"]

    def test_news_tool_falls_back_to_public_previews_without_a_session(self, ctx, no_session,
                                                                       monkeypatch):
        monkeypatch.setattr(ctx, "_tg_watchlist", lambda: ["wbnews", "-1001111111111"])
        import tg_digest

        monkeypatch.setattr(tg_digest, "fetch_channel_posts",
                            lambda name, since, **kw: (["[01.08 10:00] пост"], None))
        monkeypatch.setattr(ctx, "ttl_cache_get", lambda key: None)

        out = ctx.tool_get_tg_news({"days": 7})

        assert [c["channel"] for c in out["channels"]] == ["wbnews"]
        assert any("-1001111111111" in p and "сессией аккаунта" in p for p in out["problems"]), \
            "закрытый чат в списке не должен молча исчезать — надо сказать, чего не хватает"
