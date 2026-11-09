import asyncio
import logging
from typing import Dict, Optional
from vault.importers.router import run_import_job

logger = logging.getLogger(__name__)


class JobQueue:
    """In-memory asyncio worker queue for handling background import tasks."""

    def __init__(self, max_concurrent: int = 1):
        self.queue: asyncio.Queue[Dict[str, Optional[str]]] = asyncio.Queue()
        self.max_concurrent = max_concurrent
        self._cancelled_jobs: set[str] = set()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        """Starts the background worker task."""
        if not self._running:
            self._running = True
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("Background JobQueue worker started")

    async def stop(self) -> None:
        """Stops the background worker task gracefully."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            logger.info("Background JobQueue worker stopped")

    async def enqueue(
        self,
        job_id: str,
        url: str,
        resource_id: Optional[str] = None,
        title_override: Optional[str] = None,
    ) -> None:
        """Enqueues an import job to be processed."""
        await self.queue.put({
            "job_id": job_id,
            "url": url,
            "resource_id": resource_id,
            "title_override": title_override,
        })
        logger.info(f"Enqueued job {job_id} for URL {url}")

    def cancel(self, job_id: str) -> bool:
        """Marks a job as cancelled."""
        self._cancelled_jobs.add(job_id)
        logger.info(f"Cancellation requested for job {job_id}")
        return True

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled_jobs

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                task_data = await self.queue.get()
                job_id = task_data["job_id"]
                url = task_data["url"]
                resource_id = task_data.get("resource_id") or task_data.get("series_id")
                title_override = task_data.get("title_override")

                if not job_id or not url:
                    self.queue.task_done()
                    continue

                if self.is_cancelled(job_id):
                    self._cancelled_jobs.discard(job_id)
                    self.queue.task_done()
                    continue

                try:
                    await run_import_job(
                        job_id=job_id,
                        url=url,
                        resource_id=resource_id,
                        title_override=title_override,
                        is_cancelled_func=lambda j=job_id: self.is_cancelled(j),
                    )
                except Exception as e:
                    logger.exception(f"Fatal worker error processing job {job_id}: {e}")
                finally:
                    self._cancelled_jobs.discard(job_id)
                    self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Unexpected error in worker loop: {e}")
                await asyncio.sleep(1.0)


job_queue = JobQueue()
