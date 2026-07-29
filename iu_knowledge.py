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

import math
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


#: Раздел «Вопрос — ответ» владелец ведёт списком, а не карточками: «1. Вопрос?» на своей
#: строке, под ним «Ответ: …» и дальше свободный текст с примерами и перечислениями. Формат
#: другой, но смысл тот же самый, поэтому он превращается в те же карточки — и поиск,
#: цитирование и ссылки на источник работают без единой правки.
_QA_QUESTION_RE = re.compile(r"^\s*\d+[.)]\s*(?:\d+[.)]\s*)?(.+?)\s*$", re.M)
#: Служебная шапка, которую добавляет синк с Google Drive, вопросом не является.
_QA_SERVICE_RE = re.compile(r"^\s*(источник|обновлено[^:]*|тип)\s*:", re.I)
#: Заголовок раздела без номера: «Кто мы?», «Важно». Владелец пишет их между списками вопросов,
#: и без отдельного разбора такой раздел молча прилипал к ответу на предыдущий вопрос — агент
#: цитировал одну карточку, а рассказывал факты из другой.
_QA_HEADING_RE = re.compile(r"^\s*(\S[^\n]{0,58})\s*$", re.M)
_QA_HEADING_STOP = ("-", "—", "–", "•", "*", "#")


def _is_qa_heading(line: str) -> bool:
    """Похожа ли строка на заголовок раздела, а не на фразу внутри ответа.

    Мерки нарочно узкие: ошибиться сюда — значит откусить у карточки ответ и превратить её в
    черновик, то есть потерять её для агента. Заголовок короткий, без завершающего знака, не
    пункт списка и не начало нумерации."""
    value = line.strip()
    if not value or value[0].isdigit() or value.startswith(_QA_HEADING_STOP):
        return False
    if value.endswith((".", ":", ";", ",", "!")):
        return False
    return len(value) <= 58 and len(value.split()) <= 6


def parse_qa(document: str) -> tuple[Card, ...]:
    """Разобрать раздел «Вопрос — ответ» в карточки.

    Вопрос-заголовок часто содержит несколько формулировок подряд — владелец пишет их так,
    как спрашивают клиенты («Какая комиссия будет у Вас? Сколько я буду получать?»). Первая
    становится названием карточки, остальные — точными формулировками для поиска.

    **Слово «Ответ:» не обязательно.** Владелец 28.07.2026 дописал в документ 16 вопросов,
    ответы под которыми написаны просто текстом, — и все они молча стали черновиками, то есть
    агент их не видел вовсе. Отвечал он при этом «в базе нет информации», хотя ответ владелец
    уже написал. Поэтому ответом считается ВЕСЬ текст под вопросом; «Ответ:» остаётся
    поддержанной, но не обязательной пометкой.
    """

    text = str(document or "")
    matches = [m for m in _QA_QUESTION_RE.finditer(text) if not _QA_SERVICE_RE.match(m.group(0))]
    out: list[Card] = []
    seen: dict[str, int] = {}

    def add(title: str, answer: str, aliases: tuple[str, ...] = ()) -> None:
        base = _slug(title)
        seen[base] = seen.get(base, 0) + 1
        out.append(Card(
            id=base if seen[base] == 1 else f"{base}-{seen[base]}",
            title=title,
            answer=answer.strip(),
            aliases=aliases,
        ))

    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        if not heading:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end]
        answer_match = re.search(r"^\s*ответ\s*:\s*(.*)$", body, re.I | re.M)
        if answer_match:
            body = (answer_match.group(1) + "\n" + body[answer_match.end():])
        # Ненумерованный раздел между вопросами («Кто мы?») — самостоятельная карточка,
        # а не хвост чужого ответа.
        answer, section = _split_trailing_section(body)
        wordings = [part.strip() + "?" for part in heading.split("?") if part.strip()]
        add(wordings[0] if wordings else heading, answer, tuple(wordings[1:]))
        if section:
            add(*section)
    return tuple(out)


def _split_trailing_section(body: str) -> tuple[str, tuple[str, str] | None]:
    """Отделить от ответа ненумерованный раздел, если владелец начал его прямо под ответом."""
    lines = body.splitlines()
    for position in range(1, len(lines)):
        if lines[position - 1].strip() or not _is_qa_heading(lines[position]):
            continue  # заголовок раздела всегда стоит после пустой строки
        head = "\n".join(lines[:position]).strip()
        tail = "\n".join(lines[position + 1:]).strip()
        if not head or not tail:
            continue  # без ответа у вопроса и без текста у раздела делить нечего
        return head, (lines[position].strip(), tail)
    return body.strip(), None


#: Заголовок раздела в документе условий: короткая строка, заканчивающаяся двоеточием
#: («Что входит в условия:», «Условия входа:»). Пункт перечисления заголовком не считается.
_TERMS_SECTION_RE = re.compile(r"^\s*(\S[^\n]{0,78}):\s*$")
_TERMS_LIST_RE = re.compile(r"^\s*(?:\d{1,2}[.)]|[-—–•*])\s+")
#: Как назвать вводную часть документа — ту, что идёт до первого раздела.
TERMS_INTRO_TITLE = "Условия программы"


def parse_terms(client_text: str) -> tuple[Card, ...]:
    """Разобрать клиентскую часть документа «Условия ИУ» в карточки.

    Зачем. Этот документ агент умел только ОТПРАВЛЯТЬ дословно по действию `send_terms`, а
    отвечать по нему не мог: в карточки он не попадал. Из-за этого агент не знал того, что в
    нём написано, — «выплаты в течение 3 рабочих дней», «еженедельный отчёт комиссионера»,
    «персональный менеджер», размер взноса, — и на прямые вопросы про выплаты и вход отвечал
    «уточню у коллег». Агент Албери те же факты называет, потому что читает оба документа
    папки (живой разбор 29.07.2026, диалог 16).

    Ожидается уже ВЫРЕЗАННАЯ клиентская часть (то, что ниже строки-маркера), иначе в знания
    попала бы инструкция владельца самому себе. Резать документ — забота вызывающего кода:
    этот слой чистый.

    Разделы владелец пишет строкой с двоеточием, под ней перечисление. Каждый раздел —
    отдельная карточка: так на вопрос про выплаты цитируется раздел про условия, а не весь
    документ. Пометка [ЗАПОЛНИТЬ] превращает карточку в черновик тем же правилом, что и везде.
    """
    lines = str(client_text or "").replace("\r\n", "\n").split("\n")
    sections: list[tuple[str, list[str]]] = [(TERMS_INTRO_TITLE, [])]
    for line in lines:
        match = _TERMS_SECTION_RE.match(line)
        if match and not _TERMS_LIST_RE.match(line):
            sections.append((match.group(1).strip(), []))
            continue
        sections[-1][1].append(line)

    out: list[Card] = []
    seen: dict[str, int] = {}
    for title, body in sections:
        answer = "\n".join(body).strip()
        if not answer or not title:
            continue
        base = _slug(title)
        seen[base] = seen.get(base, 0) + 1
        out.append(Card(
            id=base if seen[base] == 1 else f"{base}-{seen[base]}",
            title=title,
            answer=answer,
        ))
    return tuple(out)


def approved(cards) -> tuple[Card, ...]:
    """Только то, что владелец действительно заполнил."""
    return tuple(card for card in cards if card.approved)


def drafts(cards) -> tuple[Card, ...]:
    """Незаполненные карточки — их видит владелец в отчёте, но никогда не видит клиент."""
    return tuple(card for card in cards if not card.approved)


def _coverage(query_stems: frozenset[str], field: str, weights: dict | None = None) -> float:
    """Какая доля ЗНАЧИМОСТИ вопроса нашлась в поле карточки.

    Раньше считалась доля слов, и все слова весили одинаково: «ваша компания на осно» — три
    слова, из них совпало одно, значит 0.33, хотя именно «осно» и определяет тему вопроса.
    Теперь вес слова — его редкость по базе: то, что встречается в каждой карточке, почти
    ничего не значит, а редкий термин («осно», «дрр», «усн») тянет скор вверх.
    """

    if not query_stems:
        return 0.0
    field_stems = stems(field)
    if not field_stems:
        return 0.0
    weights = weights or {}
    total = sum(weights.get(stem, 1.0) for stem in query_stems)
    if total <= 0:
        return 0.0
    hit = sum(weights.get(stem, 1.0) for stem in query_stems & field_stems)
    return hit / total


def idf_weights(cards) -> dict[str, float]:
    """Вес каждого слова по редкости в базе: чем в меньшем числе карточек, тем важнее."""

    documents = [f"{card.title} {' '.join(card.aliases)} {card.answer} {card.simple}"
                 for card in approved(cards)]
    if not documents:
        return {}
    seen: dict[str, int] = {}
    for document in documents:
        for stem in stems(document):
            seen[stem] = seen.get(stem, 0) + 1
    total = len(documents)
    return {stem: math.log(1.0 + total / count) for stem, count in seen.items()}


def score_card(query: str, card: Card, weights: dict | None = None) -> float:
    """Насколько карточка отвечает на запрос: 0..1.

    Формулировка клиента весит больше заголовка, а заголовок — больше тела ответа. Полное
    вхождение одной из клиентских формулировок считается точным попаданием.

    Сигналы складываются, а не соперничают: совпадение и в заголовке, и в теле ответа — более
    веский довод, чем совпадение только в одном месте. Раньше брался максимум, и вопрос,
    раскиданный по обоим полям, оценивался как половинчатый.
    """

    value = " ".join(str(query or "").split()).casefold()
    if not value:
        return 0.0
    for alias in card.aliases:
        item = alias.casefold().strip()
        if item and item in value:
            return 1.0

    query_stems = stems(value)
    if not query_stems:
        return 0.0
    alias_hit = max((_coverage(query_stems, alias, weights) for alias in card.aliases), default=0.0)
    title_hit = _coverage(query_stems, card.title, weights)
    body_hit = _coverage(query_stems, f"{card.answer} {card.simple}", weights)
    best = max(alias_hit, 0.95 * title_hit, 0.85 * body_hit)
    # Второй сигнал добавляет немного: совпасть и в заголовке, и в теле — довод в пользу
    # карточки, но не повод считать её ответом на вопрос.
    rest = sorted((alias_hit, title_hit, body_hit), reverse=True)[1:]
    for extra in rest:
        best += (1.0 - best) * 0.15 * extra

    # Тема вопроса держится на редких словах. Если совпали только ходовые («какой», «товар»,
    # «условия»), карточка похожа на ответ лишь на вид: так «WB не против?» попадал в карточку
    # про экономику, а «вы какие-то неторопливые» — в стоимость подключения. Отвечать невпопад
    # хуже, чем честно передать человеку, поэтому такие совпадения приглушаются.
    if weights:
        значимые = [weights.get(stem, 1.0) for stem in query_stems]
        порог = max(significant_weight(weights), 0.0)
        field_stems = stems(f"{card.title} {' '.join(card.aliases)} {card.answer} {card.simple}")
        matched_rare = [stem for stem in query_stems & field_stems
                        if weights.get(stem, 1.0) >= порог]
        if значимые and not matched_rare:
            best *= 0.4
    return min(1.0, best)


def significant_weight(weights: dict) -> float:
    """Граница «редкого» слова: медиана весов базы."""

    values = sorted(weights.values())
    if not values:
        return 0.0
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


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
    weights = idf_weights(cards)
    hits = [Found(card, score_card(query, card, weights)) for card in approved(cards)]
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


def everything(query: str, cards, *, rerank=None) -> tuple[Found, ...]:
    """Вся утверждённая база, отсортированная по близости к вопросу.

    Владелец 28.07.2026: «нужен такой же агент, как Албери, чтобы я дополнял базу и агент её
    видел». Албери отвечает хорошо не поэтому, что у него лучше поиск, а потому что он видит
    документы целиком и сам решает, есть ли в них ответ. Здесь то же самое: база ИУ — это
    ~2 900 токенов, она помещается в промпт полностью, поэтому выбирать карточку до модели
    незачем. Замер на живых вопросах из переписок: подбор одной карточки отвечал на 1 из 8,
    полная база — на 4 из 8, а оставшиеся 4 не отвечает никто, потому что ответа в базе нет.

    Порядок сохраняем по лексическому скору: самое близкое к вопросу идёт первым, и модель
    читает его раньше остального. Скор больше ничего не отсекает — он остаётся в трассе."""
    weights = idf_weights(cards)
    hits = [Found(card, score_card(query, card, weights)) for card in approved(cards)]
    if rerank is not None:
        try:
            reranked = rerank(query, hits)
            if reranked:
                hits = list(reranked)
        except Exception:  # noqa: BLE001 — семантика не обязана работать, порядок и так есть
            pass
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return tuple(hits)


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
    """Условия владельца «здесь нужен человек» по показанным карточкам.

    Это УСЛОВИЕ, а не запрет: «если клиент спорит с расчётом» не означает, что человек нужен
    на каждый вопрос про комиссию. Выполнено ли оно, видно только из сообщения клиента, поэтому
    условие уходит в промпт, а решение принимает модель.

    Когда в промпт уходит вся база, условий видно несколько — и каждое обязано быть названо
    вместе со своей темой. Без темы условие «если клиент торгуется» повисает в воздухе и
    модель применяет его к разговору, к которому владелец его не писал. Безусловные условия
    («всегда») сюда не попадают: их исполняет код по процитированной карточке."""
    parts = []
    for hit in found:
        condition = hit.card.human_when.strip()
        if not condition or _is_unconditional(condition):
            continue
        line = f"«{hit.card.title}» — {condition}"
        if line not in parts:
            parts.append(line)
    return "; ".join(parts)


# Безусловные формулировки: тут владелец запретил отвечать самому в принципе, и код обязан
# исполнить это сам, не спрашивая модель.
_ALWAYS = ("всегда", "да", "обязательно", "любой вопрос", "все вопросы")


def _is_unconditional(condition: str) -> bool:
    value = str(condition or "").strip().casefold()
    return bool(value) and any(value.startswith(mark) for mark in _ALWAYS)


def always_human(found) -> str:
    """Условие, которое НЕ зависит от сообщения клиента, — его принуждает код."""
    for hit in found:
        if _is_unconditional(hit.card.human_when):
            return hit.card.human_when
    return ""


def always_human_for(found, cited_ids) -> str:
    """То же безусловное правило, но по карточкам, на которые агент реально сослался.

    Когда модели показывают всю базу, «показанная карточка» перестаёт значить «карточка по
    теме разговора»: одно правило «всегда зовём человека» на карточке про гарантии увело бы
    к человеку каждый разговор, включая вопрос о сроках. Решает ссылка: агент назвал карточку
    источником ответа — правило этой карточки и применяется."""
    cited = {str(value) for value in (cited_ids or ())}
    if not cited:
        return ""
    return always_human(tuple(hit for hit in found if hit.card.id in cited))
