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


def test_groq_media_provider_is_preferred_by_default(app_module, monkeypatch):
    """Решение 10.08.2026: Groq обрабатывает медиа, Codex сохраняется как fallback."""
    import b24bot

    monkeypatch.delenv("B24_VISION_ORDER", raising=False)
    calls = []
    monkeypatch.setattr(b24bot, "_b24_vision_ocr_codex",
                        lambda data, name="": calls.append("codex") or "текст со скрина")
    monkeypatch.setattr(b24bot, "_b24_vision_ocr_groq",
                        lambda data, name="": calls.append("groq") or "groq-текст")

    out = b24bot._b24_vision_ocr(b"PNG", "s.png")

    assert out == "groq-текст"
    assert calls == ["groq"]


def test_codex_takes_over_when_groq_is_unavailable(app_module, monkeypatch):
    """Сбой media provider не должен сделать агента слепым."""
    import b24bot

    monkeypatch.delenv("B24_VISION_ORDER", raising=False)
    calls = []
    monkeypatch.setattr(b24bot, "_b24_vision_ocr_groq",
                        lambda data, name="": calls.append("groq") or "")
    monkeypatch.setattr(b24bot, "_b24_vision_ocr_codex",
                        lambda data, name="": calls.append("codex") or "текст со скрина")

    out = b24bot._b24_vision_ocr(b"PNG", "s.png")

    assert out == "текст со скрина"
    assert calls == ["groq", "codex"]


def test_codex_absent_binary_is_safe(app_module, monkeypatch):
    import b24bot

    monkeypatch.setattr(b24bot.shutil if hasattr(b24bot, "shutil") else b24bot, "which",
                        lambda name: None, raising=False)
    # even without the binary the call must not raise
    assert isinstance(b24bot._b24_vision_ocr_codex(b"PNG", "s.png"), str)


def test_unauthenticated_codex_fails_fast(app_module, monkeypatch):
    """Незалогиненный codex переподключается 20 секунд — пользователь столько ждать не должен."""
    import b24bot

    b24bot._B24_CODEX_AUTH_CACHE.update({"at": 0.0, "ok": False})
    calls = []

    class _Proc:
        returncode = 0
        stdout = "Not logged in"
        stderr = ""

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(b24bot.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or _Proc())

    assert b24bot._b24_vision_ocr_codex(b"PNG", "s.png") == ""
    assert calls and calls[0][:3] == ["codex", "login", "status"], "сначала дешёвая проверка входа"
    assert all("exec" not in c for c in calls), "тяжёлый вызов не запускается"


def test_codex_auth_verdict_is_cached(app_module, monkeypatch):
    import b24bot

    b24bot._B24_CODEX_AUTH_CACHE.update({"at": 0.0, "ok": False})
    runs = []

    class _Proc:
        returncode = 0
        stdout = "Not logged in"
        stderr = ""

    monkeypatch.setattr(b24bot.subprocess, "run", lambda *a, **k: runs.append(1) or _Proc())

    b24bot._b24_codex_logged_in()
    b24bot._b24_codex_logged_in()
    b24bot._b24_codex_logged_in()

    assert len(runs) == 1, "проверка входа кешируется, а не дёргается на каждую картинку"


def test_audio_transcription_uses_groq_whisper(app_module, monkeypatch):
    """Audio remains in the Groq media contour and never enters the Codex quality runner."""
    import b24bot

    calls = []

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"text": "test phrase 4827"}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(b24bot, "_b24_groq_api_key", lambda: "test-key")
    monkeypatch.setattr(b24bot.requests, "post", fake_post)

    out = b24bot._b24_transcribe_audio(b"WAVE", "voice.wav")

    assert out == "test phrase 4827"
    assert calls[0][0] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert calls[0][1]["data"]["model"] == "whisper-large-v3"
    assert calls[0][1]["files"]["file"] == ("voice.wav", b"WAVE")
