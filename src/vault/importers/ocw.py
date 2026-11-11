import asyncio
import glob
import html
import io
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse
import httpx
from pypdf import PdfReader
from sqlmodel import select
import yt_dlp

from vault.ai.cleaner import clean_course_batch, clean_lecture_metadata
from vault.ai.fallback import clean_series_title_fallback, sanitize_filename
from vault.config import settings
from vault.db.models import Collection, CollectionItem, Job, Resource, SourceMetadata, Topic, TranscriptCue, generate_id
from vault.db.session import async_session_maker
from vault.importers.document import extract_pdf_topics
from vault.importers.youtube import download_single_youtube_video
from vault.jellyfin.client import trigger_jellyfin_refresh
from vault.jellyfin.nfo import write_episode_nfo, write_tvshow_nfo
from vault.schemas.transcript import parse_vtt_content

logger = logging.getLogger(__name__)


def is_ocw_url(url: str) -> bool:
    """Checks if a URL points to an MIT OpenCourseWare course page."""
    return "ocw.mit.edu/courses/" in url


def normalize_ocw_course_url(url: str) -> str:
    """Normalizes any OCW subpage URL into the root course URL: https://ocw.mit.edu/courses/{slug}/"""
    parsed = urlparse(url)
    match = re.search(r"(/courses/[^/]+)/?", parsed.path)
    if match:
        course_path = match.group(1).rstrip("/") + "/"
        return f"https://ocw.mit.edu{course_path}"
    return url


async def fetch_ocw_lecture_notes(course_url: str, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Crawls /lists/lecture-notes/ and extracts all lecture slide/note PDF download links."""
    notes_url = urljoin(course_url, "lists/lecture-notes/")
    try:
        resp = await client.get(notes_url)
        if resp.status_code != 200:
            return []
    except Exception as e:
        logger.warning(f"Could not fetch OCW lecture-notes page: {e}")
        return []

    # Find each resource item container
    items = re.findall(
        r'<div class=[\"\']resource-item resource-list-page[\"\']>(.*?)</div>\s*</div>\s*</div>',
        resp.text,
        re.DOTALL,
    )

    results: List[Dict[str, Any]] = []
    for it in items:
        # Extract direct PDF download link
        dl_match = re.search(r'href=[\"\']([^\"\']+\.pdf)[\"\']', it)
        if not dl_match:
            continue
        pdf_url = urljoin(course_url, dl_match.group(1))

        # Extract title
        title_match = re.search(r'<a[^>]+href=[\"\'][^\"\']*resources/[^\"\']*[\"\'][^>]*>(.*?)</a>', it, re.DOTALL)
        raw_title = re.sub(r'<[^>]+>', ' ', title_match.group(1)).strip() if title_match else ""
        raw_title = html.unescape(" ".join(raw_title.split()))

        # Extract lecture number (e.g. "Lecture 1: Intro" -> index 1)
        lec_num_match = re.search(r"\b(?:lec|lecture)\.?\s*0*(\d+)", raw_title, flags=re.IGNORECASE)
        index = int(lec_num_match.group(1)) if lec_num_match else len(results) + 1

        # Clean title
        clean_title = re.sub(r"^(?:lec(?:ture)?\.?\s*0*\d+[\s:\-–—]+)", "", raw_title, flags=re.IGNORECASE).strip() or raw_title

        results.append({
            "index": index,
            "raw_title": raw_title,
            "title": clean_title,
            "pdf_url": pdf_url,
        })

    # Sort sequentially by lecture index
    results.sort(key=lambda x: x["index"])
    return results


async def fetch_ocw_homework(course_url: str, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Crawls /lists/homework/ and extracts all homework problem sets and solutions."""
    hw_url = urljoin(course_url, "lists/homework/")
    try:
        resp = await client.get(hw_url)
        if resp.status_code != 200:
            return []
    except Exception as e:
        logger.warning(f"Could not fetch OCW homework page: {e}")
        return []

    items = re.findall(
        r'<div class=[\"\']resource-item resource-list-page[\"\']>(.*?)</div>\s*</div>\s*</div>',
        resp.text,
        re.DOTALL,
    )

    results: List[Dict[str, Any]] = []
    for it in items:
        dl_match = re.search(r'href=[\"\']([^\"\']+\.(?:pdf|zip))[\"\']', it)
        if not dl_match:
            continue
        file_url = urljoin(course_url, dl_match.group(1))

        title_match = re.search(r'<a[^>]+href=[\"\'][^\"\']*resources/[^\"\']*[\"\'][^>]*>(.*?)</a>', it, re.DOTALL)
        raw_title = re.sub(r'<[^>]+>', ' ', title_match.group(1)).strip() if title_match else ""
        raw_title = html.unescape(" ".join(raw_title.split()))

        is_solution = "solution" in raw_title.lower() or "_sol" in file_url.lower()

        results.append({
            "title": raw_title or Path(file_url).name,
            "file_url": file_url,
            "is_solution": is_solution,
        })

    return results


async def fetch_ocw_syllabus(course_url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Crawls /pages/syllabus/ and extracts course description and prerequisites."""
    syl_url = urljoin(course_url, "pages/syllabus/")
    try:
        resp = await client.get(syl_url)
        if resp.status_code != 200:
            return {}
    except Exception:
        return {}

    main_match = re.search(r'<main[^>]*>(.*?)</main>', resp.text, re.DOTALL)
    if not main_match:
        return {}

    raw_html = main_match.group(1)
    text = " ".join(re.sub(r'<[^>]+>', ' ', raw_html).split())

    prereqs = None
    prereq_match = re.search(r"Prerequisites\s*(.*?)(?:Course Description|Course Meeting Times|Grading|$)", text, flags=re.IGNORECASE)
    if prereq_match:
        prereqs = prereq_match.group(1).strip()

    return {
        "prerequisites": prereqs,
        "syllabus_snippet": text[:500],
    }


async def fetch_ocw_videos(course_url: str, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Crawls /video_galleries/lecture-videos/ and discovers all lecture video pages and their YouTube IDs."""
    gallery_url = urljoin(course_url, "video_galleries/lecture-videos/")
    try:
        resp = await client.get(gallery_url)
        if resp.status_code != 200:
            return []
    except Exception as e:
        logger.warning(f"Could not fetch OCW video gallery page: {e}")
        return []

    # Find all links to resource mp4 subpages
    links = re.findall(r'<a[^>]+href=[\"\'](/courses/[^\"\']+_mp4/)[\"\'][^>]*>(.*?)</a>', resp.text, re.DOTALL)
    if not links:
        return []

    # Deduplicate links while preserving order
    seen_hrefs = set()
    unique_links = []
    for href, label in links:
        if href not in seen_hrefs:
            seen_hrefs.add(href)
            unique_links.append((href, label))

    async def fetch_video_details(href: str, label: str) -> Optional[Dict[str, Any]]:
        clean_label = html.unescape(" ".join(re.sub(r'<[^>]+>', ' ', label).split()))
        lec_m = re.search(r"\b(?:lec|lecture)\.?\s*0*(\d+)", clean_label, flags=re.IGNORECASE)
        idx = int(lec_m.group(1)) if lec_m else 0
        clean_title = re.sub(r"^(?:lec(?:ture)?\.?\s*0*\d+[\s:\-–—]+)", "", clean_label, flags=re.IGNORECASE).strip() or clean_label

        page_url = urljoin(course_url, href)
        try:
            p_resp = await client.get(page_url)
            if p_resp.status_code != 200:
                return None
            m = re.search(r'video-player-([a-zA-Z0-9_-]{11})-', p_resp.text)
            yt_id = m.group(1) if m else None
            if not yt_id:
                m_alt = re.search(r'(?:youtube\.com/(?:watch\?v=|embed/)|youtu\.be/)([a-zA-Z0-9_-]{11})', p_resp.text)
                yt_id = m_alt.group(1) if m_alt else None

            if yt_id:
                return {
                    "index": idx,
                    "title": clean_title,
                    "youtube_id": yt_id,
                    "video_url": f"https://www.youtube.com/watch?v={yt_id}",
                    "ocw_page_url": page_url,
                }
        except Exception as e:
            logger.warning(f"Error resolving OCW video page {page_url}: {e}")
        return None

    tasks = [fetch_video_details(href, label) for href, label in unique_links]
    results = await asyncio.gather(*tasks)
    videos = [r for r in results if r is not None]
    videos.sort(key=lambda x: x["index"])
    return videos


async def process_ocw_import(
    job_id: str,
    url: str,
    series_id: Optional[str] = None,
    title_override: Optional[str] = None,
    is_cancelled_func: Optional[Callable[[], bool]] = None,
) -> None:
    """Full background import workflow for MIT OpenCourseWare course pages."""
    course_url = normalize_ocw_course_url(url)
    slug = [p for p in urlparse(course_url).path.split("/") if p][-1]

    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        if job:
            job.status = "extracting"
            job.progress_percent = 5.0
            await session.commit()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # 1. Discover all lecture videos, slides, and homework files
        lecture_videos = await fetch_ocw_videos(course_url, client)
        lecture_notes = await fetch_ocw_lecture_notes(course_url, client)
        homework_items = await fetch_ocw_homework(course_url, client)
        syllabus_info = await fetch_ocw_syllabus(course_url, client)

    total_files = len(lecture_videos) + len(lecture_notes) + len(homework_items)
    if total_files == 0:
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error_message = f"No downloadable materials found on OCW course page {course_url}"
                await session.commit()
        return

    # 2. Resolve or Create Course Collection
    async with async_session_maker() as session:
        clean_title, _ = clean_series_title_fallback(slug.replace("-", " ").title())
        final_title = title_override or clean_title

        # Resolve or create parent Collection(context='course')
        stmt_col = select(Collection).where(Collection.title == final_title)
        course_col = (await session.exec(stmt_col)).first()
        if not course_col:
            all_cols = (await session.exec(select(Collection))).all()
            for c in all_cols:
                if slug.replace("-", " ") in c.title.lower() or "6.7960" in c.title:
                    course_col = c
                    break

        if not course_col:
            course_col = Collection(
                title=final_title,
                context="course",
            )
            course_col.data = {
                "institution": "MIT",
                "course_url": course_url,
                "prerequisites": syllabus_info.get("prerequisites"),
            }
            session.add(course_col)
            await session.commit()
            await session.refresh(course_col)
        else:
            cdata = course_col.data
            if syllabus_info.get("prerequisites"):
                cdata["prerequisites"] = syllabus_info["prerequisites"]
            cdata["course_url"] = course_url
            course_col.data = cdata
            session.add(course_col)
            await session.commit()

        target_collection_id = course_col.id
        course_folder_name = sanitize_filename(course_col.title)

        job = await session.get(Job, job_id)
        if job:
            job.collection_id = target_collection_id
            job.status = "downloading"
            job.total_items = total_files
            job.completed_items = 0
            job.progress_percent = 15.0
            await session.commit()

    # Create target directories
    videos_course_dir = settings.videos_dir / course_folder_name
    videos_course_dir.mkdir(parents=True, exist_ok=True)

    slides_course_dir = settings.slides_dir / course_folder_name
    slides_course_dir.mkdir(parents=True, exist_ok=True)

    docs_course_dir = settings.documents_dir / course_folder_name
    docs_course_dir.mkdir(parents=True, exist_ok=True)

    if lecture_videos:
        write_tvshow_nfo(
            series_dir=videos_course_dir,
            title=course_col.title,
            overview=syllabus_info.get("syllabus_snippet"),
            institution="MIT",
        )

    downloaded_videos: List[Dict[str, Any]] = []
    downloaded_slides: List[Dict[str, Any]] = []
    downloaded_homework: List[Dict[str, Any]] = []
    completed_count = 0

    # 3. Download and process Lecture Videos
    for vid_entry in lecture_videos:
        if is_cancelled_func and is_cancelled_func():
            return

        lec_idx = vid_entry["index"]
        clean_title = vid_entry["title"]
        video_url = vid_entry["video_url"]
        video_id = vid_entry["youtube_id"]
        safe_video_name = sanitize_filename(clean_title)
        episode_stem = videos_course_dir / f"{course_folder_name} - E{lec_idx:02d} - {safe_video_name}"

        # Update job progress
        completed_count += 1
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job:
                job.current_item = f"Lecture {lec_idx} Video"
                job.completed_items = completed_count
                job.progress_percent = 15.0 + (completed_count / total_files) * 80.0
                await session.commit()

        # Check deduplication
        existing_res_id = None
        async with async_session_maker() as session:
            stmt = select(SourceMetadata).where(
                SourceMetadata.provider == "youtube",
                SourceMetadata.source_id == video_id,
            )
            res = (await session.exec(stmt)).first()
            if res:
                existing_res = await session.get(Resource, res.resource_id)
                if existing_res:
                    rel_p = existing_res.data.get("file_path")
                    if rel_p and (settings.MEDIA_DIR / rel_p).is_file():
                        existing_res_id = existing_res.id

        if existing_res_id:
            async with async_session_maker() as session:
                stmt_ci = select(CollectionItem).where(
                    CollectionItem.collection_id == target_collection_id,
                    CollectionItem.resource_id == existing_res_id,
                )
                if not (await session.exec(stmt_ci)).first():
                    session.add(CollectionItem(
                        collection_id=target_collection_id,
                        resource_id=existing_res_id,
                        context="lecture_video",
                        order_index=lec_idx,
                    ))
                    await session.commit()
            downloaded_videos.append({"index": lec_idx, "resource_id": existing_res_id, "title": clean_title})
            continue

        # Download with yt-dlp in worker thread
        try:
            dl_info = await asyncio.to_thread(
                download_single_youtube_video,
                video_url=video_url,
                output_stem=episode_stem,
            )
        except Exception as e:
            logger.warning(f"Error downloading video {video_id} ({clean_title}): {e}")
            continue

        mp4_file = Path(f"{episode_stem}.mp4")
        if not mp4_file.is_file():
            matched_files = glob.glob(f"{glob.escape(str(episode_stem))}.*")
            media_matches = [
                Path(p) for p in matched_files
                if Path(p).suffix.lower() in (".mp4", ".mkv", ".webm", ".m4v")
            ]
            if media_matches:
                mp4_file = media_matches[0]
            else:
                continue

        # Clean duplicate VTTs and resolve primary
        for extra_vtt in glob.glob(f"{glob.escape(str(episode_stem))}*.en-*.vtt"):
            try:
                os.unlink(extra_vtt)
            except Exception:
                pass

        vtt_file = Path(f"{episode_stem}.en.vtt")
        has_subtitles = vtt_file.is_file()
        duration_sec = dl_info.get("duration")
        raw_desc = dl_info.get("description") or ""

        # Rich AI Metadata for this lecture
        lecture_meta = await clean_lecture_metadata(
            raw_title=clean_title,
            raw_description=raw_desc,
            channel="MIT OpenCourseWare",
            series_institution="MIT",
        )
        final_video_title = lecture_meta.title or clean_title

        # Write episode.nfo
        nfo_path = Path(f"{episode_stem}.nfo")
        write_episode_nfo(
            nfo_path=nfo_path,
            title=final_video_title,
            episode_number=lec_idx,
            plot=lecture_meta.summary or raw_desc[:1000],
            instructors=lecture_meta.instructors,
            tags=lecture_meta.topic_tags,
        )

        video_res_id = generate_id("res")
        video_resource = Resource(
            id=video_res_id,
            type="video",
            title=final_video_title,
            original_title=clean_title,
        )
        video_resource.data = {
            "duration_seconds": duration_sec,
            "file_path": str(mp4_file.relative_to(settings.MEDIA_DIR)),
            "instructors": lecture_meta.instructors,
            "institution": "MIT",
            "summary": lecture_meta.summary,
            "topic_tags": lecture_meta.topic_tags,
            "slides_url": lecture_meta.slides_url,
            "resources_url": lecture_meta.resources_url,
            "has_subtitles": has_subtitles,
            "has_embedded_chapters": bool(dl_info.get("chapters")),
        }

        async with async_session_maker() as session:
            session.add(video_resource)
            session.add(CollectionItem(
                collection_id=target_collection_id,
                resource_id=video_res_id,
                context="lecture_video",
                order_index=lec_idx,
            ))

            src_meta = SourceMetadata(
                resource_id=video_res_id,
                provider="youtube",
                source_url=video_url,
                source_id=video_id,
            )
            try:
                src_meta.raw_payload = yt_dlp.YoutubeDL().sanitize_info(dl_info)
            except Exception:
                src_meta.raw_payload = {}
            session.add(src_meta)

            for ch_idx, ch in enumerate(dl_info.get("chapters") or []):
                session.add(Topic(
                    resource_id=video_res_id,
                    title=ch.get("title") or f"Topic {ch_idx + 1}",
                    start_seconds=float(ch.get("start_time", 0)),
                    end_seconds=float(ch.get("end_time", 0)),
                    order_index=ch_idx + 1,
                ))

            if has_subtitles:
                try:
                    cues = parse_vtt_content(vtt_file.read_text(encoding="utf-8", errors="replace"))
                    for cue in cues:
                        session.add(TranscriptCue(
                            resource_id=video_res_id,
                            start_seconds=cue.start_seconds,
                            end_seconds=cue.end_seconds,
                            text=cue.text,
                        ))
                except Exception:
                    pass

            await session.commit()

        downloaded_videos.append({"index": lec_idx, "resource_id": video_res_id, "title": final_video_title})

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        # 4. Download and process Lecture Slide PDFs
        for lec in lecture_notes:
            if is_cancelled_func and is_cancelled_func():
                return

            lec_idx = lec["index"]
            lec_title = lec["title"]
            pdf_url = lec["pdf_url"]
            safe_title = sanitize_filename(lec_title)
            filename = f"{course_folder_name} - L{lec_idx:02d} - {safe_title}.pdf"
            file_path = slides_course_dir / filename

            # Download if not already on disk
            if not file_path.is_file():
                try:
                    resp = await client.get(pdf_url)
                    resp.raise_for_status()
                    file_path.write_bytes(resp.content)
                except Exception as e:
                    logger.warning(f"Error downloading lecture slides {pdf_url}: {e}")
                    continue

            # Read PDF page count and outline
            page_count = None
            extracted_topics = []
            try:
                reader = PdfReader(str(file_path))
                page_count = len(reader.pages)
                extracted_topics = extract_pdf_topics(reader)
            except Exception as e:
                logger.warning(f"Error reading PDF {file_path}: {e}")

            # Create or update slides Resource in DB
            slide_res_id = generate_id("res")
            slide_res = Resource(
                id=slide_res_id,
                type="slides",
                title=f"{course_col.title} - Lecture {lec_idx:02d} Slides: {lec_title}",
            )
            slide_res.data = {
                "lecture_index": lec_idx,
                "slide_count": page_count,
                "file_format": "pdf",
                "file_path": str(file_path.relative_to(settings.MEDIA_DIR)),
                "institution": "MIT",
                "source_url": pdf_url,
            }

            async with async_session_maker() as session:
                session.add(slide_res)
                for top in extracted_topics:
                    t_record = Topic(
                        resource_id=slide_res_id,
                        title=top["title"],
                        start_page=top["start_page"],
                        end_page=top["end_page"],
                        order_index=top["order_index"],
                    )
                    session.add(t_record)

                # Pair directly with Video resource in Collection if matching order_index
                stmt_v = select(CollectionItem).where(
                    CollectionItem.collection_id == target_collection_id,
                    CollectionItem.context == "lecture_video",
                    CollectionItem.order_index == lec_idx,
                )
                ci_v = (await session.exec(stmt_v)).first()
                if ci_v:
                    video_res = await session.get(Resource, ci_v.resource_id)
                    if video_res and video_res.type == "video":
                        vdata = video_res.data
                        vdata["slides_resource_id"] = slide_res_id
                        vdata["slides_file_path"] = str(file_path.relative_to(settings.MEDIA_DIR))
                        vdata["slides_url"] = pdf_url
                        video_res.data = vdata
                        session.add(video_res)

                # Add to Collection with context 'companion_slides'
                stmt_ci = select(CollectionItem).where(
                    CollectionItem.collection_id == target_collection_id,
                    CollectionItem.resource_id == slide_res_id,
                )
                if not (await session.exec(stmt_ci)).first():
                    c_item = CollectionItem(
                        collection_id=target_collection_id,
                        resource_id=slide_res_id,
                        context="companion_slides",
                        order_index=lec_idx,
                    )
                    session.add(c_item)

                await session.commit()

            downloaded_slides.append({
                "lecture_index": lec_idx,
                "resource_id": slide_res_id,
                "title": lec_title,
                "file_path": str(file_path.relative_to(settings.MEDIA_DIR)),
            })

            completed_count += 1
            async with async_session_maker() as session:
                job = await session.get(Job, job_id)
                if job:
                    job.completed_items = completed_count
                    job.current_item = f"Lecture {lec_idx} Slides"
                    job.progress_percent = 15.0 + (completed_count / total_files) * 80.0
                    await session.commit()

        # 4. Download and process Homework Documents
        for hw in homework_items:
            if is_cancelled_func and is_cancelled_func():
                return

            hw_title = hw["title"]
            file_url = hw["file_url"]
            ext = Path(file_url).suffix or ".pdf"
            safe_hw_name = sanitize_filename(hw_title)
            filename = f"{safe_hw_name}{ext}"
            file_path = docs_course_dir / filename

            if not file_path.is_file():
                try:
                    resp = await client.get(file_url)
                    resp.raise_for_status()
                    file_path.write_bytes(resp.content)
                except Exception as e:
                    logger.warning(f"Error downloading homework {file_url}: {e}")
                    continue

            hw_res_id = generate_id("res")
            hw_res = Resource(
                id=hw_res_id,
                type="document",
                title=f"{course_col.title} - {hw_title}",
            )
            hw_res.data = {
                "file_format": ext.lstrip("."),
                "file_path": str(file_path.relative_to(settings.MEDIA_DIR)),
                "is_solution": hw["is_solution"],
                "institution": "MIT",
                "source_url": file_url,
            }

            async with async_session_maker() as session:
                session.add(hw_res)
                # Link to Collection with context 'solution' or 'problem_set'
                c_item = CollectionItem(
                    collection_id=target_collection_id,
                    resource_id=hw_res_id,
                    context="solution" if hw["is_solution"] else "problem_set",
                    order_index=completed_count + 1,
                )
                session.add(c_item)
                await session.commit()

            downloaded_homework.append({
                "resource_id": hw_res_id,
                "title": hw_title,
                "is_solution": hw["is_solution"],
                "file_path": str(file_path.relative_to(settings.MEDIA_DIR)),
            })

            completed_count += 1
            async with async_session_maker() as session:
                job = await session.get(Job, job_id)
                if job:
                    job.completed_items = completed_count
                    job.current_item = hw_title
                    job.progress_percent = 15.0 + (completed_count / total_files) * 80.0
                    await session.commit()

    # 6. Update Course Collection manifest with all slides and homework links
    async with async_session_maker() as session:
        course_col = await session.get(Collection, target_collection_id)
        if course_col:
            cdata = course_col.data
            cdata["slides"] = downloaded_slides
            cdata["documents"] = downloaded_homework
            course_col.data = cdata
            session.add(course_col)

        job = await session.get(Job, job_id)
        if job:
            job.status = "completed"
            job.progress_percent = 100.0
            job.completed_items = total_files
            job.current_item = None
            await session.commit()

    await trigger_jellyfin_refresh()
    logger.info(f"OCW course import completed: {len(downloaded_videos)} videos, {len(downloaded_slides)} slide decks, {len(downloaded_homework)} homework files.")
