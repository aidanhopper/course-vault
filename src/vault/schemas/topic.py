from datetime import datetime
from pydantic import BaseModel, ConfigDict


class TopicBase(BaseModel):
    title: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    start_page: int | None = None
    end_page: int | None = None
    order_index: int = 0


class TopicRead(TopicBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    resource_id: str
    created_at: datetime
