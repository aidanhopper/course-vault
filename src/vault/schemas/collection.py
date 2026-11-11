from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CollectionItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_id: str
    context: str  # e.g. 'lecture_video', 'companion_slides', 'problem_set', 'solution'
    order_index: int = 0
    title: Optional[str] = None
    type: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class ResourceCollectionMembership(BaseModel):
    collection_id: str
    collection_title: str
    collection_context: str
    context: str  # Role/context of the resource in this collection


class CollectionBase(BaseModel):
    title: str
    context: str  # Arbitrary descriptive string: e.g. 'course', 'lecture_session', 'homework_pack'
    data: Dict[str, Any] = Field(default_factory=dict)


class CollectionCreate(CollectionBase):
    pass


class CollectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    context: str
    created_at: datetime
    updated_at: datetime
    data: Dict[str, Any] = Field(default_factory=dict)
    items: Optional[List[CollectionItemRead]] = None


class AddItemToCollectionRequest(BaseModel):
    resource_id: str
    context: str  # Arbitrary role/context of this resource inside the collection
    order_index: int = 0
