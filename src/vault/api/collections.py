from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from vault.api.resources import sanitize_resource_data_for_api
from vault.db.models import Collection, CollectionItem, Resource
from vault.db.session import get_session
from vault.schemas.collection import (
    AddItemToCollectionRequest,
    CollectionCreate,
    CollectionItemRead,
    CollectionRead,
)

router = APIRouter(prefix="/collections", tags=["Collections"])


def sanitize_collection_data_for_api(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """Strips internal filesystem folder paths and duplicate arrays from public Collection responses."""
    api_data = raw_data.copy()
    api_data.pop("folder_path", None)
    api_data.pop("slides", None)
    api_data.pop("documents", None)
    api_data.pop("homework", None)
    return api_data


@router.get("", response_model=List[CollectionRead], response_model_exclude_none=True, summary="List collections")
async def list_collections(
    context: Optional[str] = Query(None, description="Filter by context (e.g. 'course', 'lecture_session', 'homework_pack')"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> List[CollectionRead]:
    stmt = select(Collection)
    if context:
        stmt = stmt.where(Collection.context == context)
    stmt = stmt.order_by(Collection.created_at.desc()).offset(offset).limit(limit)
    collections = (await session.exec(stmt)).all()

    return [
        CollectionRead(
            id=c.id,
            title=c.title,
            context=c.context,
            data=sanitize_collection_data_for_api(c.data),
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in collections
    ]


@router.post("", response_model=CollectionRead, response_model_exclude_none=True, status_code=status.HTTP_201_CREATED, summary="Create a collection")
async def create_collection(
    req: CollectionCreate,
    session: AsyncSession = Depends(get_session),
) -> CollectionRead:
    collection = Collection(
        title=req.title,
        context=req.context,
    )
    collection.data = req.data
    session.add(collection)
    await session.commit()
    await session.refresh(collection)

    return CollectionRead(
        id=collection.id,
        title=collection.title,
        context=collection.context,
        data=sanitize_collection_data_for_api(collection.data),
        created_at=collection.created_at,
        updated_at=collection.updated_at,
        items=[],
    )


@router.get("/{collection_id}", response_model=CollectionRead, response_model_exclude_none=True, summary="Get collection with items")
async def get_collection(
    collection_id: str,
    session: AsyncSession = Depends(get_session),
) -> CollectionRead:
    collection = await session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found")

    stmt = (
        select(CollectionItem)
        .where(CollectionItem.collection_id == collection_id)
        .order_by(CollectionItem.order_index, CollectionItem.created_at)
    )
    items = (await session.exec(stmt)).all()

    populated_items: List[CollectionItemRead] = []
    for it in items:
        resource = await session.get(Resource, it.resource_id)
        populated_items.append(
            CollectionItemRead(
                resource_id=it.resource_id,
                context=it.context,
                order_index=it.order_index,
                title=resource.title if resource else None,
                type=resource.type if resource else None,
                data=sanitize_resource_data_for_api(it.resource_id, resource.data) if resource else None,
            )
        )

    return CollectionRead(
        id=collection.id,
        title=collection.title,
        context=collection.context,
        data=sanitize_collection_data_for_api(collection.data),
        created_at=collection.created_at,
        updated_at=collection.updated_at,
        items=populated_items,
    )


@router.post("/{collection_id}/items", status_code=status.HTTP_201_CREATED, summary="Add resource to collection with context")
async def add_item_to_collection(
    collection_id: str,
    req: AddItemToCollectionRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    collection = await session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found")

    resource = await session.get(Resource, req.resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource {req.resource_id} not found")

    stmt = select(CollectionItem).where(
        CollectionItem.collection_id == collection_id,
        CollectionItem.resource_id == req.resource_id,
    )
    existing = (await session.exec(stmt)).first()
    if existing:
        existing.context = req.context
        existing.order_index = req.order_index
    else:
        c_item = CollectionItem(
            collection_id=collection_id,
            resource_id=req.resource_id,
            context=req.context,
            order_index=req.order_index,
        )
        session.add(c_item)

    await session.commit()
    return {
        "message": f"Resource {req.resource_id} linked to collection {collection_id} with context '{req.context}'"
    }


@router.delete("/{collection_id}/items/{resource_id}", summary="Remove resource from collection")
async def remove_item_from_collection(
    collection_id: str,
    resource_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(CollectionItem).where(
        CollectionItem.collection_id == collection_id,
        CollectionItem.resource_id == resource_id,
    )
    item = (await session.exec(stmt)).first()
    if not item:
        raise HTTPException(status_code=404, detail="Resource not found in collection")

    await session.delete(item)
    await session.commit()
    return {"message": f"Resource {resource_id} unlinked from collection {collection_id}"}


@router.delete("/{collection_id}", summary="Delete collection")
async def delete_collection(
    collection_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    collection = await session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found")

    # Delete all items associated with collection
    stmt_items = select(CollectionItem).where(CollectionItem.collection_id == collection_id)
    items = (await session.exec(stmt_items)).all()
    for it in items:
        await session.delete(it)

    await session.delete(collection)
    await session.commit()
    return {"message": f"Collection {collection_id} deleted successfully"}
