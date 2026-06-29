from shared.transcript_utils import (
    drive_transcript_participants,
    parse_drive_transcript_txt,
    parse_transcript_offset,
    parse_zoom_vtt,
    zoom_plain_transcript,
)


def test_parse_zoom_vtt_extracts_speaker_text_and_strips_tags():
    cues = parse_zoom_vtt(
        """WEBVTT

00:00:01.000 --> 00:00:03.500
Александр: <v Александр>Привет</v> команда

00:00:04.000 --> 00:00:05.000
Без спикера
"""
    )
    assert cues == [
        {"start": "00:00:01.000", "end": "00:00:03.500", "speaker": "Александр", "text": "Привет команда"},
        {"start": "00:00:04.000", "end": "00:00:05.000", "speaker": None, "text": "Без спикера"},
    ]


def test_zoom_plain_transcript_keeps_existing_format():
    assert zoom_plain_transcript(
        [
            {"start": "00:00:01.000", "end": "00:00:03.500", "speaker": "Александр", "text": "Привет"},
            {"start": "00:00:04.000", "end": "00:00:05.000", "speaker": None, "text": "Ответ"},
        ]
    ) == "[00:00:01.000 - 00:00:03.500] Александр: Привет\n[00:00:04.000 - 00:00:05.000] Ответ"


def test_parse_transcript_offset_normalizes_supported_values():
    assert parse_transcript_offset("1:02") == "00:01:02.000"
    assert parse_transcript_offset("01:02:03,4") == "01:02:03.400"
    assert parse_transcript_offset("01:02:03.4567") == "01:02:03.456"
    assert parse_transcript_offset("no timestamp") is None


def test_parse_drive_transcript_txt_handles_range_single_time_plain_speaker_and_continuation():
    cues = parse_drive_transcript_txt(
        """WEBVTT
Источник: https://example.com/file
[00:00:01 - 00:00:03] Александр: Первый тезис
продолжение первого тезиса
[00:00:04] Даша: Второй тезис
3]  Иван   Петров : Третий тезис
https://example.com/ignored-as-continuation
"""
    )
    assert cues == [
        {
            "segment_index": 1,
            "start": None,
            "end": None,
            "speaker": None,
            "text": "Источник: https://example.com/file",
            "raw": "Источник: https://example.com/file",
        },
        {
            "segment_index": 1,
            "start": "00:00:01.000",
            "end": "00:00:03.000",
            "speaker": "Александр",
            "text": "Первый тезис продолжение первого тезиса",
            "raw": "[00:00:01 - 00:00:03] Александр: Первый тезис\nпродолжение первого тезиса",
        },
        {
            "segment_index": 1,
            "start": "00:00:04.000",
            "end": None,
            "speaker": "Даша",
            "text": "Второй тезис",
            "raw": "[00:00:04] Даша: Второй тезис",
        },
        {
            "segment_index": 1,
            "start": None,
            "end": None,
            "speaker": "Иван Петров",
            "text": "Третий тезис https://example.com/ignored-as-continuation",
            "raw": "3]  Иван   Петров : Третий тезис\nhttps://example.com/ignored-as-continuation",
        },
    ]


def test_parse_drive_transcript_txt_keeps_leading_metadata_as_text_when_no_cue_exists():
    cues = parse_drive_transcript_txt("Источник: файл\nОбычный текст")
    assert cues == [
        {
            "segment_index": 1,
            "start": None,
            "end": None,
            "speaker": None,
            "text": "Источник: файл Обычный текст",
            "raw": "Источник: файл\nОбычный текст",
        }
    ]


def test_drive_transcript_participants_dedupes_and_ignores_placeholders():
    participants = drive_transcript_participants(
        [
            {"speaker": "Александр"},
            {"speaker": "александр"},
            {"speaker": "Speaker"},
            {"speaker": ""},
            {"speaker": "Даша"},
        ]
    )
    assert participants == [
        {"name": "Александр", "source": "transcript_speaker"},
        {"name": "Даша", "source": "transcript_speaker"},
    ]
