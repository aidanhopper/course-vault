from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from vault.config import settings
from vault.db.models import Resource, SeriesItem, SourceMetadata, Topic, TranscriptCue
from vault.db.session import get_session
from vault.media.streamer import stream_media_file
from vault.schemas.resource import ResourceCreate, ResourceRead
from vault.schemas.topic import TopicRead
from vault.schemas.transcript import TranscriptCueRead

router = APIRouter(prefix="/resources", tags=["Resources"])


class AddResourceItemRequest(BaseModel):
    resource_id: str
    order_index: int


@router.get("", response_model=List[ResourceRead], response_model_exclude_none=True, summary="List resources")
async def list_resources(
    type: Optional[str] = Query(None, description="Filter by resource type (video, series, document, slides, audio)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> List[ResourceRead]:
    stmt = select(Resource)
    if type:
        stmt = stmt.where(Resource.type == type)
    stmt = stmt.order_by(Resource.created_at.desc()).offset(offset).limit(limit)
    resources = (await session.exec(stmt)).all()

    results: List[ResourceRead] = []
    for r in resources:
        read_obj = ResourceRead(
            id=r.id,
            type=r.type,
            title=r.title,
            original_title=r.original_title,
            data=r.data,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        results.append(read_obj)
    return results


@router.post("", response_model=ResourceRead, response_model_exclude_none=True, status_code=status.HTTP_201_CREATED, summary="Create a resource")
async def create_resource(
    req: ResourceCreate,
    session: AsyncSession = Depends(get_session),
) -> ResourceRead:
    resource = Resource(
        type=req.type,
        title=req.title,
        original_title=req.original_title or req.title,
    )
    resource.data = req.data
    session.add(resource)
    await session.commit()
    await session.refresh(resource)

    return ResourceRead(
        id=resource.id,
        type=resource.type,
        title=resource.title,
        original_title=resource.original_title,
        data=resource.data,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        items=[],
    )


@router.get("/{resource_id}", response_model=ResourceRead, response_model_exclude_none=True, summary="Get resource by ID")
async def get_resource(
    resource_id: str,
    include: Optional[str] = Query(
        None,
        description="Comma-separated aspects to include: topics, transcript, source_metadata, items",
    ),
    session: AsyncSession = Depends(get_session),
) -> ResourceRead:
    resource = await session.get(Resource, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource {resource_id} not found")

    aspects = set(a.strip() for a in include.split(",")) if include else set()

    res_read = ResourceRead(
        id=resource.id,
        type=resource.type,
        title=resource.title,
        original_title=resource.original_title,
        data=resource.data,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )

    # 1. Hydrate topics
    if "topics" in aspects:
        if resource.type == "series":
            stmt_items = select(SeriesItem).where(SeriesItem.parent_series_id == resource_id).order_by(SeriesItem.order_index)
            s_items = (await session.exec(stmt_items)).all()
            all_topics = []
            for s_it in s_items:
                stmt_t = select(Topic).where(Topic.resource_id == s_it.child_resource_id).order_by(Topic.order_index)
                all_topics.extend((await session.exec(stmt_t)).all())
            res_read.topics = [TopicRead.model_validate(t) for t in all_topics]
        else:
            stmt = select(Topic).where(Topic.resource_id == resource_id).order_by(Topic.order_index)
            topics = (await session.exec(stmt)).all()
            res_read.topics = [TopicRead.model_validate(t) for t in topics]

    # 2. Hydrate transcript cues
    if "transcript" in aspects:
        stmt = select(TranscriptCue).where(TranscriptCue.resource_id == resource_id).order_by(TranscriptCue.start_seconds)
        cues = (await session.exec(stmt)).all()
        res_read.transcript = [TranscriptCueRead.model_validate(c) for c in cues]

    # 3. Hydrate source metadata
    if "source_metadata" in aspects or "youtube_metadata" in aspects:
        stmt = select(SourceMetadata).where(SourceMetadata.resource_id == resource_id)
        src_meta = (await session.exec(stmt)).first()
        if src_meta:
            res_read.source_metadata = {
                "provider": src_meta.provider,
                "source_url": src_meta.source_url,
                "source_id": src_meta.source_id,
                "raw": src_meta.raw_payload,
            }

    # 4. Hydrate series items if type is series
    if "items" in aspects and resource.type == "series":
        stmt = select(SeriesItem).where(SeriesItem.parent_series_id == resource_id).order_by(SeriesItem.order_index)
        items = (await session.exec(stmt)).all()
        item_list = []
        for it in items:
            child = await session.get(Resource, it.child_resource_id)
            if child:
                item_list.append({
                    "order_index": it.order_index,
                    "resource_id": child.id,
                    "title": child.title,
                    "type": child.type,
                    "data": child.data,
                })
        res_read.items = item_list
        # Also keep items inside data["items"] for type-specific purity
        res_read.data["items"] = item_list

    return res_read


@router.get("/{resource_id}/topics", response_model=List[TopicRead], summary="Get resource topics")
async def get_resource_topics(
    resource_id: str,
    session: AsyncSession = Depends(get_session),
) -> List[TopicRead]:
    resource = await session.get(Resource, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource {resource_id} not found")

    stmt = select(Topic).where(Topic.resource_id == resource_id).order_by(Topic.order_index)
    topics = (await session.exec(stmt)).all()
    return [TopicRead.model_validate(t) for t in topics]


@router.get("/{resource_id}/transcript", summary="Get resource transcript")
async def get_resource_transcript(
    resource_id: str,
    format: Literal["json", "text", "vtt"] = Query("json", description="Output format"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    resource = await session.get(Resource, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource {resource_id} not found")

    stmt = select(TranscriptCue).where(TranscriptCue.resource_id == resource_id).order_by(TranscriptCue.start_seconds)
    cues = (await session.exec(stmt)).all()

    if format == "json":
        return [TranscriptCueRead.model_validate(c) for c in cues]
    elif format == "text":
        plain_text = " ".join(c.text for c in cues)
        return Response(content=plain_text, media_type="text/plain; charset=utf-8")
    elif format == "vtt":
        vtt_lines = ["WEBVTT\n"]
        for c in cues:
            start_m, start_s = divmod(c.start_seconds, 60)
            start_h, start_m = divmod(start_m, 60)
            end_m, end_s = divmod(c.end_seconds, 60)
            end_h, end_m = divmod(end_m, 60)
            timing = (
                f"{int(start_h):02d}:{int(start_m):02d}:{start_s:06.3f} --> "
                f"{int(end_h):02d}:{int(end_m):02d}:{end_s:06.3f}"
            )
            vtt_lines.append(f"{timing}\n{c.text}\n")
        return Response(content="\n".join(vtt_lines), media_type="text/vtt; charset=utf-8")


@router.get("/{resource_id}/source-metadata", summary="Get raw upstream source metadata")
async def get_resource_source_metadata(
    resource_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    stmt = select(SourceMetadata).where(SourceMetadata.resource_id == resource_id)
    src_meta = (await session.exec(stmt)).first()
    if not src_meta:
        raise HTTPException(status_code=404, detail=f"Source metadata for {resource_id} not found")

    return {
        "resource_id": resource_id,
        "provider": src_meta.provider,
        "source_url": src_meta.source_url,
        "source_id": src_meta.source_id,
        "raw": src_meta.raw_payload,
    }


# Convenience alias for YouTube metadata
@router.get("/{resource_id}/youtube-metadata", summary="Get raw YouTube metadata", include_in_schema=False)
async def get_resource_youtube_metadata(
    resource_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    return await get_resource_source_metadata(resource_id=resource_id, session=session)


@router.get("/{resource_id}/media", summary="Direct media streaming with HTTP 206 byte-ranges")
async def get_resource_media(
    resource_id: str,
    range: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    resource = await session.get(Resource, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource {resource_id} not found")

    rel_path = resource.data.get("file_path")
    if not rel_path:
        raise HTTPException(status_code=404, detail="Resource has no associated media file on disk")

    full_path = settings.MEDIA_DIR / rel_path
    return stream_media_file(full_path, range_header=range)


@router.delete("/{resource_id}", status_code=status.HTTP_200_OK, summary="Delete resource")
async def delete_resource(
    resource_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    resource = await session.get(Resource, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource {resource_id} not found")

    # Delete physical file if present
    rel_path = resource.data.get("file_path")
    if rel_path:
        full_path = settings.MEDIA_DIR / rel_path
        if full_path.is_file():
            try:
                full_path.unlink()
            except Exception:
                pass

    await session.delete(resource)
    await session.commit()
    return {"message": f"Resource {resource_id} deleted successfully"}


@router.post("/{resource_id}/items", status_code=status.HTTP_201_CREATED, summary="Add child resource to series")
async def add_child_resource_item(
    resource_id: str,
    req: AddResourceItemRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parent = await session.get(Resource, resource_id)
    if not parent:
        raise HTTPException(status_code=404, detail=f"Parent resource {resource_id} not found")

    child = await session.get(Resource, req.resource_id)
    if not child:
        raise HTTPException(status_code=404, detail=f"Child resource {req.resource_id} not found")

    stmt = select(SeriesItem).where(
        SeriesItem.parent_series_id == resource_id,
        SeriesItem.child_resource_id == req.resource_id,
    )
    existing = (await session.exec(stmt)).first()
    if existing:
        existing.order_index = req.order_index
    else:
        s_item = SeriesItem(
            parent_series_id=resource_id,
            child_resource_id=req.resource_id,
            order_index=req.order_index,
        )
        session.add(s_item)

    await session.commit()
    return {
        "message": f"Resource {req.resource_id} linked to parent {resource_id} at position {req.order_index}"
    }


@router.delete("/{resource_id}/items/{child_resource_id}", summary="Remove child resource from series")
async def remove_child_resource_item(
    resource_id: str,
    child_resource_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(SeriesItem).where(
        SeriesItem.parent_series_id == resource_id,
        SeriesItem.child_resource_id == child_resource_id,
    )
    item = (await session.exec(stmt)).first()
    if not item:
        raise HTTPException(status_code=404, detail="Child resource link not found in series")

    await session.delete(item)
    await session.commit()
    return {"message": f"Resource {child_resource_id} unlinked from {resource_id}"}
