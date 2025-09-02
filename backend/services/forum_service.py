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
        post = ForumPost(thread_id=th.id, author_user_id=author_user_id, content=content, attachments_json=json.dumps(attachments or [], ensure_ascii=False))
        self.db.add(post)
        self.db.commit()
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
        post = ForumPost(thread_id=th.id, author_user_id=author_user_id, content=content, parent_post_id=parent_post_id, attachments_json=json.dumps(attachments or [], ensure_ascii=False))
        self.db.add(post)
        self.db.commit()
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


