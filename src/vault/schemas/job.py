from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ImportRequest(BaseModel):
    url: str
    series_id: str | None = None
    title_override: str | None = None


class ImportResponse(BaseModel):
    job_id: str
    series_id: str | None = None
    status: str


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    series_id: str | None = None
    status: str
    progress_percent: float = 0.0
    total_items: int = 0
    completed_items: int = 0
    current_item: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
