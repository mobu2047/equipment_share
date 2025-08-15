"""
设备推荐路由

职责：
- 接收实验描述 -> 通过向量检索（FAISS）返回 Top-3 设备
"""

from fastapi import APIRouter, HTTPException

from backend.models.schemas import RecommendRequest, RecommendResponse, EquipmentItem
from backend.core.logger import logger
from backend.services.embeddings_client import EmbeddingsClient
from backend.services.rag_store_faiss import RagStoreFaiss


router = APIRouter(prefix="/api", tags=["equipment"])


@router.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest) -> RecommendResponse:
    logger.info("api.recommend.request", extra={"event": "recommend_rag", "len": len(req.experiment)})
    # 1) 生成查询向量
    emb = EmbeddingsClient()
    try:
        qvec = await emb.embed(req.experiment)
        await emb.aclose()
    except Exception as e:
        logger.error("api.recommend.embed_error", extra={"event": "recommend_embed_error"})
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")

    # 2) FAISS 检索 Top-3
    store = RagStoreFaiss()
    hits = store.search(query_vector=qvec, top_k=3, query_text=req.experiment)
    items = [EquipmentItem(**it) for it in hits]
    logger.info("api.recommend.success", extra={"event": "recommend_ok", "count": len(items)})
    return RecommendResponse(items=items)


