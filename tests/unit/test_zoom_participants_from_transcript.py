"""Only people actually heard in the transcript count as call participants.

Zoom's technical log also lists room/service accounts. «Координатор» was reaching every report
as «не сопоставлен с оргструктурой, требуется уточнение», although it never said a word —
a phantom participant the owner had to explain away (reported 20.07.2026, созвон 20.07 10:30).
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
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


def test_shared_first_name_is_not_enough(heard):
    """Созвон 20.07 11:02: Анастасия Докучаева молчала, говорила Анастасия Андрусяк.
    По одному лишь совпавшему имени молчавшего в участники брать нельзя."""
    participants = [{"name": "Анастасия Андрусяк"}, {"name": "Анастасия Докучаева"},
                    {"name": "Наталья"}]
    segments = [{"speaker": "Анастасия Андрусяк", "text": "а"}, {"speaker": "Наталья", "text": "б"}]
    names = [p["name"] for p in heard(participants, segments)]
    assert names == ["Анастасия Андрусяк", "Наталья"]
    assert "Анастасия Докучаева" not in names


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
