from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from vault.db.models import Job
from vault.db.session import get_session
from vault.schemas.job import ImportRequest, ImportResponse, JobRead
from vault.workers.queue import job_queue

router = APIRouter(prefix="", tags=["Imports & Jobs"])


@router.post(
    "/imports",
    response_model=ImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit URL for ingestion",
    description="Submits a YouTube playlist, single video, or document PDF for background ingestion.",
)
async def create_import(
    request: ImportRequest,
    session: AsyncSession = Depends(get_session),
) -> ImportResponse:
    if not request.url.strip():
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    target_id = request.target_resource_id
    col_id = request.collection_id or (target_id if target_id and target_id.startswith("col_") else None)
    job = Job(
        collection_id=col_id,
        resource_id=target_id,
        status="queued",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Enqueue background task
    await job_queue.enqueue(
        job_id=job.id,
        url=request.url,
        resource_id=target_id,
        title_override=request.title_override,
    )

    return ImportResponse(
        job_id=job.id,
        collection_id=job.collection_id,
        resource_id=job.resource_id,
        status=job.status,
    )


@router.get(
    "/jobs",
    response_model=List[JobRead],
    summary="List all import jobs",
    description="Lists all import jobs, ordered by newest first.",
)
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status (queued, downloading, completed, failed, cancelled)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> List[JobRead]:
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    stmt = stmt.order_by(Job.created_at.desc()).offset(offset).limit(limit)
    jobs = (await session.exec(stmt)).all()
    return [JobRead.model_validate(j) for j in jobs]


@router.get(
    "/jobs/{job_id}",
    response_model=JobRead,
    summary="Get import job progress",
    description="Polls the status, progress %, and active step of an import task.",
)
async def get_job_status(
    job_id: str,
    session: AsyncSession = Depends(get_session),
) -> JobRead:
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobRead.model_validate(job)


@router.post(
    "/jobs/{job_id}/cancel",
    summary="Cancel active import job",
)
async def cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job.status in ("completed", "failed", "cancelled"):
        return {"status": job.status, "message": f"Job is already {job.status}"}

    job_queue.cancel(job_id)
    job.status = "cancelled"
    await session.commit()
    return {"status": "cancelled", "message": f"Job {job_id} cancelled"}
