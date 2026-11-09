import re
from typing import List
from pydantic import BaseModel, ConfigDict


class TranscriptCueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str | None = None
    start_seconds: float
    end_seconds: float
    text: str


def parse_vtt_timestamp(ts: str) -> float:
    """Parse '00:01:23.456' or '01:23.456' into seconds float."""
    parts = ts.strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(parts[0])


def parse_vtt_content(content: str) -> List[TranscriptCueRead]:
    """Parse a WebVTT formatted text string into a list of TranscriptCueRead objects."""
    cues: List[TranscriptCueRead] = []
    # Match WebVTT cue timing: 00:00:00.000 --> 00:00:05.000
    cue_pattern = re.compile(
        r"((?:\d+:)?\d+:\d+\.\d+)\s+-->\s+((?:\d+:)?\d+:\d+\.\d+)[^\n]*\n([\s\S]*?)(?=\n\n|\Z)"
    )

    for match in cue_pattern.finditer(content):
        start_str, end_str, text_block = match.groups()
        start_sec = parse_vtt_timestamp(start_str)
        end_sec = parse_vtt_timestamp(end_str)

        # Clean tags like <c> </c> or <00:00:00.000>
        cleaned_text = re.sub(r"<[^>]+>", "", text_block)
        cleaned_text = " ".join(cleaned_text.strip().split())

        if cleaned_text:
            cues.append(
                TranscriptCueRead(
                    start_seconds=round(start_sec, 3),
                    end_seconds=round(end_sec, 3),
                    text=cleaned_text,
                )
            )

    return cues
