import json
import logging
from typing import List, Optional
import httpx
from pydantic import BaseModel

from vault.ai.fallback import clean_series_title_fallback, clean_title_fallback
from vault.config import settings

logger = logging.getLogger(__name__)


class CleanedSeriesResult(BaseModel):
    series_title: str
    institution: Optional[str] = None
    instructor: Optional[str] = None
    overview: Optional[str] = None
    video_titles: List[str]


async def clean_series_metadata(
    raw_series_title: str,
    raw_video_titles: List[str],
    instructor_hint: Optional[str] = None,
) -> CleanedSeriesResult:
    """
    Cleans series title, overview, and individual video titles.
    Uses LLM if AI_API_KEY is configured, otherwise uses heuristic regex fallback.
    """
    # 1. Fallback heuristic defaults
    fallback_series_title, fallback_inst = clean_series_title_fallback(raw_series_title)
    fallback_inst = instructor_hint or fallback_inst
    fallback_video_titles = [
        clean_title_fallback(title, index=idx + 1)
        for idx, title in enumerate(raw_video_titles)
    ]

    # If no API key configured, return heuristic results immediately
    if not settings.AI_API_KEY:
        return CleanedSeriesResult(
            series_title=fallback_series_title,
            instructor=fallback_inst,
            overview=f"Lectures for {fallback_series_title}.",
            video_titles=fallback_video_titles,
        )

    # 2. LLM Call
    prompt = f"""You are a course metadata curator for an educational video archive.
Clean and normalize the following course playlist:

Raw Course/Series Title: {raw_series_title}
Instructor/Channel Hint: {instructor_hint or 'Unknown'}

Raw Lecture Titles:
{json.dumps(raw_video_titles, indent=2)}

Rules:
1. 'series_title': Clean, academic title that MUST PRESERVE the university or institution prefix and course code (e.g. 'MIT 6.7960 Deep Learning' or 'Stanford CS229 Machine Learning'). It is vital to preserve the institution and course code so courses from different universities are easily differentiated! Strip resolution tags, semester codes (like 'Fall 2024'), or generic playlist fluff.
2. 'institution': The university or organization that produced the course (e.g. 'MIT', 'Stanford', 'Harvard', 'freeCodeCamp'). If unknown, null.
3. 'instructor': Professor or instructor names if mentioned or discernible, else null.
4. 'overview': Concise 1-2 sentence description of what the course covers.
5. 'video_titles': Clean topic titles only, matching the exact order of the input array. Strip lecture numbers, resolution tags, and redundant prefixes (e.g. 'Introduction to Deep Learning', 'How to Train a Neural Net').

Respond ONLY with valid JSON in this structure:
{{
  "series_title": "...",
  "institution": "...",
  "instructor": "...",
  "overview": "...",
  "video_titles": ["..."]
}}
"""

    headers = {
        "Authorization": f"Bearer {settings.AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.AI_MODEL,
        "messages": [
            {"role": "system", "content": "You are a helpful educational metadata curator."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    try:
        url = f"{settings.AI_BASE_URL.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)

            video_titles = parsed.get("video_titles", [])
            # Validate output matches length
            if len(video_titles) != len(raw_video_titles):
                logger.warning("LLM returned mismatch in video titles length; using fallback")
                video_titles = fallback_video_titles

            return CleanedSeriesResult(
                series_title=parsed.get("series_title") or fallback_series_title,
                institution=parsed.get("institution"),
                instructor=parsed.get("instructor") or fallback_inst,
                overview=parsed.get("overview") or f"Course lectures for {fallback_series_title}.",
                video_titles=video_titles,
            )
    except Exception as e:
        logger.warning(f"AI metadata cleanup failed ({e}); using regex heuristic fallback")
        return CleanedSeriesResult(
            series_title=fallback_series_title,
            instructor=fallback_inst,
            overview=f"Course lectures for {fallback_series_title}.",
            video_titles=fallback_video_titles,
        )
