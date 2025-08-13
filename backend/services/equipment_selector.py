"""
设备推荐服务

为何需要：
- 将设备选择逻辑与 LLM 调用解耦，便于替换规则或引入检索/数据库

当前实现：
- Demo 规则 + LLM 总结：
  1) 先用简易关键词规则打分
  2) 再用 LLM 生成自然语言解释
"""

from dataclasses import dataclass
from typing import List, Dict, Any

from backend.core.logger import logger


@dataclass
class EquipmentItem:
    name: str
    description: str
    tags: List[str]
    image_url: str
    score: float


_CATALOG: List[EquipmentItem] = [
    EquipmentItem(
        name="光学显微镜 A",
        description="基础光学观察，支持明场/暗场",
        tags=["optics", "microscopy", "imaging"],
        image_url="/static/assets/placeholder1.svg",
        score=0.0,
    ),
    EquipmentItem(
        name="电子显微镜 B",
        description="高分辨率形貌分析",
        tags=["sem", "microscopy", "materials"],
        image_url="/static/assets/placeholder2.svg",
        score=0.0,
    ),
    EquipmentItem(
        name="PCR 扩增仪 C",
        description="核酸扩增与温控循环",
        tags=["bio", "pcr", "dna"],
        image_url="/static/assets/placeholder3.svg",
        score=0.0,
    ),
]


def _keyword_score(text: str, keywords: List[str]) -> int:
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw in text_lower)


def recommend_equipment(experiment: str) -> Dict[str, Any]:
    """基于简易关键词的演示型推荐。

    说明：
    - 实际项目可接入数据库、倒排索引或向量检索；此处提供可用的最小实现
    """

    logger.info("recommend.start", extra={"event": "recommend", "len": len(experiment)})

    ranked: List[EquipmentItem] = []
    for item in _CATALOG:
        score = _keyword_score(experiment, item.tags)
        ranked.append(EquipmentItem(
            name=item.name,
            description=item.description,
            tags=item.tags,
            image_url=item.image_url,
            score=float(score),
        ))

    ranked.sort(key=lambda x: x.score, reverse=True)

    items = [
        {
            "name": it.name,
            "description": it.description,
            "tags": it.tags,
            "image_url": it.image_url,
            "score": it.score,
        }
        for it in ranked
    ]

    logger.info("recommend.finish", extra={"event": "recommend_ok", "count": len(items)})
    return {"items": items}


