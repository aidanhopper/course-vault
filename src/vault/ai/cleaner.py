import json
import logging
import re
from typing import List, Optional
import httpx
from pydantic import BaseModel, Field

from vault.ai.fallback import clean_series_title_fallback, clean_title_fallback
from vault.config import settings

logger = logging.getLogger(__name__)


class CleanedSeriesResult(BaseModel):
    series_title: str
    institution: Optional[str] = None
    instructor: Optional[str] = None
    overview: Optional[str] = None
    video_titles: List[str]


class CleanedLectureResult(BaseModel):
    title: str
    instructor: Optional[str] = None
    institution: Optional[str] = None
    summary: Optional[str] = None
    topic_tags: List[str] = Field(default_factory=list)
    slides_url: Optional[str] = None
    resources_url: Optional[str] = None


async def clean_series_metadata(
    raw_series_title: str,
    raw_video_titles: List[str],
    instructor_hint: Optional[str] = None,
    playlist_description: Optional[str] = None,
) -> CleanedSeriesResult:
    """
    Cleans series title, overview, and individual video titles.
    Uses LLM if AI_API_KEY is configured, otherwise uses heuristic regex fallback.
    """
    # 1. Fallback heuristic defaults
    fallback_series_title, fallback_inst = clean_series_title_fallback(raw_series_title)
    if playlist_description:
        # Check description for "Instructor(s): Name"
        inst_match = re.search(r"\bInstructors?:\s*([^\n\r]+)", playlist_description, flags=re.IGNORECASE)
        if inst_match:
            fallback_inst = inst_match.group(1).strip()
    fallback_inst = fallback_inst or instructor_hint

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
    desc_snippet = (playlist_description[:1000] if playlist_description else 'None provided')
    prompt = f"""You are a course metadata curator for an educational video archive.
Clean and normalize the following course playlist:

Raw Course/Series Title: {raw_series_title}
Instructor/Channel Hint: {instructor_hint or 'Unknown'}
Playlist Description:
{desc_snippet}

Raw Lecture Titles:
{json.dumps(raw_video_titles, indent=2)}

Rules:
1. 'series_title': Clean, academic title that MUST PRESERVE the university or institution prefix and course code (e.g. 'MIT 6.7960 Deep Learning' or 'Stanford CS229 Machine Learning'). It is vital to preserve the institution and course code so courses from different universities are easily differentiated! Strip resolution tags, semester codes (like 'Fall 2024'), or generic playlist fluff.
2. 'institution': The university or organization that produced the course (e.g. 'MIT', 'Stanford', 'Harvard', 'freeCodeCamp'). If unknown, null.
3. 'instructor': Actual human professor / instructor name(s). Look in the description! If multiple professors are listed, join them with commas (e.g. 'Phillip Isola, Sara Beery, Jeremy Bernstein'). Do NOT use the channel name if professors are named. If not found, null.
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


async def clean_lecture_metadata(
    raw_title: str,
    raw_description: Optional[str] = None,
    channel: Optional[str] = None,
    series_institution: Optional[str] = None,
) -> CleanedLectureResult:
    """
    Cleans metadata for an individual lecture video.
    Extracts the specific professor/instructor, summary, topic tags, and slide/resource links.
    """
    # 1. Fallback regex extraction
    fallback_title = clean_title_fallback(raw_title)
    fallback_topic = re.sub(r"^\d+[\.\s\-–—]+\s*", "", fallback_title).strip() or fallback_title
    fallback_inst = None
    fallback_slides = None
    fallback_resources = None

    if raw_description:
        inst_match = re.search(r"\bInstructors?:\s*([^\n\r]+)", raw_description, flags=re.IGNORECASE)
        if inst_match:
            fallback_inst = inst_match.group(1).strip()
        urls = re.findall(r"https?://[^\s<>\"']+", raw_description)
        for u in urls:
            if "slide" in u.lower() or u.lower().endswith(".pdf"):
                fallback_slides = u
            elif "course" in u.lower() or "ocw" in u.lower() or "github" in u.lower():
                fallback_resources = u

    if not settings.AI_API_KEY:
        return CleanedLectureResult(
            title=fallback_topic,
            instructor=fallback_inst,
            institution=series_institution,
            summary=raw_description[:300] if raw_description else None,
            topic_tags=[],
            slides_url=fallback_slides,
            resources_url=fallback_resources,
        )

    # 2. LLM Call
    desc_snippet = raw_description[:1200] if raw_description else "None provided"
    prompt = f"""You are an academic course curator analyzing metadata for a single lecture video.

Raw Video Title: {raw_title}
Channel/Uploader: {channel or 'Unknown'}
Description:
{desc_snippet}

Extract structured metadata for THIS specific lecture video.
If any field is missing or cannot be found in the description/title, set it to null or an empty list. DO NOT hallucinate.

1. 'title': Clean topic title without lecture numbers or resolution tags (e.g. 'Introduction to Deep Learning').
2. 'instructor': The specific human professor or speaker who delivered this lecture (e.g. 'Sara Beery'). If not found, null. Do NOT use the institution/channel name if no person is named.
3. 'institution': University or organization (e.g. 'MIT'). If not found, null.
4. 'summary': 1-2 sentence academic summary of what is taught in this specific video.
5. 'topic_tags': 2-5 specific concept tags taught in this lecture (e.g. ['Deep Neural Networks', 'Neural Network Foundations']).
6. 'slides_url': URL to lecture slides or PDF if mentioned in the description, else null.
7. 'resources_url': URL to companion course materials, problem sets, or code if mentioned in the description, else null.

Respond ONLY with valid JSON:
{{
  "title": "...",
  "instructor": "...",
  "institution": "...",
  "summary": "...",
  "topic_tags": ["..."],
  "slides_url": null,
  "resources_url": "..."
}}"""

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

            return CleanedLectureResult(
                title=parsed.get("title") or fallback_topic,
                instructor=parsed.get("instructor") or fallback_inst,
                institution=parsed.get("institution") or series_institution,
                summary=parsed.get("summary"),
                topic_tags=parsed.get("topic_tags") or [],
                slides_url=parsed.get("slides_url") or fallback_slides,
                resources_url=parsed.get("resources_url") or fallback_resources,
            )
    except Exception as e:
        logger.warning(f"Lecture metadata extraction failed ({e}); using regex heuristic fallback")
        return CleanedLectureResult(
            title=fallback_topic,
            instructor=fallback_inst,
            institution=series_institution,
            summary=raw_description[:300] if raw_description else None,
            topic_tags=[],
            slides_url=fallback_slides,
            resources_url=fallback_resources,
        )
