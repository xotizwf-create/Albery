"""Присланная картинка не должна молча исчезать из разговора.

20.07.2026, диалог 30 (Наталья): скриншот к запросу «составь доп соглашение исходя из
сокращения выплаты WB» распознался в 0 символов — провайдер снял модель
meta-llama/llama-4-scout (ответ model_not_found). Агент повёл себя так, будто картинки не
было, и переспросил ровно те данные, которые на ней и были.
"""
from __future__ import annotations

import json
import pytest


@pytest.fixture
def bot(app_module, monkeypatch):
    import b24bot

    monkeypatch.setattr(b24bot, "_b24_groq_api_key", lambda: "test-key")
    # По умолчанию в тестах проверяем Groq-ветку; codex-ветка тестируется отдельно.
    monkeypatch.setenv("B24_VISION_ORDER", "groq")
    return b24bot


class _Resp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _answer(text):
    return _Resp({"choices": [{"message": {"content": text}}]})


def test_falls_back_to_the_next_model_when_one_is_decommissioned(bot, monkeypatch):
    """Ровно то, что случилось: первая модель снята с обслуживания."""
    tried = []

    def fake_urlopen(req, timeout=0):
        model = json.loads(req.data.decode())["model"]
        tried.append(model)
        if len(tried) == 1:
            raise RuntimeError("HTTPError 404: model_not_found")
        return _answer("Скидка WB: 214 350 руб.")

    monkeypatch.setattr(bot, "_B24_VISION_MODELS", ["broken/model", "working/model"])
    monkeypatch.setattr(bot.urllib.request, "urlopen", fake_urlopen)

    out = bot._b24_vision_ocr(b"PNGDATA", "screenshot.png")

    assert out == "Скидка WB: 214 350 руб."
    assert tried == ["broken/model", "working/model"], "вторая модель обязана быть испробована"


def test_reasoning_traces_are_stripped(bot, monkeypatch):
    """Рассуждающие модели отдают <think>…</think> — в контекст агента это идти не должно."""
    monkeypatch.setattr(bot, "_B24_VISION_MODELS", ["m1"])
    monkeypatch.setattr(bot.urllib.request, "urlopen",
                        lambda req, timeout=0: _answer("<think>смотрю на картинку</think>\nСумма: 214 350"))

    out = bot._b24_vision_ocr(b"PNGDATA", "s.png")

    assert out == "Сумма: 214 350"
    assert "<think>" not in out


def test_all_models_failing_returns_empty(bot, monkeypatch):
    monkeypatch.setattr(bot, "_B24_VISION_MODELS", ["m1", "m2"])

    def boom(req, timeout=0):
        raise RuntimeError("HTTPError 404")

    monkeypatch.setattr(bot.urllib.request, "urlopen", boom)

    assert bot._b24_vision_ocr(b"PNGDATA", "s.png") == ""


def test_unreadable_image_is_announced_to_the_agent(app_module, monkeypatch):
    """Главное: агент обязан узнать, что картинка была и не прочиталась."""
    import b24bot

    texts = []
    # Собираем ту же ветку, что в обработчике вложений.
    name, txt = "screenshot.png", ""
    if txt:
        texts.append(txt)
    else:
        texts.append(
            f"(⚠️ Изображение «{name}» прислано, но распознать его не удалось. "
            "Не делай вид, что картинки не было: скажи пользователю, что скрин не "
            "прочитался, и попроси прислать его текстом или повторить.)")

    assert texts and "не удалось" in texts[0]
    assert "Не делай вид" in texts[0]


def test_model_list_is_configurable(bot):
    """Снятие модели провайдером лечится переменной окружения, без правки кода."""
    assert isinstance(bot._B24_VISION_MODELS, list) and bot._B24_VISION_MODELS


def test_our_codex_account_is_preferred_by_default(app_module, monkeypatch):
    """Владелец просил распознавать своим агентом, а не сторонними нейронками."""
    import b24bot

    monkeypatch.delenv("B24_VISION_ORDER", raising=False)
    calls = []
    monkeypatch.setattr(b24bot, "_b24_vision_ocr_codex",
                        lambda data, name="": calls.append("codex") or "текст со скрина")
    monkeypatch.setattr(b24bot, "_b24_vision_ocr_groq",
                        lambda data, name="": calls.append("groq") or "groq-текст")

    out = b24bot._b24_vision_ocr(b"PNG", "s.png")

    assert out == "текст со скрина"
    assert calls == ["codex"], "к стороннему провайдеру не идём, пока свой отвечает"


def test_groq_takes_over_when_codex_is_not_logged_in(app_module, monkeypatch):
    """Сейчас codex на сервере не залогинен — пользователь не должен это замечать."""
    import b24bot

    monkeypatch.delenv("B24_VISION_ORDER", raising=False)
    calls = []
    monkeypatch.setattr(b24bot, "_b24_vision_ocr_codex",
                        lambda data, name="": calls.append("codex") or "")
    monkeypatch.setattr(b24bot, "_b24_vision_ocr_groq",
                        lambda data, name="": calls.append("groq") or "текст со скрина")

    out = b24bot._b24_vision_ocr(b"PNG", "s.png")

    assert out == "текст со скрина"
    assert calls == ["codex", "groq"]


def test_codex_absent_binary_is_safe(app_module, monkeypatch):
    import b24bot

    monkeypatch.setattr(b24bot.shutil if hasattr(b24bot, "shutil") else b24bot, "which",
                        lambda name: None, raising=False)
    # even without the binary the call must not raise
    assert isinstance(b24bot._b24_vision_ocr_codex(b"PNG", "s.png"), str)
