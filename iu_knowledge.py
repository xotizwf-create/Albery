"""База знаний ИУ как карточки фактов: агент отвечает только тем, что в ней есть.

Владелец 26.07.2026: «мы просто наполним нашу систему информацией, агент будет отвечать исходя
из неё». Документ владелец правит сам на Google Drive, без деплоя — синк Drive → база уже
работает, поэтому здесь только разбор и поиск.

Почему карточки, а не сплошной текст. Раньше источником был ОДИН документ условий, который
уходил клиенту целиком и дословно. На вопрос, ответа на который в нём нет («какой ДРР держать?»,
«как происходит управление кабинетом?»), агент высылал тот же документ второй раз или уходил в
«уточню у команды». Карточка — атомарный факт с собственным идентификатором: её можно найти,
процитировать, указать источником в `TurnPlan` и проверить, что ответ на неё опирается.

Формат блока в документе владельца (разделитель — строка `---`):

    ### Комиссия
    Ответ: Единая комиссия 44%. В неё входят комиссия WB, логистика, хранение и приёмка.
    Проще: Вы отдаёте 44 рубля с каждых 100 рублей продаж, больше ничего доплачивать не нужно.
    Этап: любой
    Человек: если клиент спорит с расчётом

Обязательны только заголовок и «Ответ»: владелец пишет ФАКТ, а не заготовку под поиск.
Угадывать, какими словами спросит клиент, не нужно — за это отвечает семантический поиск
(`iu_embeddings`): «сколько вы берёте» находит «Комиссию» без списка синонимов.

«Проще» нужна для повторного вопроса: владелец 26.07.2026 — «если вопрос такой же, человек не
понял, то нужно попробовать объяснить простым языком».

Необязательное поле «Спрашивают так» поддержано для случая, когда владелец хочет ЖЁСТКО
привязать формулировку к карточке (точное вхождение даёт скор 1.0 мимо любой семантики). Это
исключение для спорных мест, а не обычный способ заполнения.

Карточка без ответа или с пометкой [ЗАПОЛНИТЬ] считается черновиком и клиенту недоступна: пустая
заготовка, выданная за факт, хуже честной передачи человеку.

Слой чистый: на входе текст документа и запрос, на выходе карточки со скором. Поиск лексический
и работает без базы; место для эмбеддингов оставлено параметром `rerank` — Ступень B добавляется
не переписыванием, а передачей второго скорера.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DRAFT_MARK = "[ЗАПОЛНИТЬ]"
ANY_STAGE = "любой"

_BLOCK_SPLIT_RE = re.compile(r"^\s*-{3,}\s*$", re.M)
_TITLE_RE = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$", re.M)
_FIELD_RE = re.compile(
    r"^\s*(Спрашивают\s+так|Ответ|Проще|Этап|Человек)\s*:\s*(.*)$",
    re.I | re.M,
)

# Слова, которые есть в любом вопросе и потому ничего не различают. Без их отсева «а какая у вас
# комиссия?» и «а какие у вас сроки?» получают одинаковый скор по общим словам.
_STOPWORDS = frozenset("""
а и в во на с со у к по за из от до о об для же ли бы то это тот та те как что чем чём кто
где когда куда почему зачем сколько какой какая какие какое каков какова я ты вы мы он она они
мне меня нам нас вам вас ему ей им их мой моя мои ваш ваша ваши наш наша наши есть быть будет
ну вот там тут да нет не ни или либо если так тоже еще ещё уже только просто пожалуйста
скажите подскажите расскажите хочу нужно надо можно
всё все таки итоге вообще всё-таки значит вроде типа
""".split())

# Грубый стеммер: русской морфологии здесь нет, но окончания срезать необходимо — иначе
# «комиссия» и «комиссии» считаются разными словами и карточка не находится.
_ENDINGS = (
    "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ей", "ой", "ий", "ый", "ая", "яя",
    "ое", "ее", "ые", "ие", "ов", "ев", "ам", "ям", "ах", "ях", "ом", "ем", "ию", "ия", "ии",
    "ью", "ья", "ье", "ут", "ют", "ат", "ят", "ет", "ит", "ла", "ло", "ли", "ть", "ся", "сь",
    "а", "я", "о", "е", "у", "ю", "ы", "и", "й", "ь",
)
_MIN_STEM = 4

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.I)


def _stem(word: str) -> str:
    value = word.casefold()
    for ending in _ENDINGS:
        if len(value) - len(ending) >= _MIN_STEM and value.endswith(ending):
            return value[: -len(ending)]
    return value


def stems(text: str) -> frozenset[str]:
    """Значимые основы слов. Стоп-слова отсеиваются до стемминга."""
    return frozenset(
        _stem(word)
        for word in _WORD_RE.findall(str(text or ""))
        if word.casefold() not in _STOPWORDS and len(word) > 1
    )


@dataclass(frozen=True)
class Card:
    """Один утверждённый факт с собственным идентификатором."""

    id: str
    title: str
    answer: str
    aliases: tuple[str, ...] = ()
    simple: str = ""
    stage: str = ANY_STAGE
    human_when: str = ""

    @property
    def approved(self) -> bool:
        """Готова ли карточка к показу клиенту."""
        body = self.answer.strip()
        return bool(body) and DRAFT_MARK not in body

    def text_for(self, *, simple: bool = False) -> str:
        """Текст факта. `simple=True` — упрощённое объяснение, если владелец его написал."""
        if simple and self.simple.strip():
            return self.simple.strip()
        return self.answer.strip()


def _slug(title: str) -> str:
    """Идентификатор карточки из заголовка. Он попадает в `source_ids` и в трассу."""
    value = re.sub(r"[^\w\s-]", "", str(title or "").casefold(), flags=re.U)
    value = re.sub(r"[\s_]+", "-", value.strip())
    return value.strip("-") or "card"


def parse_cards(document: str) -> tuple[Card, ...]:
    """Разобрать документ владельца в карточки. Кривой блок пропускается, а не роняет разбор."""
    out: list[Card] = []
    seen: dict[str, int] = {}
    for block in _BLOCK_SPLIT_RE.split(str(document or "")):
        if not block.strip():
            continue
        title_match = _TITLE_RE.search(block)
        fields = {name.casefold(): value for name, value in
                  ((m.group(1), m.group(2)) for m in _FIELD_RE.finditer(block))}
        # Многострочный «Ответ:» — обычное дело: владелец пишет абзацем. Берём всё до
        # следующего известного поля.
        answer = _multiline(block, "Ответ")
        simple = _multiline(block, "Проще")
        if not title_match and not answer:
            continue
        title = title_match.group(1).strip() if title_match else ""
        if not title:
            continue
        raw_aliases = next((v for k, v in fields.items() if k.startswith("спрашивают")), "")
        aliases = tuple(a.strip() for a in re.split(r"[,;|]", raw_aliases) if a.strip())
        base = _slug(title)
        seen[base] = seen.get(base, 0) + 1
        card_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
        out.append(Card(
            id=card_id,
            title=title,
            answer=answer.strip(),
            aliases=aliases,
            simple=simple.strip(),
            stage=(fields.get("этап") or ANY_STAGE).strip() or ANY_STAGE,
            human_when=(fields.get("человек") or "").strip(),
        ))
    return tuple(out)


def _multiline(block: str, name: str) -> str:
    """Значение поля до следующего известного поля или конца блока."""
    pattern = re.compile(rf"^\s*{name}\s*:\s*(.*)$", re.I | re.M)
    match = pattern.search(block)
    if not match:
        return ""
    rest = block[match.end():]
    stop = _FIELD_RE.search(rest)
    tail = rest[: stop.start()] if stop else rest
    return (match.group(1) + "\n" + tail).strip()


def approved(cards) -> tuple[Card, ...]:
    """Только то, что владелец действительно заполнил."""
    return tuple(card for card in cards if card.approved)


def drafts(cards) -> tuple[Card, ...]:
    """Незаполненные карточки — их видит владелец в отчёте, но никогда не видит клиент."""
    return tuple(card for card in cards if not card.approved)


def _coverage(query_stems: frozenset[str], field: str) -> float:
    """Какая доля значимых слов запроса нашлась в поле карточки."""
    if not query_stems:
        return 0.0
    field_stems = stems(field)
    if not field_stems:
        return 0.0
    return len(query_stems & field_stems) / len(query_stems)


def score_card(query: str, card: Card) -> float:
    """Насколько карточка отвечает на запрос: 0..1.

    Формулировка клиента весит больше заголовка, а заголовок — больше тела ответа. Полное
    вхождение одной из клиентских формулировок считается точным попаданием."""
    value = " ".join(str(query or "").split()).casefold()
    if not value:
        return 0.0
    for alias in card.aliases:
        item = alias.casefold().strip()
        if item and item in value:
            return 1.0

    query_stems = stems(value)
    alias_hit = max((_coverage(query_stems, alias) for alias in card.aliases), default=0.0)
    title_hit = _coverage(query_stems, card.title)
    body_hit = _coverage(query_stems, f"{card.answer} {card.simple}")
    return max(alias_hit, 0.9 * title_hit, 0.6 * body_hit)


@dataclass(frozen=True)
class Found:
    """Карточка и её скор под конкретный вопрос."""

    card: Card
    score: float


def search(query: str, cards, *, limit: int = 4, floor: float = 0.15,
           rerank=None) -> tuple[Found, ...]:
    """Найти карточки под вопрос клиента.

    `rerank` — необязательный второй скорер (место для эмбеддингов, Ступень B).

    Порядок здесь принципиален: скорер получает ВСЕ утверждённые карточки, а порог отсечения
    применяется уже после него. Смысл семантики в том, чтобы находить карточку, которую лексика
    не нашла вовсе («сколько это займёт» → «Сроки подключения»); если сначала отсечь по
    лексическому скору, семантический слой сможет только переставлять уже найденное, то есть
    ровно то, ради чего он и нужен, окажется невозможным."""
    hits = [Found(card, score_card(query, card)) for card in approved(cards)]
    if rerank is not None:
        try:
            reranked = rerank(query, hits)
            if reranked:
                hits = list(reranked)
        except Exception:  # noqa: BLE001 — семантика не обязана работать, лексика уже нашла
            pass
    hits = [hit for hit in hits if hit.score >= floor]
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return tuple(hits[:limit])


def retrieval_score(found) -> float:
    """Число для порога уверенности: насколько уверенно нашлась лучшая карточка."""
    return max((hit.score for hit in found), default=0.0)


def sources_text(found, *, simple: bool = False) -> str:
    """Блок источников для промпта и для механической проверки опоры на них.

    Идентификатор идёт рядом с текстом: модель обязана назвать его в `source_ids`, и выдумать
    его она не может — контракт хода сверяет ссылки с тем, что реально показали."""
    parts = []
    for hit in found:
        body = hit.card.text_for(simple=simple)
        parts.append(f"[{hit.card.id}] {hit.card.title}\n{body}")
    return "\n\n".join(parts)


def offered_ids(found) -> tuple[str, ...]:
    """Идентификаторы показанных карточек — их и только их разрешено называть источником."""
    return tuple(hit.card.id for hit in found)


def human_required(found) -> str:
    """Условие владельца «здесь нужен человек», если оно стоит на найденной карточке.

    Это УСЛОВИЕ, а не запрет: «если клиент спорит с расчётом» не означает, что человек нужен
    на каждый вопрос про комиссию. Выполнено ли оно, видно только из сообщения клиента, поэтому
    условие уходит в промпт, а решение принимает модель."""
    for hit in found:
        if hit.card.human_when:
            return hit.card.human_when
    return ""


# Безусловные формулировки: тут владелец запретил отвечать самому в принципе, и код обязан
# исполнить это сам, не спрашивая модель.
_ALWAYS = ("всегда", "да", "обязательно", "любой вопрос", "все вопросы")


def always_human(found) -> str:
    """Условие, которое НЕ зависит от сообщения клиента, — его принуждает код."""
    for hit in found:
        value = hit.card.human_when.strip().casefold()
        if value and any(value.startswith(mark) for mark in _ALWAYS):
            return hit.card.human_when
    return ""
