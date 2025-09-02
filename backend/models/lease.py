"""
租赁订单与审计日志 ORM 模型

状态机：pending -> confirmed|rejected|cancelled; confirmed -> archived
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Enum,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.db import Base


class LeaseOrder(Base):
    __tablename__ = "lease_orders"
    __table_args__ = (
        Index("ix_lease_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requester_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    assigned_provider_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    device_id: Mapped[Optional[str]] = mapped_column(String(64))  # RAG 条目 ID（字符串）
    device_name_snapshot: Mapped[Optional[str]] = mapped_column(String(256))

    requested_start_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    requested_end_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    confirmed_start_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    confirmed_end_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    confirmed_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    remark: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class LeaseOrderLog(Base):
    __tablename__ = "lease_order_logs"
    __table_args__ = (
        Index("ix_lease_log_order_created", "lease_order_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lease_order_id: Mapped[int] = mapped_column(ForeignKey("lease_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    data_json: Mapped[Optional[str]] = mapped_column(Text)
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


