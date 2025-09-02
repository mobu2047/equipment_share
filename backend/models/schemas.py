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


# ---------------- 新增：名称+metadata 高级搜索模型 ----------------
class MetaFilterCondition(BaseModel):
    """单个 metadata 过滤条件

    说明：
    - op 操作符：
      - contains: 字符串包含（默认）
      - eq/neq: 全等/不等（字符串或可解析数值）
      - in: 候选值之一（value 需为数组）
      - gt/gte/lt/lte: 数值比较（自动从字符串中提取数值进行比较）
      - range: 数值区间（闭区间），使用 min/max
      - exists: 字段存在且非空
    - value/min/max 支持字符串或数值。数值类操作会尽量从字符串中解析浮点数
    """

    op: str = Field("contains", description="操作符: contains|eq|neq|in|gt|gte|lt|lte|range|exists")
    value: Optional[Any] = Field(None, description="用于 contains/eq/neq/in 的比较值")
    min: Optional[float] = Field(None, description="用于 range 的最小值")
    max: Optional[float] = Field(None, description="用于 range 的最大值")


class SearchByMetaRequest(BaseModel):
    """基于名称与 metadata 的多维度搜索请求

    字段说明：
    - name: 名称关键词，支持 exact/contains/fuzzy 三种匹配
    - metadata_filters: 多字段过滤条件（key=元数据字段名，value=MetaFilterCondition）
    - logic: 字段间逻辑 AND|OR（默认 AND）
    - match_mode: 名称匹配模式 exact|contains|fuzzy（默认 fuzzy）
    - min_score: 最小相关性阈值(0~1)，仅 fuzzy 模式有效（默认 0）
    - sort: 排序 {field, order}，field=relevance|name（默认 relevance desc）
    - page/size: 分页参数（size 最大 200）
    -   name:
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
    """
    """
    使用示例：模糊匹配名稱+and過濾：
    curl -X POST http://localhost:8000/api/rag/search_by_meta \
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
    仅按 metadata 数值区间过滤
    curl -X POST http://localhost:8000/api/rag/search_by_meta \
    -H "Content-Type: application/json" \
    -d '{
        "metadata_filters": {
          "加速电压": {"op": "range", "min": 5, "max": 30}
        },
        "page": 1,
        "size": 10
      }'
    名称包含匹配 + OR 过滤
    curl -X POST http://localhost:8000/api/rag/search_by_meta \
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
    """

    name: Optional[str] = Field(None, description="名称关键词")
    metadata_filters: Optional[Dict[str, MetaFilterCondition]] = Field(
        default_factory=dict, description="metadata 过滤条件集合"
    )
    logic: str = Field("AND", description="字段间逻辑: AND|OR")
    match_mode: str = Field("fuzzy", description="名称匹配: exact|contains|fuzzy")
    min_score: float = Field(0.0, ge=0.0, le=1.0, description="最小相关性阈值，仅 fuzzy 有效")
    sort: Dict[str, str] = Field(
        default_factory=lambda: {"field": "relevance", "order": "desc"},
        description="排序设置"
    )
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=200)


class SearchByMetaResponse(BaseModel):
    """基于名称与 metadata 的搜索响应

    返回结构与 /api/rag/items 保持一致：包含分页信息与 items 列表；
    items 中将包含 score 字段（0~1），用于前端可选展示排序依据。
    """

    total: int
    page: int
    size: int
    items: List[Dict[str, Any]]


# ---------------- 鉴权/用户相关 ----------------
class SMSRequest(BaseModel):
    phone: str = Field(..., description="手机号")
    scene: str = Field("login", description="场景：login|register")


class SMSVerifyRequest(BaseModel):
    phone: str
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = Field("bearer")


class UserInfo(BaseModel):
    id: int
    phone: str
    display_name: Optional[str] = None
    roles: List[str] = []


# ---------------- 租赁相关 ----------------
class LeaseCreateRequest(BaseModel):
    device_id: Optional[str] = Field(None, description="可选：RAG 条目 ID")
    device_name: Optional[str] = Field(None, description="可选：设备名快照")
    requested_start_at: Optional[str] = Field(None, description="请求开始时间 ISO8601")
    requested_end_at: Optional[str] = Field(None, description="请求结束时间 ISO8601")
    remark: Optional[str] = None


class LeaseItem(BaseModel):
    id: int
    status: str
    device_id: Optional[str]
    device_name_snapshot: Optional[str]
    requested_start_at: Optional[str]
    requested_end_at: Optional[str]
    confirmed_start_at: Optional[str]
    confirmed_end_at: Optional[str]
    created_at: str


class LeaseListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: List[LeaseItem]


class LeaseConfirmRequest(BaseModel):
    device_id: str
    confirmed_start_at: str
    confirmed_end_at: str
    provider_user_id: Optional[int] = None


class LeaseActionRequest(BaseModel):
    reason: Optional[str] = None


# ---------------- 论坛相关 ----------------
class ThreadCreateRequest(BaseModel):
    title: str
    content: str
    category: str = Field("general")
    lease_order_id: Optional[int] = None


class ThreadListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: List[Dict[str, Any]]


class PostCreateRequest(BaseModel):
    content: str
    parent_post_id: Optional[int] = None

