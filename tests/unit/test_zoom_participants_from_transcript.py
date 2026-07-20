"""Only people actually heard in the transcript count as call participants.

Zoom's technical log also lists room/service accounts. «Координатор» was reaching every report
as «не сопоставлен с оргструктурой, требуется уточнение», although it never said a word —
a phantom participant the owner had to explain away (reported 20.07.2026, созвон 20.07 10:30).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def alias_directory(app_module, monkeypatch):
    """По умолчанию справочник алиасов пуст — тесты не ходят в БД и не влияют друг на друга.
    Отдаёт настоящий загрузчик тем тестам, которые проверяют сам разбор справочника."""
    real_loader = app_module.zoom.name_alias_pairs
    app_module.zoom._NAME_ALIAS_CACHE.update({"at": 0.0, "pairs": []})
    monkeypatch.setattr(app_module.zoom, "name_alias_pairs", lambda: [])
    yield real_loader
    app_module.zoom._NAME_ALIAS_CACHE.update({"at": 0.0, "pairs": []})


@pytest.fixture
def heard(app_module):
    return app_module.zoom.participants_heard_in_transcript


def test_silent_room_account_is_dropped(heard):
    """The exact call from the screenshot: two speakers, plus a silent «Координатор»."""
    participants = [{"name": "Погорелова Софья"}, {"name": "Наталья"}, {"name": "Координатор"}]
    segments = [
        {"speaker": "Наталья", "text": "Накопительным итогом количество."},
        {"speaker": "Погорелова Софья", "text": "СПП по всем артикулам растёт."},
    ]
    names = [p["name"] for p in heard(participants, segments)]
    assert names == ["Погорелова Софья", "Наталья"]
    assert "Координатор" not in names


def test_word_order_and_case_do_not_matter(heard):
    participants = [{"name": "Погорелова Софья Александровна"}]
    segments = [{"speaker": "софья погорелова", "text": "реплика"}]
    assert len(heard(participants, segments)) == 1


def test_alias_counts_as_the_same_person(app_module, heard, monkeypatch):
    """«Анастасия Докучаева» и «Анастасия Андрусяк» — один человек по справочнику компании.
    В логе Zoom она значится одной фамилией, в расшифровке говорит под другой."""
    monkeypatch.setattr(app_module.zoom, "name_alias_pairs",
                        lambda: [("Анастасия Докучаева", "Анастасия Андрусяк")])
    participants = [{"name": "Анастасия Докучаева"}, {"name": "Наталья"}]
    segments = [{"speaker": "Анастасия Андрусяк", "text": "а"}, {"speaker": "Наталья", "text": "б"}]

    names = [p["name"] for p in heard(participants, segments)]
    assert names == ["Анастасия Докучаева", "Наталья"], "алиас — тот же человек, её нельзя терять"


def test_alias_does_not_merge_different_people(app_module, heard, monkeypatch):
    monkeypatch.setattr(app_module.zoom, "name_alias_pairs",
                        lambda: [("Анастасия Докучаева", "Анастасия Андрусяк")])
    participants = [{"name": "Анастасия Клеблеева"}, {"name": "Анастасия Докучаева"}]
    segments = [{"speaker": "Анастасия Андрусяк", "text": "а"}]

    names = [p["name"] for p in heard(participants, segments)]
    assert names == ["Анастасия Докучаева"], "Клеблеева — другой человек"


def test_alias_directory_parsing(app_module, monkeypatch, alias_directory):
    """Формат справочника: «- имя в созвоне = имя в Битриксе», комментарии игнорируются."""
    block = ("# Сопоставление имён\n"
             "# комментарий = не алиас\n"
             "- Анастасия Докучаева = Анастасия Андрусяк\n"
             "- Иван Петров => Иван Сидоров\n"
             "- Анастасия Докучаева = Анастасия Андрусяк в нашей оргструктуре.\n")

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): pass
        def fetchall(self): return [{"content": block}]

    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return FakeCursor()

    monkeypatch.setattr(app_module.zoom, "pg_connect", lambda: FakeConn())
    app_module.zoom._NAME_ALIAS_CACHE.update({"at": 0.0, "pairs": []})

    pairs = alias_directory()  # настоящий загрузчик, а не заглушка

    assert ("Анастасия Докучаева", "Анастасия Андрусяк") in pairs
    assert ("Иван Петров", "Иван Сидоров") in pairs
    assert all("комментарий" not in left for left, _ in pairs)
    # Хвост прозы не должен попадать в имя.
    assert all(not right.endswith("оргструктуре") for _, right in pairs)


def test_shared_first_name_is_not_enough(heard):
    """Полные тёзки по имени — разные люди, если они не связаны алиасом."""
    participants = [{"name": "Анастасия Андрусяк"}, {"name": "Анастасия Клеблеева"},
                    {"name": "Наталья"}]
    segments = [{"speaker": "Анастасия Андрусяк", "text": "а"}, {"speaker": "Наталья", "text": "б"}]
    names = [p["name"] for p in heard(participants, segments)]
    assert names == ["Анастасия Андрусяк", "Наталья"]
    assert "Анастасия Клеблеева" not in names


def test_short_label_matches_full_name(heard):
    """Расшифровка часто подписывает человека одним именем."""
    participants = [{"name": "Наталья Викторовна Горюнова"}]
    segments = [{"speaker": "Наталья", "text": "реплика"}]
    assert len(heard(participants, segments)) == 1


def test_every_speaking_participant_is_kept(heard):
    participants = [{"name": "Евгений Палей"}, {"name": "Наталья Горюнова"}]
    segments = [{"speaker": "Евгений Палей", "text": "а"}, {"speaker": "Наталья", "text": "б"}]
    assert len(heard(participants, segments)) == 2


def test_transcript_without_speakers_keeps_real_people(heard):
    """A speaker-less transcription must not erase real participants."""
    participants = [{"name": "Погорелова Софья"}, {"name": "Наталья"}]
    segments = [{"speaker": "", "text": "сплошной текст"}]
    assert len(heard(participants, segments)) == 2


def test_room_account_is_dropped_even_without_any_transcript(heard):
    """Созвон 20.07 11:02: расшифровка ещё без спикеров, а «Координатор» уже в логе Zoom."""
    participants = [{"name": "Анастасия Докучаева"}, {"name": "Дмитрий Строгонов"},
                    {"name": "Координатор"}, {"name": "Оксана Хапова"}]
    names = [p["name"] for p in heard(participants, [])]
    assert "Координатор" not in names
    assert len(names) == 3, "остальных участников трогать нельзя"


def test_other_service_accounts_are_dropped_too(heard):
    participants = [{"name": "Наталья"}, {"name": "Zoom Room"}, {"name": "Recorder"}]
    assert [p["name"] for p in heard(participants, [])] == ["Наталья"]


def test_speakers_are_read_from_plain_text_transcript(heard):
    """Некоторые созвоны хранятся текстом без сегментов — спикеров берём из него."""
    participants = [{"name": "Погорелова Софья"}, {"name": "Наталья"}, {"name": "Артур Степанян"}]
    text = "00:01:00 Наталья: корзин двадцать тысяч\n00:02:02 Погорелова Софья: СПП растёт"
    names = [p["name"] for p in heard(participants, [], text)]
    assert names == ["Погорелова Софья", "Наталья"]
    assert "Артур Степанян" not in names


def test_no_segments_keeps_everyone(heard):
    participants = [{"name": "Погорелова Софья"}]
    assert len(heard(participants, [])) == 1
    assert len(heard(participants, None)) == 1


def test_noise_speaker_labels_do_not_count_as_speech(heard):
    """«Unknown»/«Спикер» are transcription artefacts, not evidence anybody was heard."""
    participants = [{"name": "Погорелова Софья"}, {"name": "Координатор"}]
    segments = [{"speaker": "unknown", "text": "шум"}, {"speaker": "Погорелова Софья", "text": "реплика"}]
    names = [p["name"] for p in heard(participants, segments)]
    assert names == ["Погорелова Софья"]


def test_nobody_matches_falls_back_to_original_list(heard):
    """If labels are unrecognisable, keep the log rather than emptying the report."""
    participants = [{"name": "Погорелова Софья"}, {"name": "Наталья"}]
    segments = [{"speaker": "iPhone пользователя", "text": "реплика"}]
    assert len(heard(participants, segments)) == 2


def test_empty_participants_is_safe(heard):
    assert heard([], [{"speaker": "Наталья", "text": "а"}]) == []
    assert heard(None, None) == []
