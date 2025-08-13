"""
Pydantic 请求/响应模型

为何需要：
- 明确 API 边界，提供类型校验与自动文档
"""

from typing import List
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    prompt: str = Field(..., description="用户输入的对话内容")


class ChatResponse(BaseModel):
    reply: str = Field(..., description="模型生成的回复")


class RecommendRequest(BaseModel):
    experiment: str = Field(..., description="实验描述")


class EquipmentItem(BaseModel):
    name: str
    description: str
    tags: List[str]
    image_url: str
    score: float


class RecommendResponse(BaseModel):
    items: List[EquipmentItem]


