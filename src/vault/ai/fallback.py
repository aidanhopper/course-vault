import re
from typing import List, Tuple


def sanitize_filename(name: str) -> str:
    """Sanitize a string to be safe for filenames on macOS, Linux, and Windows."""
    # Remove characters illegal in filenames: / \ : * ? " < > |
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    # Collapse multiple spaces
    cleaned = " ".join(cleaned.strip().split())
    # Avoid empty names or leading/trailing dots
    cleaned = cleaned.strip(".")
    return cleaned or "unnamed"


def clean_title_fallback(raw_title: str, index: int | None = None) -> str:
    """
    Regex/heuristic title cleanup when no AI API key is configured.
    Strips resolution tags, year brackets, channel watermarks, and common noise.
    """
    title = raw_title

    # Remove [HD], [1080p], [4K], (Official), [Full Course], etc.
    title = re.sub(r"\[(?:1080p|720p|4k|hd|full\s*course|lecture\s*\d+|[^\]]*\d{4}[^\]]*)\]", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\((?:1080p|720p|4k|hd|official|lecture\s*\d+|[^)]*\d{4}[^)]*)\)", "", title, flags=re.IGNORECASE)

    # Remove trailing channel branding like "| MIT 6.006 Fall 2011" or "- freeCodeCamp.org"
    title = re.sub(r"\s*[-|–—]\s*(?:MIT|Stanford|Harvard|freeCodeCamp|Coursera|edX)[^$]*$", "", title, flags=re.IGNORECASE)

    # Normalize "MIT 6.006 Lecture 01: Intro" or "Lec 01: Intro to Algo" -> "01. Intro to Algo"
    match_lec = re.search(r"\b(?:lec(?:ture)?)\.?\s*0*(\d+)[\s:\-–—]+(.*)$", title, flags=re.IGNORECASE)
    if match_lec:
        lec_num, topic = match_lec.groups()
        num_prefix = f"{int(lec_num):02d}. "
        cleaned_topic = topic.strip(" -:–—|")
        if cleaned_topic:
            return f"{num_prefix}{cleaned_topic}"

    # Also check if it starts with "01 - Intro" or "1. Intro"
    match_num = re.match(r"^0*(\d+)[\s.\-–—]+(.*)$", title)
    if match_num:
        num, topic = match_num.groups()
        return f"{int(num):02d}. {topic.strip(' -:–—|')}"

    # Collapse remaining extra whitespace
    title = " ".join(title.strip(" -:–—|").split())

    # If an index was provided and title doesn't start with a number, prepend it
    if index is not None and not re.match(r"^\d+[\.\s]", title):
        title = f"{index:02d}. {title}"

    return title or (f"Lecture {index:02d}" if index is not None else "Untitled Lecture")


def clean_series_title_fallback(raw_title: str) -> Tuple[str, str | None]:
    """Extracts a clean series title and possible instructor name."""
    title = raw_title

    # Remove generic playlist tags like "[Full Playlist]", "(Fall 2011)"
    title = re.sub(r"\[[^\]]*\]", "", title)
    title = re.sub(r"\((?:Fall|Spring|Winter|Summer)?\s*\d{4}\)", "", title, flags=re.IGNORECASE)
    title = " ".join(title.strip(" -:–—|").split())

    instructor = None
    # Check for "by Professor Name" or "with Instructor"
    match_inst = re.search(r"\b(?:by|with)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", raw_title)
    if match_inst:
        instructor = match_inst.group(1)

    return title or "Untitled Series", instructor
