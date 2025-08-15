"""
RAG 路由

能力：
- upsert 设备信息（含图片上传）
- 搜索设备（向量检索）
- RAG 问答（基于检索上下文调用 Ollama）
"""

import os
from pathlib import Path
import sys
from typing import List, Optional

# 允许在直接运行单文件或非常规入口下解析 package 导入
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
    items = store.search(query_vector=qvec, top_k=top_k, query_text=query)
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
    docs = store.search(query_vector=qvec, top_k=top_k, query_text=query)

    # 若未命中，直接返回提示，避免空回答
    if not docs:
        return {"answer": "未在知识库中找到相关实验设备，请尝试使用其他描述或先添加设备信息。", "sources": []}

    # 2) 组织上下文并调用 LLM（强化输出要求，确保列出设备）
    context = "\n\n".join([
        f"[{i+1}] 名称: {d['name']}\n描述: {d['description']}\n标签: {', '.join(d['tags'])}"
        for i, d in enumerate(docs)
    ])

    system = (
        "你是实验设备共享平台的助手。严格遵循以下要求：\n"
        "1) 仅依据提供的设备资料回答，不要编造。\n"
        "2) 回答先给出简短结论（不超过两句话）。\n"
        "3) 必须包含一个《推荐设备》列表，逐条列出命中的设备：`- [编号] 设备名：1句理由`。\n"
        "4) 在答案末尾给出引用编号，如 [1][2]，对应上方列表的编号。"
    )

    prompt = (
        "已知设备资料如下：\n" + context + "\n\n"
        + "用户问题：" + query + "\n"
        + "请按系统要求生成答案。"
    )

    llm = OllamaClient()
    try:
        answer = await llm.generate(prompt, system=system)
        await llm.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM failed: {e}")

    return {"answer": answer, "sources": docs, "recommendations": docs}


@router.post("/reindex", response_model=dict)
async def reindex_all() -> dict:
    """重建全部设备的向量（当更换嵌入模型或早期条目写入异常时使用）。"""
    emb = EmbeddingsClient()
    async def _embed(text: str):
        vec = await emb.embed(text)
        return vec

    store = RagStore()
    try:
        count = await store.reindex_async(_embed)
    finally:
        await emb.aclose()
    return {"reindexed": count}


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


