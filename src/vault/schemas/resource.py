from datetime import datetime
from typing import Any, Dict, List, Literal
from pydantic import BaseModel, ConfigDict, Field

from vault.schemas.topic import TopicRead
from vault.schemas.transcript import TranscriptCueRead


class VideoData(BaseModel):
    duration_seconds: float | None = None
    file_path: str | None = None
    jellyfin_item_id: str | None = None
    has_subtitles: bool = False
    has_embedded_chapters: bool = False


class SeriesItemSummary(BaseModel):
    order_index: int
    resource_id: str
    title: str | None = None
    duration_seconds: float | None = None
    type: str = "video"


class SeriesData(BaseModel):
    instructor: str | None = None
    overview: str | None = None
    jellyfin_series_id: str | None = None
    items: List[SeriesItemSummary] = Field(default_factory=list)


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


ResourceType = Literal["video", "series", "document", "slides", "audio"]


class ResourceBase(BaseModel):
    title: str
    type: ResourceType
    original_title: str | None = None
    data: Dict[str, Any] = Field(default_factory=dict)


class ResourceCreate(ResourceBase):
    pass


class ResourceRead(ResourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime

    # Optional aspects hydrated when requested via ?include=...
    topics: List[TopicRead] | None = None
    transcript: List[TranscriptCueRead] | None = None
    source_metadata: Dict[str, Any] | None = None
    items: List[Dict[str, Any]] | None = None
