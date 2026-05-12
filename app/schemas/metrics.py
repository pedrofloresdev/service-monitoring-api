from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class MetricResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    service_id: int
    status: str
    status_code: Optional[int]
    response_time_ms: Optional[float]
    error_message: Optional[str]
    checked_at: datetime


class MetricsPage(BaseModel):
    service_id: int
    total: int
    limit: int
    offset: int
    items: list[MetricResponse]
