"""
Pydantic 请求/响应模型

为何需要：
- 明确 API 边界，提供类型校验与自动文档
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    prompt: str = Field(..., description="用户输入的对话内容")


class ChatResponse(BaseModel):
    reply: str = Field(..., description="模型生成的回复")


class RecommendRequest(BaseModel):
    experiment: str = Field(..., description="实验描述")
    user_location: Optional[Dict[str, float]] = Field(None, description="用户位置 {lat: float, lng: float}")
    max_distance: Optional[float] = Field(50.0, description="最大搜索距离(km)")


class EquipmentItem(BaseModel):
    name: str
    description: str
    tags: List[str]
    image_url: str
    score: float
    distance: Optional[float] = Field(None, description="距离用户的距离(km)")
    location: Optional[Dict[str, Any]] = Field(None, description="设备位置信息")
    # 扩展字段：用于列表/搜索返回完整信息
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    quantity: Optional[int] = Field(None, description="台数/数量")


class RecommendResponse(BaseModel):
    items: List[EquipmentItem]


class UpsertFullRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    tags: List[str] = []
    image_url: Optional[str] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    quantity: Optional[int] = None


class ItemResponse(BaseModel):
    id: str
    name: str
    description: str
    tags: List[str]
    image_url: str
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    quantity: Optional[int] = None


class BatchUpsertRequest(BaseModel):
    """批量插入请求"""
    items: List[UpsertFullRequest] = Field(..., description="要批量插入的设备列表")
    batch_size: Optional[int] = Field(50, description="批处理大小")


class BatchUpsertResult(BaseModel):
    """单个批量插入结果"""
    success: bool
    item_id: Optional[str] = None
    error: Optional[str] = None
    name: str
    duplicate: bool = False


class BatchUpsertResponse(BaseModel):
    """批量插入响应"""
    total: int
    success: int
    failed: int
    duplicates: int
    processing_time: float
    results: List[BatchUpsertResult]


