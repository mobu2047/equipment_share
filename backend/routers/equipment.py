"""
设备推荐路由

职责：
- 接收实验描述 -> 计算推荐结果 -> 返回结构化列表
"""

from fastapi import APIRouter

from backend.models.schemas import RecommendRequest, RecommendResponse, EquipmentItem
from backend.services.equipment_selector import recommend_equipment
from backend.core.logger import logger


router = APIRouter(prefix="/api", tags=["equipment"])


@router.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest) -> RecommendResponse:
    logger.info("api.recommend.request", extra={"event": "recommend", "len": len(req.experiment)})
    data = recommend_equipment(req.experiment)
    items = [EquipmentItem(**it) for it in data["items"]]
    logger.info("api.recommend.success", extra={"event": "recommend_ok", "count": len(items)})
    return RecommendResponse(items=items)


