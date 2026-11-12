from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.services import Service


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    service_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(4), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    service: Mapped[Service] = relationship("Service", back_populates="metrics")
