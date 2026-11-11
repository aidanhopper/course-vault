import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
from sqlmodel import Field, SQLModel


def generate_id(prefix: str = "res") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Resource(SQLModel, table=True):
    __tablename__ = "resources"

    id: str = Field(default_factory=lambda: generate_id("res"), primary_key=True)
    type: str = Field(index=True)  # 'video', 'series', 'document', 'slides', 'audio'
    title: str
    original_title: str | None = None
    data_json: str = Field(default="{}", sa_column_kwargs={"name": "data"})
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def data(self) -> Dict[str, Any]:
        try:
            return json.loads(self.data_json)
        except Exception:
            return {}

    @data.setter
    def data(self, value: Dict[str, Any]) -> None:
        self.data_json = json.dumps(value, default=str)


class Collection(SQLModel, table=True):
    __tablename__ = "collections"

    id: str = Field(default_factory=lambda: generate_id("col"), primary_key=True)
    title: str
    context: str = Field(index=True)  # Arbitrary string proposed by AI or user: e.g. 'course', 'lecture_session'
    data_json: str = Field(default="{}", sa_column_kwargs={"name": "data"})
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def data(self) -> Dict[str, Any]:
        try:
            return json.loads(self.data_json)
        except Exception:
            return {}

    @data.setter
    def data(self, value: Dict[str, Any]) -> None:
        self.data_json = json.dumps(value, default=str)


class CollectionItem(SQLModel, table=True):
    __tablename__ = "collection_items"

    id: str = Field(default_factory=lambda: generate_id("citem"), primary_key=True)
    collection_id: str = Field(index=True, foreign_key="collections.id")
    resource_id: str = Field(index=True, foreign_key="resources.id")
    context: str = Field(index=True)  # Role within this collection: e.g. 'lecture_video', 'companion_slides'
    order_index: int = Field(default=0, index=True)
    created_at: datetime = Field(default_factory=utc_now)


class SeriesItem(SQLModel, table=True):
    __tablename__ = "series_items"

    id: str = Field(default_factory=lambda: generate_id("sitem"), primary_key=True)
    parent_series_id: str = Field(index=True, foreign_key="resources.id")
    child_resource_id: str = Field(index=True, foreign_key="resources.id")
    order_index: int = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)


class ResourceRelation(SQLModel, table=True):
    __tablename__ = "resource_relations"

    id: str = Field(default_factory=lambda: generate_id("rel"), primary_key=True)
    source_resource_id: str = Field(index=True, foreign_key="resources.id")
    target_resource_id: str = Field(index=True, foreign_key="resources.id")
    role: str = Field(index=True)  # 'slides', 'homework', 'solution', 'series', 'syllabus', 'reading', 'prerequisite'
    created_at: datetime = Field(default_factory=utc_now)


class Topic(SQLModel, table=True):
    __tablename__ = "topics"

    id: str = Field(default_factory=lambda: generate_id("top"), primary_key=True)
    resource_id: str = Field(index=True, foreign_key="resources.id")
    title: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    start_page: int | None = None
    end_page: int | None = None
    order_index: int = Field(default=0, index=True)
    created_at: datetime = Field(default_factory=utc_now)


class TranscriptCue(SQLModel, table=True):
    __tablename__ = "transcript_cues"

    id: str = Field(default_factory=lambda: generate_id("cue"), primary_key=True)
    resource_id: str = Field(index=True, foreign_key="resources.id")
    start_seconds: float = Field(index=True)
    end_seconds: float
    text: str


class SourceMetadata(SQLModel, table=True):
    __tablename__ = "source_metadata"

    id: str = Field(default_factory=lambda: generate_id("src"), primary_key=True)
    resource_id: str = Field(unique=True, index=True, foreign_key="resources.id")
    provider: str = Field(index=True)  # 'youtube', etc.
    source_url: str | None = None
    source_id: str | None = Field(default=None, index=True)  # e.g., YouTube video ID
    raw_payload_json: str = Field(default="{}", sa_column_kwargs={"name": "raw_payload"})
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def raw_payload(self) -> Dict[str, Any]:
        try:
            return json.loads(self.raw_payload_json)
        except Exception:
            return {}

    @raw_payload.setter
    def raw_payload(self, value: Dict[str, Any]) -> None:
        try:
            import yt_dlp
            value = yt_dlp.YoutubeDL().sanitize_info(value)
        except Exception:
            pass
        self.raw_payload_json = json.dumps(value, default=str)


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(default_factory=lambda: generate_id("job"), primary_key=True)
    collection_id: str | None = Field(default=None, index=True)
    resource_id: str | None = Field(default=None, index=True, sa_column_kwargs={"name": "series_id"})
    status: str = Field(default="queued", index=True)  # queued, extracting, cleaning, downloading, completed, failed, cancelled
    progress_percent: float = Field(default=0.0)
    total_items: int = Field(default=0)
    completed_items: int = Field(default=0)
    current_item: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def series_id(self) -> str | None:
        return self.resource_id

    @series_id.setter
    def series_id(self, value: str | None) -> None:
        self.resource_id = value
