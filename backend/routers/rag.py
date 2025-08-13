"""
RAG 路由

能力：
- upsert 设备信息（含图片上传）
- 搜索设备（向量检索）
- RAG 问答（基于检索上下文调用 Ollama）
"""

import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.core.config import get_settings
from backend.core.logger import logger
from backend.models.schemas import RecommendResponse, EquipmentItem
from backend.services.embeddings_client import EmbeddingsClient
from backend.services.rag_store import RagStore
from backend.services.ollama_client import OllamaClient


router = APIRouter(prefix="/api/rag", tags=["rag"])


@router.post("/upsert", response_model=dict)
async def upsert_equipment(
    name: str = Form(...),
    description: str = Form(""),
    tags: Optional[str] = Form("") ,  # 逗号分隔
    image: Optional[UploadFile] = File(None),
):
    settings = get_settings()
    uploads_dir = Path(settings.uploads_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # 1) 保存图片（如果有）
    image_url = ""
    if image is not None:
        suffix = Path(image.filename).suffix or ".bin"
        filename = f"{name.strip().lower().replace(' ', '_')}_{os.getpid()}_{image.filename}"
        save_path = uploads_dir / filename
        content = await image.read()
        with open(save_path, "wb") as f:
            f.write(content)
        image_url = f"/static/uploads/{filename}"

    # 2) 生成 embedding
    client = EmbeddingsClient()
    text = f"{name}\n{description}\nTags: {tags or ''}"
    try:
        vector = await client.embed(text)
        await client.aclose()
    except Exception as e:
        logger.error("rag.upsert.embed_error", extra={"event": "rag_upsert_embed_error"})
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")

    tag_list: List[str] = [t.strip() for t in (tags or "").split(",") if t.strip()]

    # 3) 写入向量库
    store = RagStore()
    item_id = store.upsert(name=name, description=description, tags=tag_list, image_url=image_url, vector=vector)
    logger.info("rag.upsert.ok", extra={"event": "rag_upsert_ok", "id": item_id})
    return {"id": item_id}


@router.get("/search", response_model=RecommendResponse)
async def search_equipment(query: str, top_k: int = 3) -> RecommendResponse:
    # 为查询生成 embedding，然后在本地向量库中检索
    client = EmbeddingsClient()
    try:
        qvec = await client.embed(query)
        await client.aclose()
    except Exception as e:
        logger.error("rag.search.embed_error", extra={"event": "rag_search_embed_error"})
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")

    store = RagStore()
    items = store.search(query_vector=qvec, top_k=top_k)
    return RecommendResponse(items=[EquipmentItem(**it) for it in items])


@router.post("/ask", response_model=dict)
async def ask_with_context(query: str, top_k: int = 3) -> dict:
    # 1) 向量检索
    emb = EmbeddingsClient()
    try:
        qvec = await emb.embed(query)
        await emb.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")

    store = RagStore()
    docs = store.search(query_vector=qvec, top_k=top_k)

    # 2) 组织上下文并调用 LLM
    context = "\n\n".join([f"[{i+1}] {d['name']}: {d['description']} (tags: {', '.join(d['tags'])})" for i, d in enumerate(docs)])
    prompt = (
        "你是一个实验设备共享平台的智能助手。根据给定的设备资料回答用户问题。"
        "\n\n已知设备信息:\n" + context + "\n\n问题: " + query + "\n请基于资料作答，并在结尾给出引用编号如 [1][2]。"
    )

    llm = OllamaClient()
    try:
        answer = await llm.generate(prompt)
        await llm.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM failed: {e}")

    return {"answer": answer, "sources": docs}


@router.get("/list", response_model=dict)
async def list_all() -> dict:
    """列出所有设备条目。"""
    store = RagStore()
    items = store.list_items()
    return {"items": items}


@router.delete("/delete/{item_id}", response_model=dict)
async def delete_item(item_id: str) -> dict:
    """按 id 删除设备条目。"""
    store = RagStore()
    ok = store.delete(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"deleted": True, "id": item_id}


