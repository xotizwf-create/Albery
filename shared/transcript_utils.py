"""Pure transcript parsing helpers for Albery.

These helpers normalize VTT/TXT transcript payloads without touching Flask,
network services, database state, or secrets.
"""

from __future__ import annotations

import re
from typing import Any


def parse_zoom_vtt(text: str) -> list[dict[str, Any]]:
    lines = text.replace("\ufeff", "").splitlines()
    cues: list[dict[str, Any]] = []
    time_re = re.compile(r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})")
    i = 0
    while i < len(lines):
        match = time_re.search(lines[i].strip())
        if not match:
            i += 1
            continue
        start_offset = match.group("start")
        end_offset = match.group("end")
        i += 1
        payload: list[str] = []
        while i < len(lines) and lines[i].strip():
            payload.append(lines[i].strip())
            i += 1
        raw_text = re.sub(r"<[^>]+>", "", " ".join(payload)).strip()
        speaker = None
        cue_text = raw_text
        speaker_match = re.match(r"^([^:]{1,80}):\s*(.*)$", raw_text)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            cue_text = speaker_match.group(2).strip()
        if cue_text:
            cues.append(
                {
                    "start": start_offset,
                    "end": end_offset,
                    "speaker": speaker,
                    "text": cue_text,
                }
            )
        i += 1
    return cues


def zoom_plain_transcript(cues: list[dict[str, Any]]) -> str:
    lines = []
    for cue in cues:
        prefix = f"[{cue.get('start')} - {cue.get('end')}]"
        if cue.get("speaker"):
            prefix += f" {cue['speaker']}:"
        lines.append(f"{prefix} {cue.get('text') or ''}".strip())
    return "\n".join(lines)


def parse_transcript_offset(value: str) -> str | None:
    match = re.search(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?", value)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = (match.group(4) or "000").ljust(3, "0")[:3]
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis}"


def parse_drive_transcript_txt(text: str) -> list[dict[str, Any]]:
    lines = text.replace("\ufeff", "").splitlines()
    cues: list[dict[str, Any]] = []
    time_value = r"(?:(?:\d{1,2}:)?\d{1,2}:\d{2})(?:[.,]\d{1,3})?"
    speaker_range_re = re.compile(
        rf"^\[(?P<start>{time_value})\s*(?:-->|-|–|—|\?)\s*(?P<end>{time_value})\]\s*(?P<speaker>[^:]+):\s*(?P<text>.*)$"
    )
    speaker_single_time_re = re.compile(
        rf"^\[(?P<start>{time_value})\]\s*(?P<speaker>[^:]+):\s*(?P<text>.*)$"
    )
    speaker_plain_re = re.compile(r"^(?P<speaker>[^:]{1,80}):\s*(?P<text>.+)$")

    def clean_speaker(value: str) -> str:
        cleaned = re.sub(r"^\s*\d+\]\s*", "", value).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:120]

    def append_text(raw_value: str) -> None:
        value = raw_value.strip()
        if not value:
            return
        if cues:
            cues[-1]["text"] = f"{cues[-1].get('text', '').rstrip()} {value}".strip()
            cues[-1]["raw"] = f"{cues[-1].get('raw', '')}\n{raw_value}".strip()
            return
        cues.append(
            {
                "segment_index": 1,
                "start": None,
                "end": None,
                "speaker": None,
                "text": value,
                "raw": raw_value,
            }
        )

    def start_cue(start: str | None, end: str | None, speaker: str | None, cue_text: str, raw_line: str) -> None:
        cues.append(
            {
                "segment_index": 1,
                "start": parse_transcript_offset(start or ""),
                "end": parse_transcript_offset(end or ""),
                "speaker": clean_speaker(speaker or "") or None,
                "text": cue_text.strip(),
                "raw": raw_line,
            }
        )

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.upper() == "WEBVTT":
            continue

        range_speaker_match = speaker_range_re.match(line)
        if range_speaker_match:
            start_cue(
                range_speaker_match.group("start"),
                range_speaker_match.group("end"),
                range_speaker_match.group("speaker"),
                range_speaker_match.group("text"),
                raw_line,
            )
            continue

        single_time_speaker_match = speaker_single_time_re.match(line)
        if single_time_speaker_match:
            start_cue(
                single_time_speaker_match.group("start"),
                None,
                single_time_speaker_match.group("speaker"),
                single_time_speaker_match.group("text"),
                raw_line,
            )
            continue

        plain_speaker_match = speaker_plain_re.match(line)
        if plain_speaker_match and not re.match(r"^(https?|Источник|Тип|Обновлено)\b", line, re.IGNORECASE):
            start_cue(
                None,
                None,
                plain_speaker_match.group("speaker"),
                plain_speaker_match.group("text"),
                raw_line,
            )
            continue

        append_text(raw_line)

    return [cue for cue in cues if str(cue.get("text") or "").strip()]


def drive_transcript_participants(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    participants: list[dict[str, Any]] = []
    seen: set[str] = set()
    ignored = {"unknown", "speaker", "webvtt", "transcript", "участник", "спикер"}
    for cue in cues:
        speaker = str(cue.get("speaker") or "").strip()
        speaker_key = speaker.lower()
        if not speaker or speaker_key in ignored or speaker_key in seen:
            continue
        seen.add(speaker_key)
        participants.append({"name": speaker, "source": "transcript_speaker"})
    return participants
