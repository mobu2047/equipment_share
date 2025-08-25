"""
集中定义后端 API（包含路由与业务实现）

使用说明（主要端点）：
- POST /api/rag/upsert           [multipart/form-data]
  - form fields:
    - name: str (必填)
    - description: str
    - tags: str  逗号分隔（例："sem,microscopy"，服务端会归一化为小写）
    - image: file (图片文件，可选；落盘到 /static/uploads/ 并返回 image_url)
    - address: str (可选；若提供则调用 AK/SK 地理编码补充 lat/lng)
    - quantity: int (可选；台数)
  - response: ItemResponse（包含 name/description/tags/image_url/address/lat/lng/metadata/quantity 等）

- POST /api/rag/upsert_full      [application/json]
  - body: UpsertFullRequest
  - response: ItemResponse

- GET  /api/rag/item/{id}        -> ItemResponse
- PUT  /api/rag/item/{id}        -> ItemResponse（body: UpsertFullRequest）
- GET  /api/rag/items            -> { total,page,size,items[] }（支持 tags,match,q 分页过滤）
- GET  /api/rag/search           -> RecommendResponse（结构化检索）
- POST /api/rag/ask              -> { answer, sources[], recommendations[] }（RAG 问答）
- POST /api/rag/reindex          -> { reindexed }
- GET  /api/rag/list             -> { items[] }（简表）
- DELETE /api/rag/delete/{id}    -> { deleted, id }

维护约定：
- 新增/修改/删除 API，都在本文件中进行；路由与业务保持同文件、同命名空间，便于检索与维护。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import os
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.core.logger import logger
from backend.core.config import get_settings
from backend.models.schemas import UpsertFullRequest, ItemResponse, RecommendResponse, EquipmentItem
from backend.services.embeddings_client import EmbeddingsClient
from backend.services.rag_store_faiss import RagStoreFaiss
from backend.services.geocode_baidu import geocode_address


router = APIRouter(prefix="/api/rag", tags=["rag"])

# -------------------------- 业务函数（内部复用） --------------------------
async def api_upsert_full(req: UpsertFullRequest) -> ItemResponse:
    client = EmbeddingsClient()
    text = f"{req.name}\n{req.description or ''}\nTags: {', '.join(req.tags or [])}"
    # 避免覆盖 LogRecord 保留字段 "name"，使用 equipment_name
    logger.info("api.upsert_full.embed", extra={"event": "embed_request", "equipment_name": req.name})
    vector = await client.embed(text)
    await client.aclose()

    lat_val = req.lat
    lng_val = req.lng
    if (lat_val is None or lng_val is None) and (req.address or "").strip():
        geo = await geocode_address(req.address or "")
        lat_val = geo.get("lat")
        lng_val = geo.get("lng")

    store = RagStoreFaiss()
    item_id = store.upsert(
        name=req.name,
        description=req.description or "",
        tags=[t.strip().lower() for t in (req.tags or []) if t.strip()],
        image_url=req.image_url or "",
        vector=vector,
        address=req.address,
        lat=lat_val,
        lng=lng_val,
        metadata=req.metadata,
        quantity=req.quantity,
    )
    data = store.get_item(item_id)
    return ItemResponse(**data)  # type: ignore[arg-type]


async def api_upsert_from_form(
    *, name: str, description: str, tags_csv: str, image_url: str, address: Optional[str], quantity: Optional[int]
) -> ItemResponse:
    tags = [t.strip().lower() for t in (tags_csv or "").split(",") if t.strip()]
    client = EmbeddingsClient()
    text = f"{name}\n{description}\nTags: {', '.join(tags)}"
    vector = await client.embed(text)
    await client.aclose()

    geo = await geocode_address(address or "") if (address or "").strip() else {"lat": None, "lng": None}
    store = RagStoreFaiss()
    item_id = store.upsert(
        name=name,
        description=description,
        tags=tags,
        image_url=image_url,
        vector=vector,
        address=address,
        lat=geo.get("lat"),
        lng=geo.get("lng"),
        metadata=None,
        quantity=quantity,
    )
    data = store.get_item(item_id)
    return ItemResponse(**data)  # type: ignore[arg-type]


async def api_get_item(item_id: str) -> ItemResponse:
    store = RagStoreFaiss()
    data = store.get_item(item_id)
    return ItemResponse(**data)  # type: ignore[arg-type]


async def api_list_items(*, tags: List[str], match: str, q: str, page: int, size: int) -> Dict[str, Any]:
    store = RagStoreFaiss()
    return store.list_items_filtered(tags=tags, match=match, q=q, page=page, size=size)


async def api_update_item(item_id: str, req: UpsertFullRequest) -> ItemResponse:
    client = EmbeddingsClient()
    text = f"{req.name}\n{req.description or ''}\nTags: {', '.join(req.tags or [])}"
    vector = await client.embed(text)
    await client.aclose()

    geo = await geocode_address(req.address or "") if (req.address or "").strip() else {"lat": req.lat, "lng": req.lng}
    store = RagStoreFaiss()
    new_id = store.upsert(
        name=req.name,
        description=req.description or "",
        tags=[t.strip().lower() for t in (req.tags or []) if t.strip()],
        image_url=req.image_url or "",
        vector=vector,
        address=req.address,
        lat=geo.get("lat"),
        lng=geo.get("lng"),
        metadata=req.metadata,
        quantity=req.quantity,
    )
    data = store.get_item(new_id)
    return ItemResponse(**data)  # type: ignore[arg-type]


async def api_search(*, query: str, top_k: int) -> RecommendResponse:
    client = EmbeddingsClient()
    qvec = await client.embed(query)
    await client.aclose()
    store = RagStoreFaiss()
    items = store.search(query_vector=qvec, top_k=top_k, query_text=query)
    return RecommendResponse(items=[EquipmentItem(**it) for it in items])


async def api_ask(*, query: str, top_k: int) -> Dict[str, Any]:
    client = EmbeddingsClient()
    qvec = await client.embed(query)
    await client.aclose()
    store = RagStoreFaiss()
    docs = store.search(query_vector=qvec, top_k=top_k, query_text=query)
    if not docs:
        return {"answer": "未在知识库中找到相关实验设备，请尝试使用其他描述或先添加设备信息。", "sources": []}
    context = "\n\n".join([f"[{i+1}] 名称: {d['name']}\n描述: {d['description']}\n标签: {', '.join(d['tags'])}" for i, d in enumerate(docs)])
    system = (
        "你是实验设备共享平台的助手。严格遵循以下要求：\n"
        "1) 仅依据提供的设备资料回答，不要编造。\n"
        "2) 回答先给出简短结论。\n"
        "3) 必须包含《推荐设备》列表，逐条列出命中的设备。\n"
        "4) 在答案末尾给出引用编号。"
    )
    from backend.services.ollama_client import OllamaClient

    llm = OllamaClient()
    answer = await llm.generate(
        "已知设备资料如下：\n" + context + "\n\n" + "用户问题：" + query + "\n请按系统要求生成答案。",
        system=system,
    )
    await llm.aclose()
    return {"answer": answer, "sources": docs, "recommendations": docs}


async def api_reindex() -> Dict[str, int]:
    client = EmbeddingsClient()

    async def _embed(text: str):
        vec = await client.embed(text)
        return vec

    store = RagStoreFaiss()
    try:
        count = await store.reindex_async(_embed)
    finally:
        await client.aclose()
    return {"reindexed": count}


async def api_delete_item(item_id: str) -> Dict[str, Any]:
    store = RagStoreFaiss()
    ok = store.delete(item_id)
    return {"deleted": bool(ok), "id": item_id}


# -------------------------- 路由定义 --------------------------

@router.post("/upsert", response_model=ItemResponse)
async def http_upsert_equipment(
    name: str = Form(...),
    description: str = Form(""),
    tags: Optional[str] = Form(""),
    image: Optional[UploadFile] = File(None),
    address: Optional[str] = Form(None),
    quantity: Optional[int] = Form(None),
) -> ItemResponse:
    """单条上传（表单）。保存图片到 /static/uploads 并完成入库。

    表单字段见模块头部注释。
    """
    settings = get_settings()
    # 将上传目录锚定到项目根的 static/uploads，避免工作目录差异
    static_root = Path(__file__).resolve().parents[1] / "static"
    uploads_dir = static_root / Path(settings.uploads_dir).name if not Path(settings.uploads_dir).is_absolute() else Path(settings.uploads_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    image_url = ""
    if image is not None:
        filename = f"{name.strip().lower().replace(' ', '_')}_{os.getpid()}_{Path(image.filename).name}"
        save_path = uploads_dir / filename
        content = await image.read()
        with open(save_path, "wb") as f:
            f.write(content)
        image_url = f"/static/{uploads_dir.relative_to(static_root).as_posix()}/{filename}"

    try:
        return await api_upsert_from_form(
            name=name,
            description=description,
            tags_csv=tags or "",
            image_url=image_url,
            address=address,
            quantity=quantity,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upsert_full", response_model=ItemResponse)
async def http_upsert_full(req: UpsertFullRequest) -> ItemResponse:
    """JSON 方式新增/更新（与批量导入对齐）。"""
    try:
        return await api_upsert_full(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/item/{item_id}", response_model=ItemResponse)
async def http_get_item(item_id: str) -> ItemResponse:
    try:
        return await api_get_item(item_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/items", response_model=dict)
async def http_list_items(tags: Optional[str] = None, match: str = "any", q: str = "", page: int = 1, size: int = 20) -> dict:
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
    return await api_list_items(tags=tag_list, match=match, q=q, page=page, size=size)


@router.put("/item/{item_id}", response_model=ItemResponse)
async def http_update_item(item_id: str, req: UpsertFullRequest) -> ItemResponse:
    try:
        return await api_update_item(item_id, req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search", response_model=RecommendResponse)
async def http_search_equipment(query: str, top_k: int = 3) -> RecommendResponse:
    try:
        return await api_search(query=query, top_k=top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ask", response_model=dict)
async def http_ask_with_context(query: str, top_k: int = 3) -> dict:
    try:
        return await api_ask(query=query, top_k=top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reindex", response_model=dict)
async def http_reindex_all() -> dict:
    return await api_reindex()


@router.get("/list", response_model=dict)
async def http_list_all() -> dict:
    return await api_list_items(tags=[], match="any", q="", page=1, size=1000)


@router.delete("/delete/{item_id}", response_model=dict)
async def http_delete_item(item_id: str) -> dict:
    # 在删除记录后，尽量清理本地图片（如果仍存在且未被其他条目引用）
    try:
        item = await api_get_item(item_id)
    except Exception:
        item = None
    res = await api_delete_item(item_id)
    try:
        if item and getattr(item, "image_url", None) and item.image_url.startswith("/static/"):
            static_dir = Path(__file__).resolve().parents[1] / "static"
            local_path = static_dir / item.image_url.replace("/static/", "")
            if local_path.exists():
                local_path.unlink()
    except Exception:
        pass
    return res


