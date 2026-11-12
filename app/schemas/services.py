from datetime import datetime
from typing import Any, Optional, Self

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    ModelWrapValidatorHandler,
    PrivateAttr,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.services.url_safety import NOT_ALLOWED_MESSAGE, HostClass, classify_host


class _ServiceUrlInput(BaseModel):
    """URL safety checks shared by create and update requests (spec 002).

    Reserved names and non-public IP literals are rejected here, during request
    validation and without DNS (RF-2). Hostnames are resolved later, in the handler.
    """

    _submitted_url: Optional[str] = PrivateAttr(default=None)

    @field_validator("url", check_fields=False)
    @classmethod
    def _reject_unsafe_host(cls, url: Optional[HttpUrl]) -> Optional[HttpUrl]:
        if url is not None and url.host is not None:
            if classify_host(url.host) in (HostClass.RESERVED, HostClass.UNSAFE_LITERAL):
                raise PydanticCustomError("url_not_allowed", NOT_ALLOWED_MESSAGE)
        return url

    @model_validator(mode="wrap")
    @classmethod
    def _keep_submitted_url(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        model = handler(data)
        if isinstance(data, dict) and isinstance(data.get("url"), str):
            model._submitted_url = data["url"]
        return model

    @property
    def submitted_url(self) -> str:
        """The URL exactly as the client sent it (used as `input` in a 422, RF-5)."""
        url = getattr(self, "url", None)
        return self._submitted_url or str(url)


class ServiceCreate(_ServiceUrlInput):
    name: str = Field(..., min_length=1, max_length=100)
    url: HttpUrl
    expected_status: int = Field(default=200, ge=100, le=599)
    check_interval: int = Field(default=60, ge=10, le=86400, description="Interval in seconds")


class ServiceUpdate(_ServiceUrlInput):
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
