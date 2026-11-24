from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.metrics import Metric


class ServiceOrigin(StrEnum):
    """Spec 003a: who created and maintains a service."""

    SEEDED = "seeded"
    VISITOR = "visitor"
    ORDINARY = "ordinary"


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("origin IN ('seeded', 'visitor', 'ordinary')", name="ck_services_origin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    expected_status: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    check_interval: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    origin: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=ServiceOrigin.ORDINARY.value,
        server_default=text("'ordinary'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    metrics: Mapped[list[Metric]] = relationship(
        "Metric", back_populates="service", cascade="all, delete-orphan"
    )
