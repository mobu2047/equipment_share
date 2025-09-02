from __future__ import annotations

"""
论坛服务：发帖/回帖/列表/详情
"""

from typing import List, Optional, Tuple
import json

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.models.forum import ForumThread, ForumPost
from backend.models.user import Role
from backend.core.logger import logger


class ForumService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_thread(self, *, author_user_id: int, title: str, content: str, category: str, lease_order_id: Optional[int], attachments: Optional[List[str]] = None) -> ForumThread:
        # 若传入 lease_order_id，需要校验该租赁单存在，否则置为 None 以避免外键错误
        if lease_order_id:
            from backend.models.lease import LeaseOrder
            exists = self.db.get(LeaseOrder, lease_order_id)
            if not exists:
                lease_order_id = None
        th = ForumThread(title=title, author_user_id=author_user_id, category=category, lease_order_id=lease_order_id, attachments_json=json.dumps(attachments or [], ensure_ascii=False))
        self.db.add(th)
        self.db.flush()
        # 首帖固定为 1 楼
        post = ForumPost(
            thread_id=th.id,
            author_user_id=author_user_id,
            content=content or "",
            attachments_json=json.dumps(attachments or [], ensure_ascii=False),
            floor_no=1,
        )
        self.db.add(post)
        self.db.commit()
        # 结构化日志：创建线程与首帖
        logger.info(
            "forum.thread.create",
            extra={
                "event": "forum_thread_create",
                "thread_id": th.id,
                "author_user_id": author_user_id,
                "category": category,
                "attachments_count": len(attachments or []),
            },
        )
        return th

    def list_threads(self, *, category: Optional[str], lease_order_id: Optional[int], page: int, size: int) -> Tuple[int, List[ForumThread]]:
        q = self.db.query(ForumThread).filter(ForumThread.is_deleted.is_(False))
        if category:
            q = q.filter(ForumThread.category == category)
        if lease_order_id:
            q = q.filter(ForumThread.lease_order_id == lease_order_id)
        total = q.count()
        items = q.order_by(ForumThread.created_at.desc()).offset((page - 1) * size).limit(size).all()
        return total, items

    def get_thread(self, thread_id: int) -> ForumThread:
        th = self.db.get(ForumThread, thread_id)
        if not th or th.is_deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="线程不存在")
        return th

    def create_post(self, *, thread_id: int, author_user_id: int, content: str, parent_post_id: Optional[int], attachments: Optional[List[str]] = None) -> ForumPost:
        th = self.get_thread(thread_id)
        # 计算楼层号：优先基于最大 floor_no；若历史数据未填，退化为 COUNT(*)+1
        from backend.models.forum import ForumPost as FP
        last = (
            self.db.query(FP.floor_no)
            .filter(FP.thread_id == th.id)
            .order_by(FP.floor_no.desc())
            .first()
        )
        max_floor = (last[0] if last and last[0] else 0)
        if max_floor <= 0:
            # 历史数据未设置 floor_no 时的兜底：按已存在帖子数量 + 1
            cnt = self.db.query(FP).filter(FP.thread_id == th.id).count()
            next_floor = cnt + 1
            logger.info(
                "forum.post.floor_fallback",
                extra={"event": "forum_floor_fallback", "thread_id": th.id, "computed_from_count": cnt},
            )
        else:
            next_floor = max_floor + 1
        post = ForumPost(
            thread_id=th.id,
            author_user_id=author_user_id,
            content=content or "",
            parent_post_id=parent_post_id,
            attachments_json=json.dumps(attachments or [], ensure_ascii=False),
            floor_no=next_floor,
        )
        self.db.add(post)
        self.db.commit()
        # 结构化日志：创建回复
        logger.info(
            "forum.post.create",
            extra={
                "event": "forum_post_create",
                "thread_id": th.id,
                "post_id": post.id,
                "author_user_id": author_user_id,
                "floor_no": next_floor,
                "attachments_count": len(attachments or []),
            },
        )
        return post

    def delete_thread(self, *, thread_id: int, actor_user_id: int, actor_is_admin: bool) -> None:
        th = self.db.get(ForumThread, thread_id)
        if not th or th.is_deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="线程不存在")
        if (th.author_user_id != actor_user_id) and (not actor_is_admin):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权删除该线程")
        th.is_deleted = True
        self.db.commit()

    def delete_post(self, *, post_id: int, actor_user_id: int, actor_is_admin: bool) -> None:
        p = self.db.get(ForumPost, post_id)
        if not p or p.is_deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="回复不存在")
        if (p.author_user_id != actor_user_id) and (not actor_is_admin):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权删除该回复")
        p.is_deleted = True
        self.db.commit()


