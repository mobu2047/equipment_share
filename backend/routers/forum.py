from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.core.deps import get_current_user
from backend.models.schemas import ThreadCreateRequest, ThreadListResponse, PostCreateRequest
from backend.services.forum_service import ForumService


router = APIRouter(prefix="/api/v1/forum", tags=["forum"])


@router.post("/threads", response_model=Dict[str, Any])
async def create_thread(body: ThreadCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    svc = ForumService(db)
    th = svc.create_thread(author_user_id=user.id, title=body.title, content=body.content, category=body.category, lease_order_id=body.lease_order_id)
    return {"id": th.id}


@router.get("/threads", response_model=ThreadListResponse)
async def list_threads(category: str | None = None, lease_order_id: int | None = None, page: int = 1, size: int = 20, db: Session = Depends(get_db)) -> ThreadListResponse:
    svc = ForumService(db)
    total, items = svc.list_threads(category=category, lease_order_id=lease_order_id, page=page, size=size)
    return ThreadListResponse(
        total=total,
        page=page,
        size=size,
        items=[{
            "id": it.id,
            "author_user_id": it.author_user_id,
            "title": it.title,
            "category": it.category,
            "lease_order_id": it.lease_order_id,
            "created_at": it.created_at.isoformat(),
        } for it in items]
    )


@router.get("/threads/{thread_id}", response_model=Dict[str, Any])
async def get_thread(thread_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    svc = ForumService(db)
    th = svc.get_thread(thread_id)
    # 帖子列表
    from backend.models.forum import ForumPost
    posts = db.query(ForumPost).filter(ForumPost.thread_id == th.id, ForumPost.is_deleted.is_(False)).order_by(ForumPost.created_at.asc()).all()
    return {
        "id": th.id,
        "title": th.title,
        "author_user_id": th.author_user_id,
        "category": th.category,
        "lease_order_id": th.lease_order_id,
        "created_at": th.created_at.isoformat(),
        "posts": [
            {
                "id": p.id,
                "author_user_id": p.author_user_id,
                "content": p.content,
                "parent_post_id": p.parent_post_id,
                "created_at": p.created_at.isoformat(),
            }
            for p in posts
        ],
    }


@router.post("/threads/{thread_id}/posts", response_model=Dict[str, Any])
async def create_post(thread_id: int, body: PostCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    svc = ForumService(db)
    p = svc.create_post(thread_id=thread_id, author_user_id=user.id, content=body.content, parent_post_id=body.parent_post_id)
    return {"id": p.id}


@router.delete("/threads/{thread_id}", response_model=Dict[str, Any])
async def delete_thread(thread_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    svc = ForumService(db)
    role_names = {r.name for r in user.roles}
    svc.delete_thread(thread_id=thread_id, actor_user_id=user.id, actor_is_admin=("ADMIN" in role_names))
    return {"deleted": True}


@router.delete("/posts/{post_id}", response_model=Dict[str, Any])
async def delete_post(post_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    svc = ForumService(db)
    role_names = {r.name for r in user.roles}
    svc.delete_post(post_id=post_id, actor_user_id=user.id, actor_is_admin=("ADMIN" in role_names))
    return {"deleted": True}


