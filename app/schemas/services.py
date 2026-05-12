from pydantic import BaseModel, HttpUrl, Field
from datetime import datetime
from typing import Optional


class ServiceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    url: HttpUrl
    expected_status: int = Field(default=200, ge=100, le=599)
    check_interval: int = Field(default=60, ge=10, le=86400, description="Interval in seconds")


class ServiceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    url: Optional[HttpUrl] = None
    expected_status: Optional[int] = Field(default=None, ge=100, le=599)
    check_interval: Optional[int] = Field(default=None, ge=10, le=86400)
    is_active: Optional[bool] = None


class ServiceResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    url: str
    expected_status: int
    check_interval: int
    is_active: bool
    created_at: datetime
