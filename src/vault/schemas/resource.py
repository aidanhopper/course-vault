from datetime import datetime
from typing import Any, Dict, List, Literal
from pydantic import BaseModel, ConfigDict, Field

from vault.schemas.collection import ResourceCollectionMembership
from vault.schemas.topic import TopicRead
from vault.schemas.transcript import TranscriptCueRead


class VideoData(BaseModel):
    duration_seconds: float | None = None
    file_path: str | None = None
    instructors: List[str] = Field(default_factory=list)
    institution: str | None = None
    summary: str | None = None
    topic_tags: List[str] = Field(default_factory=list)
    slides_url: str | None = None
    resources_url: str | None = None
    jellyfin_item_id: str | None = None
    has_subtitles: bool = False
    has_embedded_chapters: bool = False


class DocumentData(BaseModel):
    page_count: int | None = None
    file_format: str = "pdf"
    file_path: str | None = None
    table_of_contents_depth: int | None = None


class SlidesData(BaseModel):
    slide_count: int | None = None
    file_format: str = "pdf"
    file_path: str | None = None


class AudioData(BaseModel):
    duration_seconds: float | None = None
    file_path: str | None = None
    jellyfin_item_id: str | None = None
    has_subtitles: bool = False


ResourceType = Literal["video", "slides", "document", "audio"]


class ResourceBase(BaseModel):
    title: str
    type: ResourceType
    data: Dict[str, Any] = Field(default_factory=dict)


class ResourceCreate(ResourceBase):
    pass


class ResourceRelationRead(BaseModel):
    id: str
    role: str


class ResourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: ResourceType
    title: str
    created_at: datetime
    updated_at: datetime
    data: Dict[str, Any] = Field(default_factory=dict)

    # Optional aspects hydrated when requested via ?include=...
    collections: List[ResourceCollectionMembership] | None = None
    relations: List[ResourceRelationRead] | None = None
    topics: List[TopicRead] | None = None
    transcript: List[TranscriptCueRead] | None = None
    source_metadata: Dict[str, Any] | None = None
    items: List[Dict[str, Any]] | None = None
