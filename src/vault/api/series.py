from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from vault.db.models import Resource, SeriesItem
from vault.db.session import get_session
from vault.schemas.resource import ResourceRead

router = APIRouter(prefix="/series", tags=["Series"])


class CreateSeriesRequest(BaseModel):
    title: str
    instructor: Optional[str] = None
    overview: Optional[str] = None


class AddSeriesItemRequest(BaseModel):
    resource_id: str
    order_index: int


@router.get("", response_model=List[ResourceRead], summary="List all series")
async def list_series(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> List[ResourceRead]:
    stmt = (
        select(Resource)
        .where(Resource.type == "series")
        .order_by(Resource.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    series_list = (await session.exec(stmt)).all()
    return [
        ResourceRead(
            id=s.id,
            type=s.type,
            title=s.title,
            original_title=s.original_title,
            data=s.data,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in series_list
    ]


@router.get("/{series_id}", response_model=ResourceRead, summary="Get series with ordered items")
async def get_series_detail(
    series_id: str,
    session: AsyncSession = Depends(get_session),
) -> ResourceRead:
    series = await session.get(Resource, series_id)
    if not series or series.type != "series":
        raise HTTPException(status_code=404, detail=f"Series {series_id} not found")

    stmt = (
        select(SeriesItem)
        .where(SeriesItem.parent_series_id == series_id)
        .order_by(SeriesItem.order_index)
    )
    items = (await session.exec(stmt)).all()

    child_items: List[Dict[str, Any]] = []
    for it in items:
        child = await session.get(Resource, it.child_resource_id)
        if child:
            child_items.append({
                "order_index": it.order_index,
                "resource_id": child.id,
                "title": child.title,
                "type": child.type,
                "data": child.data,
            })

    res_read = ResourceRead(
        id=series.id,
        type=series.type,
        title=series.title,
        original_title=series.original_title,
        data=series.data,
        created_at=series.created_at,
        updated_at=series.updated_at,
        items=child_items,
    )
    return res_read


@router.post("", response_model=ResourceRead, status_code=status.HTTP_201_CREATED, summary="Create empty series")
async def create_series(
    req: CreateSeriesRequest,
    session: AsyncSession = Depends(get_session),
) -> ResourceRead:
    series = Resource(
        type="series",
        title=req.title,
        original_title=req.title,
    )
    series.data = {
        "instructor": req.instructor,
        "overview": req.overview,
    }
    session.add(series)
    await session.commit()
    await session.refresh(series)

    return ResourceRead(
        id=series.id,
        type=series.type,
        title=series.title,
        original_title=series.original_title,
        data=series.data,
        created_at=series.created_at,
        updated_at=series.updated_at,
        items=[],
    )


@router.post("/{series_id}/items", status_code=status.HTTP_201_CREATED, summary="Add resource to series")
async def add_resource_to_series(
    series_id: str,
    req: AddSeriesItemRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    series = await session.get(Resource, series_id)
    if not series or series.type != "series":
        raise HTTPException(status_code=404, detail=f"Series {series_id} not found")

    child = await session.get(Resource, req.resource_id)
    if not child:
        raise HTTPException(status_code=404, detail=f"Resource {req.resource_id} not found")

    stmt = select(SeriesItem).where(
        SeriesItem.parent_series_id == series_id,
        SeriesItem.child_resource_id == req.resource_id,
    )
    existing = (await session.exec(stmt)).first()
    if existing:
        existing.order_index = req.order_index
    else:
        s_item = SeriesItem(
            parent_series_id=series_id,
            child_resource_id=req.resource_id,
            order_index=req.order_index,
        )
        session.add(s_item)

    await session.commit()
    return {
        "message": f"Resource {req.resource_id} linked to series {series_id} at position {req.order_index}"
    }


@router.delete("/{series_id}/items/{resource_id}", summary="Remove resource from series")
async def remove_resource_from_series(
    series_id: str,
    resource_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(SeriesItem).where(
        SeriesItem.parent_series_id == series_id,
        SeriesItem.child_resource_id == resource_id,
    )
    item = (await session.exec(stmt)).first()
    if not item:
        raise HTTPException(status_code=404, detail="Resource link not found in series")

    await session.delete(item)
    await session.commit()
    return {"message": f"Resource {resource_id} unlinked from series {series_id}"}
