import logging
from typing import Callable, Optional
from vault.db.models import Job
from vault.db.session import async_session_maker
from vault.importers.document import import_pdf_document
from vault.importers.youtube import is_youtube_url, process_youtube_import

logger = logging.getLogger(__name__)


def is_pdf_url(url: str) -> bool:
    """Checks if a URL points to a PDF document."""
    clean_url = url.split("?")[0].lower()
    return clean_url.endswith(".pdf")


async def run_import_job(
    job_id: str,
    url: str,
    series_id: Optional[str] = None,
    title_override: Optional[str] = None,
    is_cancelled_func: Optional[Callable[[], bool]] = None,
) -> None:
    """Dispatcher that inspects the URL and triggers the appropriate import handler."""
    logger.info(f"Starting import job {job_id} for URL: {url}")

    try:
        if is_youtube_url(url):
            await process_youtube_import(
                job_id=job_id,
                url=url,
                target_series_id=series_id,
                title_override=title_override,
                is_cancelled_func=is_cancelled_func,
            )
        elif is_pdf_url(url):
            async with async_session_maker() as session:
                job = await session.get(Job, job_id)
                if job:
                    job.status = "downloading"
                    job.progress_percent = 25.0
                    job.total_items = 1
                    await session.commit()

            resource = await import_pdf_document(
                url=url,
                title_override=title_override,
                series_id=series_id,
            )

            async with async_session_maker() as session:
                job = await session.get(Job, job_id)
                if job:
                    job.status = "completed"
                    job.progress_percent = 100.0
                    job.completed_items = 1
                    await session.commit()
        else:
            # Fallback attempt via yt-dlp (handles Vimeo, university video portals, direct media links)
            await process_youtube_import(
                job_id=job_id,
                url=url,
                target_series_id=series_id,
                title_override=title_override,
                is_cancelled_func=is_cancelled_func,
            )
    except Exception as e:
        logger.exception(f"Unhandled exception in import job {job_id}: {e}")
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(e)
                await session.commit()
