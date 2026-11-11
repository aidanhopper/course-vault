from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from vault.ai.fallback import clean_series_title_fallback, clean_title_fallback
from vault.config import settings

logger = logging.getLogger(__name__)


class CleanedSeriesResult(BaseModel):
    series_title: str
    institution: Optional[str] = None
    instructors: List[str] = Field(default_factory=list)
    overview: Optional[str] = None
    video_titles: List[str]


class CleanedLectureResult(BaseModel):
    title: str
    instructors: List[str] = Field(default_factory=list)
    institution: Optional[str] = None
    summary: Optional[str] = None
    topic_tags: List[str] = Field(default_factory=list)
    slides_url: Optional[str] = None
    resources_url: Optional[str] = None


class BatchLectureItem(BaseModel):
    source_id: str
    title: str
    instructor: Optional[str] = None
    summary: Optional[str] = None
    topic_tags: List[str] = Field(default_factory=list)
    slides_url: Optional[str] = None
    resources_url: Optional[str] = None


class BatchCourseResult(BaseModel):
    series_title: str
    institution: Optional[str] = None
    instructors: List[str] = Field(default_factory=list)
    overview: Optional[str] = None
    lectures: List[BatchLectureItem] = Field(default_factory=list)


async def clean_series_metadata(
    raw_series_title: str,
    raw_video_titles: List[str],
    uploader: Optional[str] = None,
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
    fallback_inst = fallback_inst or uploader
    fallback_institution = "MIT" if ("mit" in (uploader or "").lower() or "mit" in raw_series_title.lower()) else uploader

    fallback_video_titles = [
        clean_title_fallback(title, index=idx + 1)
        for idx, title in enumerate(raw_video_titles)
    ]

    # If no API key configured, return heuristic results immediately
    if not settings.AI_API_KEY:
        return CleanedSeriesResult(
            series_title=fallback_series_title,
            institution=fallback_institution,
            instructors=[fallback_inst] if fallback_inst else [],
            overview=f"Lectures for {fallback_series_title}.",
            video_titles=fallback_video_titles,
        )

    # 2. LLM Call
    desc_snippet = (playlist_description[:1000] if playlist_description else 'None provided')
    prompt = f"""You are a course metadata curator for an educational video archive.
Clean and normalize the following course playlist:

Raw Course/Series Title: {raw_series_title}
Instructor/Channel Hint: {uploader or 'Unknown'}
Playlist Description:
{desc_snippet}

Raw Lecture Titles:
{json.dumps(raw_video_titles, indent=2)}

Rules:
1. 'series_title': Clean, academic title that MUST PRESERVE the university or institution prefix and course code (e.g. 'MIT 6.7960 Deep Learning' or 'Stanford CS229 Machine Learning'). It is vital to preserve the institution and course code so courses from different universities are easily differentiated! Strip resolution tags, semester codes (like 'Fall 2024'), or generic playlist fluff.
2. 'institution': The university or organization that produced the course (e.g. 'MIT', 'Stanford', 'Harvard', 'freeCodeCamp'). If unknown, null.
3. 'instructors': Array of actual human professor / instructor names (e.g. ['Phillip Isola', 'Sara Beery', 'Jeremy Bernstein']). Look in the description! Do NOT use the channel name. If not found, [].
4. 'overview': Concise 1-2 sentence description of what the course covers.
5. 'video_titles': Clean topic titles only, matching the exact order of the input array. Strip lecture numbers, resolution tags, and redundant prefixes (e.g. 'Introduction to Deep Learning', 'How to Train a Neural Net').

Respond ONLY with valid JSON in this structure:
{{
  "series_title": "...",
  "institution": "...",
  "instructors": ["..."],
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

            inst_list = parsed.get("instructors") or ([parsed["instructor"]] if parsed.get("instructor") else ([fallback_inst] if fallback_inst else []))
            return CleanedSeriesResult(
                series_title=parsed.get("series_title") or fallback_series_title,
                institution=parsed.get("institution"),
                instructors=inst_list,
                overview=parsed.get("overview") or f"Course lectures for {fallback_series_title}.",
                video_titles=video_titles,
            )
    except Exception as e:
        logger.warning(f"AI metadata cleanup failed ({e}); using regex heuristic fallback")
        return CleanedSeriesResult(
            series_title=fallback_series_title,
            institution=fallback_institution,
            instructors=[fallback_inst] if fallback_inst else [],
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
            instructors=[fallback_inst] if fallback_inst else [],
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
2. 'instructors': Array of human professors or speakers who delivered this lecture (e.g. ['Sara Beery']). If not found, []. Do NOT use the institution/channel name if no person is named.
3. 'institution': University or organization (e.g. 'MIT'). If not found, null.
4. 'summary': 1-2 sentence academic summary of what is taught in this specific video.
5. 'topic_tags': 6-12 deep, granular, and precise academic concept tags. Do NOT stop at generic labels like 'Deep Learning' or 'Education'. Extract specific mathematical foundations (e.g. 'Universal Approximation', 'Empirical Risk Minimization'), concrete algorithms & mechanisms (e.g. 'Backpropagation', 'Stochastic Gradient Descent', 'Multi-Head Attention', 'ReLU', 'Perceptrons'), and theoretical phenomena (e.g. 'Double Descent', 'Inductive Bias', 'Vanishing Gradients').
6. 'slides_url': URL to lecture slides or PDF if mentioned in the description, else null.
7. 'resources_url': URL to companion course materials, problem sets, or code if mentioned in the description, else null.

Respond ONLY with valid JSON:
{{
  "title": "...",
  "instructors": ["..."],
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

    retries = 3
    backoff = 2.0
    for attempt in range(retries):
        try:
            url = f"{settings.AI_BASE_URL.rstrip('/')}/chat/completions"
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 429:
                    if attempt < retries - 1:
                        wait_time = float(resp.headers.get("retry-after", backoff))
                        logger.info(f"Gemini rate limit (429) hit; waiting {wait_time:.1f}s before retry...")
                        await asyncio.sleep(wait_time)
                        backoff *= 2
                        continue
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)

                lec_insts = parsed.get("instructors") or ([parsed["instructor"]] if parsed.get("instructor") else ([fallback_inst] if fallback_inst else []))
                return CleanedLectureResult(
                    title=parsed.get("title") or fallback_topic,
                    instructors=lec_insts,
                    institution=parsed.get("institution") or series_institution,
                    summary=parsed.get("summary"),
                    topic_tags=parsed.get("topic_tags") or [],
                    slides_url=parsed.get("slides_url") or fallback_slides,
                    resources_url=parsed.get("resources_url") or fallback_resources,
                )
        except Exception as e:
            if attempt == retries - 1:
                logger.info(f"Using regex heuristic metadata fallback for '{raw_title}' ({e})")
                break
            await asyncio.sleep(backoff)
            backoff *= 2

    return CleanedLectureResult(
        title=fallback_topic,
        instructors=[fallback_inst] if fallback_inst else [],
        institution=series_institution,
        summary=raw_description[:300] if raw_description else None,
        topic_tags=[],
        slides_url=fallback_slides,
        resources_url=fallback_resources,
    )


async def clean_course_batch(
    raw_series_title: str,
    uploader: Optional[str] = None,
    playlist_description: Optional[str] = None,
    lectures: Optional[List[Dict[str, Any]]] = None,
) -> BatchCourseResult:
    """
    Cleans an entire course playlist in a single batched LLM request.
    Extracts series title, institution, all professors, overview, and per-lecture metadata.
    Avoids 429 rate limits by sending 1 request instead of N individual requests.
    """
    lectures = lectures or []
    # 1. Fallback defaults
    fallback_series_title, fallback_inst = clean_series_title_fallback(raw_series_title)
    if playlist_description:
        inst_match = re.search(r"\bInstructors?:\s*([^\n\r]+)", playlist_description, flags=re.IGNORECASE)
        if inst_match:
            fallback_inst = inst_match.group(1).strip()
    fallback_inst = fallback_inst or uploader

    fallback_lectures: List[BatchLectureItem] = []
    for idx, lec in enumerate(lectures):
        raw_t = lec.get("title") or f"Lecture {idx + 1}"
        desc = lec.get("description") or ""
        clean_t = clean_title_fallback(raw_t, index=idx + 1)
        clean_top = re.sub(r"^\d+[\.\s\-–—]+\s*", "", clean_t).strip() or clean_t
        lec_inst = None
        if desc:
            im = re.search(r"\bInstructors?:\s*([^\n\r]+)", desc, flags=re.IGNORECASE)
            if im:
                lec_inst = im.group(1).strip()

        fallback_lectures.append(
            BatchLectureItem(
                source_id=lec.get("source_id") or str(idx + 1),
                title=clean_top,
                instructor=lec_inst or fallback_inst,
                summary=desc[:250] if desc else None,
            )
        )

    if not settings.AI_API_KEY:
        return BatchCourseResult(
            series_title=fallback_series_title,
            institution=uploader,
            instructors=[fallback_inst] if fallback_inst else [],
            overview=f"Course lectures for {fallback_series_title}.",
            lectures=fallback_lectures,
        )

    # 2. Prepare single batch prompt
    batch_payload = []
    for idx, lec in enumerate(lectures):
        batch_payload.append({
            "source_id": lec.get("source_id") or str(idx + 1),
            "raw_title": lec.get("title") or f"Lecture {idx + 1}",
            "description": (lec.get("description") or "")[:400],
        })

    desc_snippet = (playlist_description[:800] if playlist_description else "None provided")
    prompt = f"""You are an academic course curator analyzing an entire course playlist at once.

Series Title: {raw_series_title}
Uploader/Channel: {uploader or 'Unknown'}
Playlist Description:
{desc_snippet}

Lectures to analyze:
{json.dumps(batch_payload, indent=2)}

Analyze the course and all lectures in ONE batch.
Rules:
1. 'series_title': Preserves university and course code (e.g. 'MIT 6.7960 Deep Learning' or 'Stanford CS229 Machine Learning'). Strip generic tags like [1080p] or (Fall 2024).
2. 'institution': University or organization (e.g. 'MIT', 'Stanford', 'Harvard'). If unknown, null.
3. 'instructors': Array of all professors who lecture in this course (e.g. ['Phillip Isola', 'Sara Beery', 'Jeremy Bernstein']). Look in playlist description and lecture descriptions!
4. 'overview': 1-2 sentence academic overview of the course.
5. 'lectures': Array of objects matching each input lecture by 'source_id':
   - 'source_id': matching source_id
   - 'title': Clean topic title without lecture numbers or resolution tags (e.g. 'Introduction to Deep Learning')
   - 'instructor': Specific professor who delivered this lecture (e.g. 'Sara Beery'). Look in the lecture's description! If not found, null.
   - 'summary': 1-sentence summary of what is taught
   - 'topic_tags': 6-12 deep, granular, and precise academic concept tags. Do NOT stop at generic labels like 'Deep Learning'. Extract specific mathematical principles, algorithms, mechanisms, and architectures (e.g. ['Empirical Risk Minimization', 'Backpropagation', 'Stochastic Gradient Descent', 'Activation Functions', 'ReLU', 'Perceptrons'])
   - 'slides_url': URL to slides if mentioned in description, else null
   - 'resources_url': URL to course page or code if mentioned in description, else null

Respond ONLY with valid JSON. Use null for missing fields. Do NOT hallucinate.
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
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)

            output_lectures: List[BatchLectureItem] = []
            parsed_lectures = parsed.get("lectures", [])

            lookup = {item.get("source_id"): item for item in parsed_lectures if isinstance(item, dict)}

            for idx, lec in enumerate(lectures):
                sid = lec.get("source_id") or str(idx + 1)
                item = lookup.get(sid) or {}
                output_lectures.append(
                    BatchLectureItem(
                        source_id=sid,
                        title=item.get("title") or fallback_lectures[idx].title,
                        instructor=item.get("instructor") or fallback_lectures[idx].instructor,
                        summary=item.get("summary") or fallback_lectures[idx].summary,
                        topic_tags=item.get("topic_tags") or [],
                        slides_url=item.get("slides_url"),
                        resources_url=item.get("resources_url"),
                    )
                )

            return BatchCourseResult(
                series_title=parsed.get("series_title") or fallback_series_title,
                institution=parsed.get("institution") or uploader,
                instructors=parsed.get("instructors") or ([fallback_inst] if fallback_inst else []),
                overview=parsed.get("overview") or f"Course lectures for {fallback_series_title}.",
                lectures=output_lectures,
            )
    except Exception as e:
        logger.warning(f"Batch metadata cleanup failed ({e}); using heuristic fallback")
        return BatchCourseResult(
            series_title=fallback_series_title,
            institution=uploader,
            instructors=[fallback_inst] if fallback_inst else [],
            overview=f"Course lectures for {fallback_series_title}.",
            lectures=fallback_lectures,
        )
