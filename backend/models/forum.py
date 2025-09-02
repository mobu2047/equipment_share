"""
论坛模型：线程与回复

说明：
- 线程可选绑定租赁单（lease_order_id），用于与租赁流程协同讨论；
- 采用软删除字段 is_deleted，便于后续审核/恢复。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.db import Base


class ForumThread(Base):
    __tablename__ = "forum_threads"
    __table_args__ = (
        Index("ix_thread_category_created", "category", "created_at"),
        Index("ix_thread_lease", "lease_order_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    category: Mapped[str] = mapped_column(String(32), default="general", nullable=False)
    lease_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lease_orders.id", ondelete="SET NULL"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attachments_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ForumPost(Base):
    __tablename__ = "forum_posts"
    __table_args__ = (
        Index("ix_post_thread_created", "thread_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("forum_threads.id", ondelete="CASCADE"), index=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    parent_post_id: Mapped[Optional[int]] = mapped_column(ForeignKey("forum_posts.id", ondelete="SET NULL"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attachments_json: Mapped[Optional[str]] = mapped_column(Text)
    # 贴内楼层号：每个线程内从 1 开始递增，用于前端展示固定序号
    floor_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


