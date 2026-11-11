import asyncio
import html
import io
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse
import httpx
from pypdf import PdfReader
from sqlmodel import select

from vault.ai.fallback import clean_series_title_fallback, sanitize_filename
from vault.config import settings
from vault.db.models import Collection, CollectionItem, Job, Resource, SeriesItem, SourceMetadata, Topic, generate_id
from vault.db.session import async_session_maker
from vault.importers.document import extract_pdf_topics

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
        # 1. Discover all lecture slides and homework files
        lecture_notes = await fetch_ocw_lecture_notes(course_url, client)
        homework_items = await fetch_ocw_homework(course_url, client)
        syllabus_info = await fetch_ocw_syllabus(course_url, client)

    total_files = len(lecture_notes) + len(homework_items)
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
    slides_course_dir = settings.slides_dir / course_folder_name
    slides_course_dir.mkdir(parents=True, exist_ok=True)

    docs_course_dir = settings.documents_dir / course_folder_name
    docs_course_dir.mkdir(parents=True, exist_ok=True)

    downloaded_slides: List[Dict[str, Any]] = []
    downloaded_homework: List[Dict[str, Any]] = []
    completed_count = 0

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        # 3. Download and process Lecture Slide PDFs
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

                # Pair directly with existing Video resource in series if matching order_index
                if target_series_id:
                    stmt_v = select(SeriesItem).where(
                        SeriesItem.parent_series_id == target_series_id,
                        SeriesItem.order_index == lec_idx,
                    )
                    s_item = (await session.exec(stmt_v)).first()
                    if s_item:
                        video_res = await session.get(Resource, s_item.child_resource_id)
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

    # 5. Update Course Collection manifest with all slides and homework links
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
        if job:
            job.status = "completed"
            job.progress_percent = 100.0
            job.completed_items = total_files
            job.current_item = None
            await session.commit()

    logger.info(f"OCW course import completed: {len(downloaded_slides)} slide decks, {len(downloaded_homework)} homework files.")
