from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ImportRequest(BaseModel):
    url: str
    resource_id: str | None = None
    series_id: str | None = None  # Legacy alias
    title_override: str | None = None

    @property
    def target_resource_id(self) -> str | None:
        return self.resource_id or self.series_id


class ImportResponse(BaseModel):
    job_id: str
    resource_id: str | None = None
    status: str


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    resource_id: str | None = None
    status: str
    progress_percent: float = 0.0
    total_items: int = 0
    completed_items: int = 0
    current_item: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
