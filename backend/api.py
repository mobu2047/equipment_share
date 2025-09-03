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

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Request

from backend.core.logger import logger
from backend.core.config import get_settings
from backend.models.schemas import (
    UpsertFullRequest, ItemResponse, RecommendResponse, EquipmentItem,
    BatchUpsertRequest, BatchUpsertResponse, BatchUpsertResult,
    SearchByMetaRequest, SearchByMetaResponse
)
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

    # 仅生成 location（不再持久化顶层 lat/lng）
    lat_val = req.lat
    lng_val = req.lng
    if (lat_val is None or lng_val is None) and (req.address or "").strip():
        geo = await geocode_address(req.address or "")
        lat_val = geo.get("lat")
        lng_val = geo.get("lng")

    store = RagStoreFaiss()
    # 先进行查重：需要 name/参数/测试项目/单位名称/地址 完整匹配
    md = req.metadata or {}
    dup = store.find_duplicate(
        name=req.name,
        parameters=(md.get("参数") if isinstance(md, dict) else None),
        test_items=(md.get("测试项目") if isinstance(md, dict) else None),
        unit_name=((md.get("单位名称") or md.get("单位")) if isinstance(md, dict) else None),
        address=(req.address or (md.get("原始地址") if isinstance(md, dict) else None)),
    )
    if dup:
        # 直接返回已存在条目，避免重复写入
        return ItemResponse(**dup)  # type: ignore[arg-type]
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
    # 表单模式下，尽可能从描述中抽取“参数/测试项目/单位名称”，以便查重
    def _kv(key: str) -> Optional[str]:
        import re as _re
        m = _re.search(rf"{key}[:：]\s*(.+)", description or "")
        return m.group(1).strip() if m else None
    meta_like = {
        "参数": _kv("参数"),
        "测试项目": _kv("测试项目"),
        "单位名称": _kv("单位名称") or _kv("单位"),
        "原始地址": address or "",
    }
    store = RagStoreFaiss()
    dup = store.find_duplicate(
        name=name,
        parameters=meta_like.get("参数"),
        test_items=meta_like.get("测试项目"),
        unit_name=meta_like.get("单位名称"),
        address=address,
    )
    if dup:
        return ItemResponse(**dup)  # type: ignore[arg-type]
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
            '''你是一名专业的株洲市高新区设备共享平台的智能推荐助手，专门帮助科研人员和企业高效匹配实验设备资源。请遵循以下回答规范：
                1. **回答结构**：
                - 先提供简洁的结论性回答（4-5句话）
                - 然后列出《推荐设备》清单，每项格式为：- [编号] 设备名称：推荐理由（结合设备资料，2-3句话）
                - 最后标注引用资料的设备编号

                2. **专业要求**：
                - 理解设备的技术参数和应用场景
                - 考虑设备的规格匹配度和技术先进性
                - 对设备功能不做夸大描述，客观准确

                3. **交互风格**：
                - 专业但友好，体现高新区服务特色
                - 回答简洁明了，避免技术 jargon 堆砌
                - 必要时可询问用户更详细的需求'''
    )
    from backend.services.ollama_client import OllamaClient

    llm = OllamaClient()
    answer = await llm.generate(
                f'''已知株洲市高新区设备共享平台的设备资料：
                {context}

                用户咨询：{query}

                请根据以上资料，按照要求格式生成推荐回答。注意：
                1. 优先推荐匹配度高、可用性好的设备，如果有多台设备符合需求，可以推荐多台设备；
                2. 若用户需求不明确，可请求更详细的技术参数要求；
                3. 确保推荐理由基于提供的设备资料；
                4. 株洲本地设备优先推荐。''',
        system=system,
    )
    await llm.aclose()
    return {"answer": answer, "sources": docs, "recommendations": docs}


async def api_check_duplicate(payload: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """按指定关键字段检查是否重复。

    预期字段：name, parameters, test_items, unit_name, address
    """
    store = RagStoreFaiss()
    dup = store.find_duplicate(
        name=payload.get("name"),
        parameters=payload.get("parameters"),
        test_items=payload.get("test_items"),
        unit_name=payload.get("unit_name"),
        address=payload.get("address"),
    )
    if dup:
        return {"duplicate": True, "item": dup}
    return {"duplicate": False}


async def api_batch_upsert(req: BatchUpsertRequest) -> BatchUpsertResponse:
    """批量插入API - 提高导入性能"""
    import time
    start_time = time.time()
    
    total = len(req.items)
    success_count = 0
    failed_count = 0
    duplicate_count = 0
    results = []
    
    # 批量生成embedding
    client = EmbeddingsClient()
    try:
        # 1. 准备所有文本
        texts = []
        for item in req.items:
            text = f"{item.name}\n{item.description or ''}\nTags: {', '.join(item.tags or [])}"
            texts.append(text)
        
        # 2. 批量生成向量 (这里仍然需要逐个调用，但可以考虑并发)
        vectors = []
        for i, text in enumerate(texts):
            try:
                vector = await client.embed(text)
                vectors.append(vector)
                logger.info("batch.embed.success", extra={"event": "batch_embed", "index": i})
            except Exception as e:
                logger.error("batch.embed.error", extra={"event": "batch_embed_error", "index": i, "error": str(e)})
                vectors.append(None)
        
        # 3. 批量处理地理编码
        addresses = [item.address or "" for item in req.items]
        geocode_results = []
        for address in addresses:
            if address.strip():
                try:
                    geo_result = await geocode_address(address)
                    geocode_results.append(geo_result)
                except Exception:
                    geocode_results.append({"lat": None, "lng": None})
            else:
                geocode_results.append({"lat": None, "lng": None})
        
        # 4. 批量存储
        store = RagStoreFaiss()
        for i, (item, vector, geo_result) in enumerate(zip(req.items, vectors, geocode_results)):
            result = BatchUpsertResult(
                success=False,
                name=item.name,
                duplicate=False
            )
            
            try:
                if vector is None:
                    result.error = "向量生成失败"
                    failed_count += 1
                else:
                    # 检查重复
                    md = item.metadata or {}
                    dup = store.find_duplicate(
                        name=item.name,
                        parameters=(md.get("参数") if isinstance(md, dict) else None),
                        test_items=(md.get("测试项目") if isinstance(md, dict) else None),
                        unit_name=((md.get("单位名称") or md.get("单位")) if isinstance(md, dict) else None),
                        address=(item.address or (md.get("原始地址") if isinstance(md, dict) else None)),
                    )
                    
                    if dup:
                        result.duplicate = True
                        result.item_id = dup["id"]
                        duplicate_count += 1
                    else:
                        # 插入新记录
                        lat_val = item.lat if item.lat is not None else geo_result.get("lat")
                        lng_val = item.lng if item.lng is not None else geo_result.get("lng")
                        
                        item_id = store.upsert(
                            name=item.name,
                            description=item.description or "",
                            tags=[t.strip().lower() for t in (item.tags or []) if t.strip()],
                            image_url=item.image_url or "",
                            vector=vector,
                            address=item.address,
                            lat=lat_val,
                            lng=lng_val,
                            metadata=item.metadata,
                            quantity=item.quantity,
                        )
                        
                        result.success = True
                        result.item_id = item_id
                        success_count += 1
                        
            except Exception as e:
                result.error = str(e)
                failed_count += 1
            
            results.append(result)
    
    finally:
        await client.aclose()
    
    processing_time = time.time() - start_time
    
    return BatchUpsertResponse(
        total=total,
        success=success_count,
        failed=failed_count,
        duplicates=duplicate_count,
        processing_time=round(processing_time, 2),
        results=results
    )


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


async def api_search_by_meta(req: SearchByMetaRequest) -> Dict[str, Any]:
    """名称 + metadata 多维度搜索（模糊/精确），支持分页与排序。

    说明：
    - 不依赖向量检索，仅在内存条目上基于结构化字段进行筛选和评分
    - 支持名称匹配模式：exact | contains | fuzzy（默认 fuzzy）
    - 支持 metadata 多字段过滤（AND/OR 组合、数值区间、in/contains 等）
    - 返回结构与 /api/rag/items 一致：{ total,page,size,items[] }
    """
    store = RagStoreFaiss()
    return store.search_by_name_and_metadata(req)


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

    # 先做一次查重（不依赖 image_url）
    # 尝试从 description 中提取关键字段用于查重
    def _kv2(key: str) -> Optional[str]:
        import re as _re
        m = _re.search(rf"{key}[:：]\s*(.+)", description or "")
        return m.group(1).strip() if m else None
    meta_like2 = {
        "参数": _kv2("参数"),
        "测试项目": _kv2("测试项目"),
        "单位名称": _kv2("单位名称") or _kv2("单位"),
        "原始地址": address or "",
    }
    store_chk = RagStoreFaiss()
    dup_pre = store_chk.find_duplicate(
        name=name,
        parameters=meta_like2.get("参数"),
        test_items=meta_like2.get("测试项目"),
        unit_name=meta_like2.get("单位名称"),
        address=address,
    )
    if dup_pre:
        return ItemResponse(**dup_pre)  # type: ignore[arg-type]

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


@router.post("/geocode")
async def http_geocode(request: Dict[str, str]) -> Dict[str, Optional[float]]:
    """地理编码API - 将地址转换为经纬度坐标"""
    try:
        address = request.get("address", "").strip()
        if not address:
            return {"lat": None, "lng": None}
        
        result = await geocode_address(address)
        return {
            "lat": result.get("lat"),
            "lng": result.get("lng")
        }
    except Exception as e:
        logger.error("geocode.error", extra={"event": "geocode_error", "error": str(e)})
        return {"lat": None, "lng": None}


@router.post("/upsert_full", response_model=ItemResponse)
async def http_upsert_full(req: UpsertFullRequest) -> ItemResponse:
    """JSON 方式新增/更新（与批量导入对齐）。"""
    try:
        return await api_upsert_full(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch_upsert", response_model=BatchUpsertResponse)
async def http_batch_upsert(req: BatchUpsertRequest) -> BatchUpsertResponse:
    """批量插入API - 性能优化版本"""
    try:
        return await api_batch_upsert(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import_excel_async", response_model=dict)
async def http_import_excel_async(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("append"),
    concurrency: int = Form(10),
    sheet: str = Form("0"),
) -> Dict[str, Any]:
    """接收 Excel 文件并直接执行异步导入（复用 tools/import_excel_async.py 逻辑）。

    用法（Postman，multipart/form-data）：
    - URL: /api/rag/import_excel_async
    - form-data:
      - file: 选择本地 Excel 文件（例如 data/device_data.xlsx）
      - mode: append|overwrite（默认 append；overwrite 会清空数据+索引并删除导入图片）
      - concurrency: 并发数（默认 10）
      - sheet: 工作表名称或索引（默认 "0"）

    返回：
    - 与脚本导入 summary 一致的 JSON（total/success/failed/duplicates/elapsed_seconds/...）

    实现说明：
    - 将上传文件保存到相对目录（项目根/data），避免绝对路径依赖，便于后续移植。
    - 通过 request.base_url 作为 api_base，脚本内部通过 HTTP 调用本服务既有 API 实现导入。
    - 若 mode=overwrite，脚本会自动调用 /api/rag/clear_all 并 cleanup_images=true。
    """
    try:
        import os as _os
        from pathlib import Path as _Path
        import uuid as _uuid

        # 1) 解析 sheet（可能是索引或名称）
        sheet_param: Any
        try:
            sheet_param = int(sheet)
        except Exception:
            sheet_param = sheet

        # 2) 将上传文件保存到项目根 data 目录（相对路径）
        project_root = _Path(__file__).resolve().parents[1]
        data_dir = project_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = _Path(file.filename).suffix or ".xlsx"
        save_name = f"import_{_uuid.uuid4().hex}{ext}"
        save_path = data_dir / save_name
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)

        # 3) 准备参数并调用脚本的 run_import_async
        #    - 使用环境变量传递 mode（脚本会读取 IMPORT_MODE）
        _os.environ["IMPORT_MODE"] = (mode or "append").strip().lower()

        # 计算 api_base，自引用本服务
        api_base = str(request.base_url).rstrip("/")

        # 惰性导入工具函数，避免模块路径问题
        try:
            from tools.import_excel_async import run_import_async as _run_import_async
        except Exception:
            import sys as _sys
            _sys.path.insert(0, str(project_root))
            from tools.import_excel_async import run_import_async as _run_import_async

        # 执行导入
        result = await _run_import_async(file=str(save_path), api=api_base, concurrency=int(concurrency), sheet=sheet_param)
        return result
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


@router.post("/check_duplicate", response_model=dict)
async def http_check_duplicate(body: Dict[str, Optional[str]]) -> dict:
    """检查是否存在重复条目。

    body: { name, parameters, test_items, unit_name, address }
    """
    try:
        return await api_check_duplicate(body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear_all")
async def http_clear_all(options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """清空所有RAG数据与FAISS索引；可选同时清理导入脚本产生的图片。

    请求体（可选，application/json）：
    - cleanup_images: bool  是否同时删除导入脚本生成的图片文件（默认 True
    - prefixes: string[]    需要删除的文件名前缀（默认 ["import_embed_", "import_shape_", "import_disp_"]）

    用法示例：
    1) 仅清空数据与索引（默认行为）
       curl -X POST http://<host>:<port>/api/rag/clear_all

    2) 覆盖导入前执行：清空数据与索引，并删除导入图片
       curl -X POST http://<host>:<port>/api/rag/clear_all \
            -H "Content-Type: application/json" \
            -d '{"cleanup_images": true}'

    说明：
    - 图片删除仅作用于 static/uploads 下由导入脚本生成的文件，
      通过前缀限制避免误删其他业务图片。
    - 路径解析相对于项目根目录进行，不使用硬编码绝对路径。
    """
    try:
        store = RagStoreFaiss()
        old_count = len(store._items)
        store.clear_all_data()

        # 2) 可选清理导入脚本生成的图片
        images_info: Dict[str, Any] = {}
        cleanup_images = bool((options or {}).get("cleanup_images", True))
        prefixes = (options or {}).get("prefixes") or ["import_embed_", "import_shape_", "import_disp_"]

        if cleanup_images:
            try:
                settings = get_settings()
                # 统一锚定到项目根 static 目录，避免工作目录差异与绝对路径依赖
                static_root = Path(__file__).resolve().parents[1] / "static"
                uploads_dir = (
                    static_root / Path(settings.uploads_dir).name
                    if not Path(settings.uploads_dir).is_absolute()
                    else Path(settings.uploads_dir)
                )

                matched = 0
                deleted = 0
                if uploads_dir.exists() and uploads_dir.is_dir():
                    for p in uploads_dir.iterdir():
                        try:
                            if p.is_file() and any(p.name.startswith(pref) for pref in prefixes):
                                matched += 1
                                p.unlink(missing_ok=True)
                                deleted += 1
                        except Exception:
                            # 单文件失败不影响总体流程
                            continue

                images_info = {
                    "uploads_dir": f"/static/{uploads_dir.relative_to(static_root).as_posix()}" if uploads_dir.exists() else "/static/uploads",
                    "matched_count": matched,
                    "deleted_count": deleted,
                    "prefixes": prefixes,
                }
            except Exception as e:
                # 图片清理失败不阻断清空流程，返回错误信息以便排查
                images_info = {"error": str(e), "prefixes": prefixes}

        return {
            "success": True,
            "message": f"已清空所有数据，原有{old_count}个设备记录已删除",
            "cleared_count": old_count,
            "images_cleanup": images_info or None,
        }
    except Exception as e:
        logger.error("clear_all.error", extra={"event": "clear_all_error", "error": str(e)})
        raise HTTPException(status_code=500, detail=f"清空数据失败: {str(e)}")


@router.post("/search_by_meta", response_model=SearchByMetaResponse)
async def http_search_by_meta(req: SearchByMetaRequest) -> dict:
    """名称 + metadata 高级搜索接口
    name:
    名称关键词
    match_mode: exact | contains | fuzzy（默认 fuzzy）
    min_score: 仅在 fuzzy 模式下生效（0~1）
    metadata_filters: 键为元数据字段名，值为条件对象：
    op: contains(默认) | eq | neq | in | gt | gte | lt | lte | range | exists
    value: 用于 contains/eq/neq/in
    min/max: 用于 range
    logic: 字段间逻辑 AND | OR（默认 AND）
    sort: {"field": "relevance"|"name", "order": "asc"|"desc"}
    page/size: 分页，size 最大 200
    
    返回结构
    total: 命中条目总数
    page/size: 分页信息
    items: 列表项含 id, name, description, tags, image_url, address, lat, lng, metadata, quantity, score

    使用方式（Postman 或 curl）：
    1) 模糊匹配名称 + 多字段 contains 过滤：
       curl -X POST ${HOST}/api/rag/search_by_meta \
            -H "Content-Type: application/json" \
            -d '{
                  "name": "扫描电镜",
                  "match_mode": "fuzzy",
                  "min_score": 0.55,
                  "metadata_filters": {
                    "单位名称": {"op": "contains", "value": "工业大学"},
                    "测试项目": {"op": "in", "value": ["形貌观察", "能谱分析"]}
                  },
                  "logic": "AND",
                  "sort": {"field": "relevance", "order": "desc"},
                  "page": 1,
                  "size": 20
                }'

    2) 仅 metadata 数值区间过滤（不传 name）：
       curl -X POST ${HOST}/api/rag/search_by_meta \
            -H "Content-Type: application/json" \
            -d '{
                  "metadata_filters": {
                    "加速电压": {"op": "range", "min": 5, "max": 30}
                  },
                  "page": 1,
                  "size": 10
                }'

    3) 名称 contains 精确包含匹配 + OR 逻辑：
       curl -X POST ${HOST}/api/rag/search_by_meta \
            -H "Content-Type: application/json" \
            -d '{
                  "name": "显微镜",
                  "match_mode": "contains",
                  "metadata_filters": {
                    "单位名称": {"op": "contains", "value": "研究院"},
                    "测试项目": {"op": "contains", "value": "XRD"}
                  },
                  "logic": "OR",
                  "page": 1,
                  "size": 15
                }'

    返回示例：
    {
      "total": 156,
      "page": 1,
      "size": 20,
      "items": [
        {
          "id": "...",
          "name": "扫描电子显微镜(SEM)",
          "description": "...",
          "tags": ["sem", "microscopy"],
          "image_url": "/static/uploads/xxx.png",
          "address": "...",
          "lat": 28.2,
          "lng": 112.9,
          "metadata": {"单位名称": "湖南工业大学", "测试项目": ["形貌观察", "能谱分析"]},
          "quantity": 1,
          "score": 0.86
        }
      ]
    }
    """
    try:
        logger.info(
            "api.search_by_meta.request",
            extra={
                "event": "search_by_meta",
                "has_name": bool(req.name),
                "filters_count": len(req.metadata_filters or {}),
                "logic": (req.logic or "AND"),
                "match_mode": (req.match_mode or "fuzzy"),
                "page": req.page,
                "size": req.size,
            },
        )
        return await api_search_by_meta(req)
    except Exception as e:
        logger.error("api.search_by_meta.error", extra={"event": "search_by_meta_error", "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------- 聚合其他模块路由（方案A） --------------------------
from backend.routers.auth import router as auth_router  # noqa: E402
from backend.routers.lease import router as lease_router  # noqa: E402
from backend.routers.forum import router as forum_router  # noqa: E402

# 导出统一聚合路由，供 main.py 统一注册
api_router = APIRouter()
api_router.include_router(router)  # 本文件 RAG 路由
api_router.include_router(auth_router)
api_router.include_router(lease_router)
api_router.include_router(forum_router)