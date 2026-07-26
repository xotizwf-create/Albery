"""Семантический поиск по карточкам знаний: смысл вместо совпадения слов.

Владелец 26.07.2026: «для этого же есть эмбеддинги, не надо вручную эту фигню прописывать».

Он прав. Ручной список формулировок клиента («спрашивают так: сколько вы берёте, ваша
комиссия…») — это костыль под лексический поиск: человек заранее угадывает, какими словами
спросят. Эмбеддинги кодируют СМЫСЛ карточки в вектор, и «сколько вы берёте» находит «Комиссию»
без всякого списка синонимов. Владелец пишет только сам факт.

Почему лексика всё равно осталась. Эмбеддинги — сетевой вызов: ключ может протухнуть, API
ответить 429 или лечь. Молча перестать находить знания в этот момент значит вернуть агента в то
самое состояние, из-за которого всё и переделывается. Поэтому лексический скор считается всегда,
а итоговый — максимум из двух: семантика поднимает то, что лексика не увидела, но не может
уронить то, что она уже нашла.

Калибровка. Косинус между родственными русскими текстами у `text-embedding-3-small` обычно
0.3–0.8, у несвязанных — около 0.0–0.2. Отдать сырой косинус в порог уверенности нельзя: он
систематически ниже, и 0.65 не проходил бы почти никогда. Поэтому косинус линейно растягивается
между полом и потолком, оба вынесены в окружение — после наполнения базы их надо будет
подстроить по реальным вопросам, и это делается без деплоя.

Модель — та же `OPENAI_API_KEY`, которым Albery уже пользуется. Эмбеддинги стоят копейки и
считаются отдельно от лимитов чат-моделей.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from dataclasses import dataclass, field

import iu_knowledge

log = logging.getLogger("iu-embeddings")

MODEL = os.getenv("IU_EMBEDDING_MODEL", "text-embedding-3-small").strip()
# Границы растяжения косинуса. Ниже пола — «не про это», выше потолка — «точно про это».
COS_FLOOR = float(os.getenv("IU_EMBEDDING_FLOOR", "0.25") or 0.25)
COS_CEIL = float(os.getenv("IU_EMBEDDING_CEIL", "0.72") or 0.72)
TIMEOUT_S = float(os.getenv("IU_EMBEDDING_TIMEOUT_S", "20") or 20)


class Unavailable(RuntimeError):
    """Эмбеддинги посчитать не удалось. Не ошибка хода: остаётся лексический поиск."""


def available() -> bool:
    """Есть ли чем считать векторы. Без ключа слой молча выключен."""
    import llm

    return bool(llm.llm_api_key())


def _http_embed(texts: list[str]) -> list[list[float]]:
    """Один вызов /v1/embeddings. Вынесен отдельно, чтобы тесты подменяли его целиком."""
    import requests

    import llm

    key = llm.llm_api_key()
    if not key:
        raise Unavailable("нет OPENAI_API_KEY")
    try:
        response = requests.post(
            llm.llm_api_url("/embeddings"),
            headers=llm.llm_auth_headers(key),
            json={"model": MODEL, "input": texts},
            timeout=TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — сеть не обязана работать
        raise Unavailable(f"сеть недоступна: {exc}") from exc
    if response.status_code != 200:
        raise Unavailable(f"HTTP {response.status_code}: {response.text[:160]}")
    try:
        rows = response.json()["data"]
        return [list(row["embedding"]) for row in rows]
    except Exception as exc:  # noqa: BLE001
        raise Unavailable(f"неожиданный ответ эмбеддингов: {exc}") from exc


def cosine(left, right) -> float:
    """Косинус между векторами. Разная длина или нулевой вектор — нулевая близость."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if not norm_left or not norm_right:
        return 0.0
    return dot / (norm_left * norm_right)


def calibrate(value: float) -> float:
    """Косинус → скор 0..1 для порога уверенности."""
    if COS_CEIL <= COS_FLOOR:
        return max(0.0, min(1.0, float(value)))
    scaled = (float(value) - COS_FLOOR) / (COS_CEIL - COS_FLOOR)
    return max(0.0, min(1.0, scaled))


def card_text(card) -> str:
    """Что именно кодируется в вектор: сам факт, а не служебные поля.

    Заголовок и ответ несут смысл; «Проще» добавляется, потому что владелец пишет там то же
    самое бытовым языком — а клиенты спрашивают именно бытовым."""
    parts = [card.title, card.answer]
    if getattr(card, "simple", ""):
        parts.append(card.simple)
    # Список формулировок остаётся поддержанным, но теперь он не обязателен: если владелец
    # его не заполнил, смысл всё равно закодирован заголовком и ответом.
    parts.extend(getattr(card, "aliases", ()) or ())
    return "\n".join(part for part in parts if part)


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Index:
    """Векторы карточек с кэшем по хэшу содержимого.

    Пересчитывается только изменившееся: правка одной карточки не тянет за собой весь корпус."""

    store: dict = field(default_factory=dict)
    embed = staticmethod(_http_embed)

    def __post_init__(self):
        if not isinstance(self.store, dict):
            raise TypeError("store должен быть словарём вектор-кэша")

    def warm(self, cards) -> int:
        """Досчитать векторы недостающих карточек. Возвращает, сколько посчитано."""
        wanted: dict[str, str] = {}
        for card in iu_knowledge.approved(cards):
            text = card_text(card)
            key = _key(text)
            if key not in self.store:
                wanted[key] = text
        if not wanted:
            return 0
        keys = list(wanted)
        vectors = type(self).embed([wanted[k] for k in keys])
        if len(vectors) != len(keys):
            raise Unavailable("эмбеддингов вернулось меньше, чем запрошено")
        for key, vector in zip(keys, vectors):
            self.store[key] = vector
        return len(keys)

    def vector_of(self, card):
        return self.store.get(_key(card_text(card)))

    def rerank(self, query: str, hits):
        """Скорер для `iu_knowledge.search`: максимум из лексики и семантики.

        Сбой здесь не имеет права стоить ответа клиенту — при любой проблеме возвращаем
        лексические скоры без изменений."""
        cards = [hit.card for hit in hits]
        if not cards:
            return hits
        try:
            self.warm(cards)
            query_vector = type(self).embed([str(query or "")])[0]
        except Unavailable as exc:
            log.info("семантический поиск недоступен, работаем лексикой: %s", exc)
            return hits
        except Exception:  # noqa: BLE001
            log.warning("семантический поиск упал, работаем лексикой", exc_info=True)
            return hits

        out = []
        for hit in hits:
            vector = self.vector_of(hit.card)
            semantic = calibrate(cosine(query_vector, vector)) if vector else 0.0
            out.append(iu_knowledge.Found(hit.card, max(hit.score, semantic)))
        return out


def scorer(store: dict | None = None):
    """Готовый `rerank` для поиска. `None` — если считать векторы нечем."""
    if not available():
        return None
    return Index(store if store is not None else {}).rerank
