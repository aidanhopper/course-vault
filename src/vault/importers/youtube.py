from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import yt_dlp
from sqlmodel import select

from vault.ai.cleaner import clean_course_batch, clean_lecture_metadata, clean_series_metadata
from vault.ai.fallback import clean_title_fallback, sanitize_filename
from vault.config import settings
from vault.db.models import Collection, CollectionItem, Job, Resource, SourceMetadata, Topic, TranscriptCue, generate_id
from vault.db.session import async_session_maker
from vault.jellyfin.client import trigger_jellyfin_refresh
from vault.jellyfin.nfo import write_episode_nfo, write_tvshow_nfo
from vault.schemas.transcript import parse_vtt_content

logger = logging.getLogger(__name__)


def is_youtube_url(url: str) -> bool:
    """Checks if a URL is from YouTube."""
    return any(domain in url for domain in ("youtube.com", "youtu.be"))


def is_playlist_url(url: str) -> bool:
    """Checks if a URL points to a playlist rather than a single video."""
    return ("list=" in url and "list=RD" not in url) or "/playlist" in url


def normalize_youtube_url(url: str) -> str:
    """Normalizes watch?v=...&list=... into a clean playlist URL if a playlist is present."""
    import urllib.parse
    if is_playlist_url(url):
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        list_id = qs.get("list", [None])[0]
        if list_id and not list_id.startswith("RD"):
            return f"https://www.youtube.com/playlist?list={list_id}"
    return url


def extract_youtube_info_flat(url: str) -> Dict[str, Any]:
    """Fast synchronous flat extraction of playlist or video metadata."""
    ydl_opts = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info or {}


def download_single_youtube_video(
    video_url: str,
    output_stem: Path,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Downloads one video with mp4 merge, vtt subtitles, embedded chapters, and thumbnails."""
    outtmpl = f"{output_stem}.%(ext)s"

    ydl_opts: Dict[str, Any] = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en.*", "en"],
        "subtitlesformat": "vtt",
        "embedchapters": True,
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }

    if progress_callback:
        ydl_opts["progress_hooks"] = [progress_callback]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        return info or {}


async def process_youtube_import(
    job_id: str,
    url: str,
    target_collection_id: Optional[str] = None,
    title_override: Optional[str] = None,
    is_cancelled_func: Optional[Callable[[], bool]] = None,
) -> None:
    """Full background import workflow for YouTube playlists and single videos."""
    url = normalize_youtube_url(url)

    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        if not job:
            return
        job.status = "extracting"
        job.progress_percent = 5.0
        await session.commit()

    # 1. Flat metadata extraction in worker thread
    try:
        raw_info = await asyncio.to_thread(extract_youtube_info_flat, url)
    except Exception as e:
        logger.error(f"Failed to inspect YouTube URL {url}: {e}")
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error_message = f"Metadata extraction failed: {str(e)}"
                await session.commit()
        return

    # Check for playlist entries vs single video
    entries: List[Dict[str, Any]] = []
    if "entries" in raw_info and raw_info["entries"]:
        entries = [e for e in raw_info["entries"] if e]
        raw_series_title = raw_info.get("title") or "YouTube Course"
        uploader = raw_info.get("uploader") or raw_info.get("channel")
    else:
        # Single video treated as a 1-entry list
        entries = [raw_info]
        raw_series_title = raw_info.get("title") or "YouTube Lecture"
        uploader = raw_info.get("uploader") or raw_info.get("channel")

    if not entries:
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error_message = "No downloadable videos found in URL"
                await session.commit()
        return

    # 2. AI Metadata Cleanup
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        if job:
            job.status = "cleaning"
            job.progress_percent = 10.0
            await session.commit()

    raw_video_titles = [e.get("title") or f"Lecture {idx + 1}" for idx, e in enumerate(entries)]
    cleaned_meta = await clean_series_metadata(
        raw_series_title=title_override or raw_series_title,
        raw_video_titles=raw_video_titles,
        instructor_hint=uploader,
        playlist_description=raw_info.get("description"),
    )

    final_series_title = title_override or cleaned_meta.series_title
    clean_folder_name = sanitize_filename(final_series_title)
    series_dir = settings.videos_dir / clean_folder_name
    series_dir.mkdir(parents=True, exist_ok=True)

    # 3. Resolve or create parent Course Collection
    async with async_session_maker() as session:
        course_col = None
        if target_collection_id:
            course_col = await session.get(Collection, target_collection_id)

        if not course_col:
            stmt_col = select(Collection).where(Collection.title == final_series_title)
            course_col = (await session.exec(stmt_col)).first()

        if not course_col:
            course_col = Collection(
                title=final_series_title,
                context="course",
            )
            course_col.data = {
                "institution": cleaned_meta.institution,
                "instructors": cleaned_meta.instructors,
                "channel": uploader,
                "overview": cleaned_meta.overview,
            }
            session.add(course_col)
            await session.commit()
            await session.refresh(course_col)
        target_collection_id = course_col.id

        job = await session.get(Job, job_id)
        if job:
            job.collection_id = target_collection_id
            job.status = "downloading"
            job.total_items = len(entries)
            job.completed_items = 0
            job.progress_percent = 15.0
            await session.commit()

    # Write tvshow.nfo for Jellyfin
    write_tvshow_nfo(
        series_dir=series_dir,
        title=final_series_title,
        overview=cleaned_meta.overview,
        instructors=cleaned_meta.instructors,
        institution=cleaned_meta.institution,
    )

    # 4. Gather available descriptions and run a single batched metadata call
    lectures_batch_input = []
    async with async_session_maker() as session:
        for idx, entry in enumerate(entries):
            vid = entry.get("id")
            desc = entry.get("description") or ""
            if not desc and vid:
                stmt_s = select(SourceMetadata).where(SourceMetadata.source_id == vid)
                sm = (await session.exec(stmt_s)).first()
                if sm:
                    desc = sm.raw_payload.get("description") or ""
            lectures_batch_input.append({
                "source_id": vid or str(idx + 1),
                "title": entry.get("title") or f"Lecture {idx + 1}",
                "description": desc,
            })

    batch_lookup = {}
    if any(bool(l["description"]) for l in lectures_batch_input):
        logger.info(f"Running batched AI metadata cleanup for {len(lectures_batch_input)} lectures...")
        batch_res = await clean_course_batch(
            raw_series_title=title_override or raw_series_title,
            uploader=uploader,
            playlist_description=raw_info.get("description"),
            lectures=lectures_batch_input,
        )
        batch_lookup = {l.source_id: l for l in batch_res.lectures}

    # 5. Download and process each video sequentially
    total_videos = len(entries)
    for idx, entry in enumerate(entries):
        if is_cancelled_func and is_cancelled_func():
            logger.info(f"Job {job_id} cancelled by user")
            async with async_session_maker() as session:
                job = await session.get(Job, job_id)
                if job:
                    job.status = "cancelled"
                    await session.commit()
            return

        video_id = entry.get("id")
        video_url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
        clean_title = (
            cleaned_meta.video_titles[idx]
            if idx < len(cleaned_meta.video_titles)
            else clean_title_fallback(entry.get("title") or f"Lecture {idx + 1}", index=idx + 1)
        )
        # Strip redundant leading numbering (e.g. '01. Intro' -> 'Intro') for the episode title
        import re
        clean_topic_title = re.sub(r"^\d+[\.\s\-–—]+\s*", "", clean_title).strip() or clean_title
        safe_video_name = sanitize_filename(clean_topic_title)

        # Update job progress
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job:
                job.current_item = clean_topic_title
                job.progress_percent = 15.0 + (idx / total_videos) * 80.0
                await session.commit()

        # Check deduplication: does this video already exist in DB with a file on disk?
        existing_res_id: Optional[str] = None
        async with async_session_maker() as session:
            stmt = select(SourceMetadata).where(
                SourceMetadata.provider == "youtube",
                SourceMetadata.source_id == video_id,
            )
            res = (await session.exec(stmt)).first()
            if res:
                existing_res = await session.get(Resource, res.resource_id)
                if existing_res:
                    rel_path = existing_res.data.get("file_path")
                    if rel_path and (settings.MEDIA_DIR / rel_path).is_file():
                        existing_res_id = existing_res.id

        if existing_res_id:
            # Re-use existing video file, but refresh and ensure metadata and NFO are up-to-date
            async with async_session_maker() as session:
                existing_res = await session.get(Resource, existing_res_id)
                b_meta = batch_lookup.get(video_id)

                if b_meta:
                    final_title = b_meta.title or clean_topic_title
                    inst_list = [b_meta.instructor] if b_meta.instructor else []
                    summ = b_meta.summary
                    tags = b_meta.topic_tags
                    slides = b_meta.slides_url
                    res_url = b_meta.resources_url
                else:
                    stmt_src = select(SourceMetadata).where(SourceMetadata.resource_id == existing_res_id)
                    src_meta_rec = (await session.exec(stmt_src)).first()
                    raw_desc = (src_meta_rec.raw_payload.get("description") if src_meta_rec else "") or ""
                    lecture_meta = await clean_lecture_metadata(
                        raw_title=clean_title,
                        raw_description=raw_desc,
                        channel=uploader,
                        series_institution=cleaned_meta.institution,
                    )
                    final_title = lecture_meta.title or clean_topic_title
                    inst_list = lecture_meta.instructors
                    summ = lecture_meta.summary
                    tags = lecture_meta.topic_tags
                    slides = lecture_meta.slides_url
                    res_url = lecture_meta.resources_url

                if existing_res:
                    existing_res.title = final_title
                    data = existing_res.data
                    data["instructors"] = inst_list
                    data.pop("instructor", None)  # Remove legacy singular field
                    data["institution"] = cleaned_meta.institution
                    data["summary"] = summ
                    data["topic_tags"] = tags
                    data["slides_url"] = slides
                    data["resources_url"] = res_url
                    existing_res.data = data
                    session.add(existing_res)

                    # Update episode.nfo file if present
                    rel_p = data.get("file_path")
                    if rel_p:
                        nfo_p = (settings.MEDIA_DIR / rel_p).with_suffix(".nfo")
                        write_episode_nfo(
                            nfo_path=nfo_p,
                            title=final_title,
                            episode_number=idx + 1,
                            plot=summ or final_title,
                            instructors=inst_list,
                            tags=tags,
                        )

                # Ensure linked to course Collection with context 'lecture_video'
                stmt_ci = select(CollectionItem).where(
                    CollectionItem.collection_id == target_collection_id,
                    CollectionItem.resource_id == existing_res_id,
                )
                if not (await session.exec(stmt_ci)).first():
                    c_item = CollectionItem(
                        collection_id=target_collection_id,
                        resource_id=existing_res_id,
                        context="lecture_video",
                        order_index=idx + 1,
                    )
                    session.add(c_item)

                job = await session.get(Job, job_id)
                if job:
                    job.completed_items = idx + 1
                    await session.commit()
            continue

        # Target file stem for Jellyfin naming: {Series Title} - E{XX} - {Clean Topic}
        episode_stem = series_dir / f"{clean_folder_name} - E{idx + 1:02d} - {safe_video_name}"

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

        # Locate the downloaded media file (.mp4 or other)
        mp4_file = Path(f"{episode_stem}.mp4")
        if not mp4_file.is_file():
            # Check for any extension matched
            matched_files = glob.glob(f"{glob.escape(str(episode_stem))}.*")
            media_matches = [
                Path(p) for p in matched_files
                if Path(p).suffix.lower() in (".mp4", ".mkv", ".webm", ".m4v")
            ]
            if media_matches:
                mp4_file = media_matches[0]
            else:
                logger.warning(f"No media file found after download for {episode_stem}")
                continue

        # Check for subtitles and clean up variant duplicates (.en-orig.vtt, etc.)
        for extra_vtt in glob.glob(f"{glob.escape(str(episode_stem))}*.en-*.vtt"):
            try:
                os.unlink(extra_vtt)
            except Exception:
                pass

        has_subtitles = False
        vtt_file: Optional[Path] = None
        primary_vtt = Path(f"{episode_stem}.en.vtt")
        if primary_vtt.is_file():
            vtt_file = primary_vtt
            has_subtitles = True
        else:
            vtt_matches = glob.glob(f"{glob.escape(str(episode_stem))}*.vtt")
            if vtt_matches:
                vtt_file = Path(vtt_matches[0])
                has_subtitles = True

        duration_sec = dl_info.get("duration") or entry.get("duration")
        raw_description = dl_info.get("description") or ""

        # Extract rich per-lecture metadata (specific professor, summary, topics, slides)
        lecture_meta = await clean_lecture_metadata(
            raw_title=clean_title,
            raw_description=raw_description,
            channel=uploader,
            series_institution=cleaned_meta.institution,
        )
        final_lecture_title = lecture_meta.title or clean_topic_title

        # Write episode.nfo for Jellyfin
        nfo_path = Path(f"{episode_stem}.nfo")
        write_episode_nfo(
            nfo_path=nfo_path,
            title=final_lecture_title,
            episode_number=idx + 1,
            plot=lecture_meta.summary or (raw_description[:1000] if raw_description else final_lecture_title),
            instructors=lecture_meta.instructors,
            tags=lecture_meta.topic_tags,
        )

        # Create Resource in DB
        video_resource_id = generate_id("res")
        video_resource = Resource(
            id=video_resource_id,
            type="video",
            title=final_lecture_title,
            original_title=entry.get("title") or clean_title,
        )
        video_resource.data = {
            "duration_seconds": duration_sec,
            "file_path": str(mp4_file.relative_to(settings.MEDIA_DIR)),
            "instructors": lecture_meta.instructors,
            "institution": lecture_meta.institution,
            "summary": lecture_meta.summary,
            "topic_tags": lecture_meta.topic_tags,
            "slides_url": lecture_meta.slides_url,
            "resources_url": lecture_meta.resources_url,
            "has_subtitles": has_subtitles,
            "has_embedded_chapters": bool(dl_info.get("chapters")),
        }

        async with async_session_maker() as session:
            session.add(video_resource)

            # Link to course Collection with context 'lecture_video'
            c_item = CollectionItem(
                collection_id=target_collection_id,
                resource_id=video_resource_id,
                context="lecture_video",
                order_index=idx + 1,
            )
            session.add(c_item)

            # Save SourceMetadata
            src_meta = SourceMetadata(
                resource_id=video_resource_id,
                provider="youtube",
                source_url=video_url,
                source_id=video_id,
            )
            try:
                sanitized_payload = yt_dlp.YoutubeDL().sanitize_info(dl_info or entry)
            except Exception:
                sanitized_payload = entry
            src_meta.raw_payload = sanitized_payload
            session.add(src_meta)

            # Parse Chapters into Topics
            chapters = dl_info.get("chapters") or []
            for ch_idx, ch in enumerate(chapters):
                topic = Topic(
                    resource_id=video_resource_id,
                    title=ch.get("title") or f"Topic {ch_idx + 1}",
                    start_seconds=float(ch.get("start_time", 0)),
                    end_seconds=float(ch.get("end_time", 0)),
                    order_index=ch_idx + 1,
                )
                session.add(topic)

            # Parse Subtitles into Transcript Cues
            if vtt_file and vtt_file.is_file():
                try:
                    vtt_text = vtt_file.read_text(encoding="utf-8", errors="replace")
                    cues = parse_vtt_content(vtt_text)
                    for cue in cues:
                        cue_record = TranscriptCue(
                            resource_id=video_resource_id,
                            start_seconds=cue.start_seconds,
                            end_seconds=cue.end_seconds,
                            text=cue.text,
                        )
                        session.add(cue_record)
                except Exception as e:
                    logger.warning(f"Could not parse VTT transcript for {video_id}: {e}")

            # Update job item count
            job = await session.get(Job, job_id)
            if job:
                job.completed_items = idx + 1
                await session.commit()

    # 5. Mark Job as Completed
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        if job:
            job.status = "completed"
            job.progress_percent = 100.0
            job.current_item = None
            await session.commit()

    # 6. Trigger Jellyfin library refresh if configured
    await trigger_jellyfin_refresh()
