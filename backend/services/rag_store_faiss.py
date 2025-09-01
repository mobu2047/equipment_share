"""
基于FAISS的向量存储（RAG Store）

设计目标：
- 使用FAISS进行高效的向量检索
- 支持增量写入与查询，适合中大规模数据集
- 保持与原有RagStore接口兼容
"""

import json
import os
import threading
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import numpy as np
import faiss
import hashlib

from backend.core.config import get_settings
from backend.core.logger import logger
from backend.models.schemas import SearchByMetaRequest


_LOCK = threading.Lock()


CURRENT_DEDUP_VER = 1


@dataclass
class RagItem:
    id: str
    name: str
    description: str
    tags: List[str]
    image_url: str
    text: str
    # 不再在 JSON 中持久化向量，仅由 FAISS 索引保存
    faiss_id: int
    # 扩展字段
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    quantity: Optional[int] = None
    # 兼容原先的位置信息
    location: Optional[Dict[str, Any]] = None
    # 持久化去重键（五要素规范化后拼接的 sha1）与版本
    dedup_key: Optional[str] = None
    dedup_ver: Optional[int] = None


class RagStoreFaiss:
    """基于FAISS的高效向量库。"""

    def __init__(self) -> None:
        settings = get_settings()
        # 将数据目录锚定到项目根，避免因工作目录变化导致找不到数据
        project_root = Path(__file__).resolve().parents[2]
        configured = Path(settings.data_dir)
        self._data_dir = configured if configured.is_absolute() else (project_root / configured)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        
        # 数据文件
        self._store_file = self._data_dir / "rag_store_faiss.json"
        self._index_file = self._data_dir / "faiss_index.bin"
        
        # 内存数据
        self._items: List[RagItem] = []
        # 使用带 ID 的索引，便于删除/更新：IndexIDMap2(IndexFlatIP)
        self._index: Optional[faiss.Index] = None
        self._dimension: Optional[int] = None  # 在首次写入或加载索引时确定
        # 五要素去重索引：key(sha1 of normalized 5-tuple) -> item_id
        self._dup_index: Dict[str, str] = {}
        
        self._load()
        self._init_index()
        # 启动时从已加载的 items 重建去重索引
        self._rebuild_dup_index()

    def _load(self) -> None:
        """加载元数据。"""
        if not self._store_file.exists():
            logger.info("rag_store_faiss.init_empty", extra={"event": "rag_store_faiss"})
            return
        try:
            with _LOCK, self._store_file.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            loaded: List[RagItem] = []
            for it in raw.get("items", []):
                try:
                    # 兼容旧数据：容错缺失/多余字段
                    name = it.get("name", "")
                    description = it.get("description", "")
                    tags = it.get("tags") or []
                    if not isinstance(tags, list):
                        tags = []
                    image_url = it.get("image_url", "")
                    text = it.get("text") or f"{name}\n{description}\nTags: {', '.join(tags)}"
                    # 不再从 JSON 读取向量
                    item_id = it.get("id") or str(uuid.uuid4())
                    # 固定可复现的 int64 faiss_id（基于 UUID）
                    faiss_id = it.get("faiss_id")
                    if not isinstance(faiss_id, int):
                        try:
                            faiss_id = uuid.UUID(item_id).int % (2**63 - 1)
                        except Exception:
                            faiss_id = abs(hash(item_id)) % (2**63 - 1)
                    # 加载位置信息与扩展字段（新规范：仅持久化 location）
                    address = it.get("address")
                    lat = it.get("lat")
                    lng = it.get("lng")
                    metadata = it.get("metadata") if isinstance(it.get("metadata"), dict) else None
                    quantity = it.get("quantity") if isinstance(it.get("quantity"), int) else None
                    location = it.get("location")
                    # 兼容旧数据：如仅有顶层 lat/lng，则构造 location
                    if (not location) and (lat is not None or lng is not None):
                        location = {"lat": lat, "lng": lng}
                    # 去重字段（可能不存在）
                    dedup_key = it.get("dedup_key")
                    dedup_ver = it.get("dedup_ver")

                    item = RagItem(
                        id=item_id,
                        name=name,
                        description=description,
                        tags=tags,
                        image_url=image_url,
                        text=text,
                        faiss_id=faiss_id,
                        address=address,
                        lat=(location.get('lat') if isinstance(location, dict) else None),
                        lng=(location.get('lng') if isinstance(location, dict) else None),
                        metadata=metadata,
                        quantity=quantity,
                        location=location,
                        dedup_key=dedup_key,
                        dedup_ver=dedup_ver,
                    )
                    loaded.append(item)
                except Exception:
                    # 单条容错，避免整体加载失败
                    continue
            self._items = loaded
            logger.info("rag_store_faiss.loaded", extra={"event": "rag_store_faiss_load", "count": len(self._items)})
            # 迁移：若条目缺少 location 但存在顶层 lat/lng，则补齐 location 并落盘一次
            changed = False
            for it in self._items:
                if (not it.location) and (it.lat is not None or it.lng is not None):
                    it.location = {"lat": it.lat, "lng": it.lng}
                    changed = True
            # 迁移：回填缺失或旧版本的去重键
            for it in self._items:
                try:
                    need = (not it.dedup_key) or (it.dedup_ver != CURRENT_DEDUP_VER)
                    if need:
                        k = self._dedup_key_for_item(it)
                        if k:
                            it.dedup_key = k
                            it.dedup_ver = CURRENT_DEDUP_VER
                            changed = True
                except Exception:
                    continue
            if changed:
                self._save()
        except Exception:
            logger.error("rag_store_faiss.load_failed", extra={"event": "rag_store_faiss_load_error"})

    def _init_index(self) -> None:
        """初始化FAISS索引。"""
        # 优先从磁盘加载已存在的索引
        if self._index_file.exists():
            try:
                self._index = faiss.read_index(str(self._index_file))
                # 尝试取得维度
                self._dimension = self._index.d
                logger.info("rag_store_faiss.index_loaded", extra={"event": "index_load", "dimension": self._dimension})
                return
            except Exception:
                logger.warning("rag_store_faiss.index_load_failed", extra={"event": "index_load_error"})
                self._index = None
                self._dimension = None
        # 若无索引文件，则等待首次 upsert/reindex 再创建
        logger.info("rag_store_faiss.index_uninitialized", extra={"event": "index_wait_create"})

    def _save(self) -> None:
        """保存元数据。"""
        # 保存时不持久化向量，仅保存元数据与 faiss_id
        payload_items = []
        for it in self._items:
            obj = asdict(it)
            # 确保没有 vector 字段（向后兼容防护）
            obj.pop("vector", None)
            # 新规范：仅持久化 location；清理顶层 lat/lng
            if "lat" in obj:
                obj.pop("lat", None)
            if "lng" in obj:
                obj.pop("lng", None)
            payload_items.append(obj)
        payload = {"items": payload_items}
        with _LOCK, self._store_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("rag_store_faiss.saved", extra={"event": "rag_store_faiss_save", "count": len(self._items)})

    # ---------------------- 去重索引支持 ----------------------
    def _make_dedup_key(self, name: Optional[str], parameters: Optional[str], test_items: Optional[str],
                         unit_name: Optional[str], address: Optional[str]) -> Optional[str]:
        """基于五要素生成稳定去重键（sha1）。任一要素缺失则返回 None。

        规范化策略沿用 _norm，避免与现有查重口径不一致。
        """
        n_name = self._norm(name)
        n_param = self._norm(parameters)
        n_test = self._norm(test_items)
        n_unit = self._norm(unit_name)
        n_addr = self._norm(address)
        if not all([n_name, n_param, n_test, n_unit, n_addr]):
            return None
        joined = "|".join([n_name, n_param, n_test, n_unit, n_addr])
        return hashlib.sha1(joined.encode("utf-8")).hexdigest()

    def _dedup_key_for_item(self, item: RagItem) -> Optional[str]:
        md = item.metadata or {}
        name = item.name
        parameters = md.get("参数") if isinstance(md, dict) else None
        test_items = md.get("测试项目") if isinstance(md, dict) else None
        unit_name = (md.get("单位名称") or md.get("单位")) if isinstance(md, dict) else None
        address = item.address or (md.get("原始地址") if isinstance(md, dict) else None)
        return self._make_dedup_key(name, parameters, test_items, unit_name, address)

    def _rebuild_dup_index(self) -> None:
        """从内存 items 重建去重索引。优先使用已持久化的 dedup_key。"""
        try:
            self._dup_index = {}
            need_save = False
            for it in self._items:
                try:
                    k = it.dedup_key if (it.dedup_key and it.dedup_ver == CURRENT_DEDUP_VER) else None
                    if not k:
                        k = self._dedup_key_for_item(it)
                        if k:
                            it.dedup_key = k
                            it.dedup_ver = CURRENT_DEDUP_VER
                            need_save = True
                    if k:
                        self._dup_index.setdefault(k, it.id)
                except Exception:
                    continue
            if need_save:
                self._save()
        except Exception:
            self._dup_index = {}

    def _save_index(self) -> None:
        """保存FAISS索引。"""
        if self._index is not None:
            faiss.write_index(self._index, str(self._index_file))
            
            logger.info("rag_store_faiss.index_saved", extra={"event": "index_save"})

    def _ensure_index(self, dim: int) -> None:
        """按需创建带 ID 的索引。"""
        if self._index is None:
            self._dimension = dim
            base = faiss.IndexFlatIP(dim)
            self._index = faiss.IndexIDMap2(base)

    @staticmethod
    def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """计算两点间距离(km) - Haversine公式"""
        import math
        R = 6371  # 地球半径(km)
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (math.sin(dlat/2) ** 2 + 
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
             math.sin(dlng/2) ** 2)
        c = 2 * math.asin(math.sqrt(a))
        return R * c

    @staticmethod
    def _kw_score(query: str, text: str) -> float:
        """简易关键词相似度兜底（Jaccard 近似）。"""
        remove = set(" \t\n\r，。；：、,.!?！?()（）[]【】'\"`|/")
        qa = {ch for ch in query if ch not in remove}
        ta = {ch for ch in text if ch not in remove}
        if not qa or not ta:
            return 0.0
        inter = len(qa & ta)
        union = len(qa | ta) or 1
        return inter / union

    def upsert(self, *, name: str, description: str, tags: List[str], image_url: str, vector: List[float],
               address: Optional[str] = None, lat: Optional[float] = None, lng: Optional[float] = None,
               metadata: Optional[Dict[str, Any]] = None, quantity: Optional[int] = None) -> str:
        """新增或更新条目：用 name+tags 作为简易键去重，如重名则覆盖。"""
        key = (name.strip().lower(), tuple(sorted(t.strip().lower() for t in tags)))
        text = f"{name}\n{description}\nTags: {', '.join(tags)}"

        # 查找是否已有同键条目
        existing_idx = -1
        for idx, it in enumerate(self._items):
            if (it.name.strip().lower(), tuple(sorted(t.strip().lower() for t in it.tags))) == key:
                existing_idx = idx
                break

        item_id = str(uuid.uuid4()) if existing_idx < 0 else self._items[existing_idx].id
        try:
            faiss_id = uuid.UUID(item_id).int % (2**63 - 1)
        except Exception:
            faiss_id = abs(hash(item_id)) % (2**63 - 1)
        # 计算去重键（任一要素缺失时为空）
        md_for_key = metadata or {}
        key_for_insert = self._make_dedup_key(
            name,
            (md_for_key.get("参数") if isinstance(md_for_key, dict) else None),
            (md_for_key.get("测试项目") if isinstance(md_for_key, dict) else None),
            ((md_for_key.get("单位名称") or md_for_key.get("单位")) if isinstance(md_for_key, dict) else None),
            address,
        )

        item = RagItem(
            id=item_id,
            name=name,
            description=description,
            tags=tags,
            image_url=image_url,
            text=text,
            faiss_id=faiss_id,
            address=address,
            lat=lat,
            lng=lng,
            metadata=metadata,
            quantity=quantity,
            location={"lat": lat, "lng": lng} if (lat is not None and lng is not None) else None,
            dedup_key=key_for_insert,
            dedup_ver=(CURRENT_DEDUP_VER if key_for_insert else None),
        )

        if existing_idx >= 0:
            # 更新现有条目
            prev_item = self._items[existing_idx]
            self._items[existing_idx] = item
            # 更新索引中的向量（基于 faiss_id）
            if vector:
                self._ensure_index(len(vector))
                ids = np.array([item.faiss_id], dtype=np.int64)
                try:
                    self._index.remove_ids(ids)
                except Exception:
                    # 某些情况下旧 ID 可能不存在，忽略
                    pass
                vec = np.array([vector], dtype=np.float32)
                faiss.normalize_L2(vec)
                self._index.add_with_ids(vec, ids)
            # 同步去重索引（先移除旧键，再加入新键）
            try:
                old_k = prev_item.dedup_key or self._dedup_key_for_item(prev_item)
                if old_k and self._dup_index.get(old_k) == prev_item.id:
                    self._dup_index.pop(old_k, None)
                new_k = item.dedup_key or self._dedup_key_for_item(item)
                if new_k:
                    self._dup_index[new_k] = item.id
            except Exception:
                pass
        else:
            # 添加新条目
            self._items.append(item)
            # 添加到索引
            if vector:
                self._ensure_index(len(vector))
                vec = np.array([vector], dtype=np.float32)
                faiss.normalize_L2(vec)
                ids = np.array([item.faiss_id], dtype=np.int64)
                self._index.add_with_ids(vec, ids)
            # 新增：加入去重索引
            try:
                new_k = item.dedup_key or self._dedup_key_for_item(item)
                if new_k:
                    self._dup_index.setdefault(new_k, item.id)
            except Exception:
                pass

        self._save()
        self._save_index()



        return item.id

    def search(self, *, query_vector: List[float], top_k: int = 3, query_text: str = "", 
               user_location: Optional[Dict[str, float]] = None, max_distance: float = 50.0) -> List[Dict[str, Any]]:
        """使用FAISS进行向量检索。"""
        if not self._items or self._index is None or not query_vector:
            return []
            
        # 准备查询向量
        q = np.array([query_vector], dtype=np.float32)
        faiss.normalize_L2(q)  # 归一化查询向量
        
        # FAISS检索
        try:
            scores, labels = self._index.search(q, min(top_k, len(self._items)))
            scores = scores[0]  # 取第一个查询的结果
            labels = labels[0]
        except Exception as e:
            logger.error("rag_store_faiss.search_error", extra={"event": "search_error", "error": str(e)})
            return []

        # 构建结果
        results = []
        # 建立 faiss_id -> item 映射
        id_map = {it.faiss_id: it for it in self._items}
        
        # 获取更多候选项用于地理重排序
        search_top_k = top_k * 3 if user_location else top_k
        
        for i, (score, fid) in enumerate(zip(scores, labels)):
            if i >= search_top_k:
                break
            if int(fid) not in id_map:
                continue
            item = id_map[int(fid)]
            
            # 结合向量相似度和关键词相似度
            cos01 = (float(score) + 1.0) / 2.0  # FAISS内积 -> 0..1
            kw = self._kw_score(query_text or "", item.text)
            content_score = 0.75 * cos01 + 0.25 * kw
            
            result_item = {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "tags": item.tags,
                "image_url": item.image_url,
                "address": item.address,
                # 对外输出统一保持顶层 lat/lng（从 location 映射出），兼容前端
                "lat": (item.location.get('lat') if item.location else None),
                "lng": (item.location.get('lng') if item.location else None),
                "metadata": item.metadata,
                "quantity": item.quantity,
                "score": content_score,
                "content_score": content_score,
            }
            
            # 添加位置信息和距离计算
            # 优先使用顶层 lat/lng；若缺失则回退到 location 字段
            device_lat = (item.location.get('lat') if item.location else None)
            device_lng = (item.location.get('lng') if item.location else None)
            if user_location and device_lat is not None and device_lng is not None:
                user_lat = user_location.get('lat', 0)
                user_lng = user_location.get('lng', 0)
                
                distance = self.calculate_distance(user_lat, user_lng, device_lat, device_lng)
                
                # 距离过滤
                if distance <= max_distance:
                    # 距离权重 (距离越近权重越高)
                    distance_weight = max(0, 1 - distance / max_distance)
                    
                    # 综合得分: 70%内容相关性 + 30%距离便利性
                    final_score = 0.7 * content_score + 0.3 * distance_weight
                    
                    result_item.update({
                        "score": final_score,
                        "distance": round(distance, 2),
                        # 继续返回 location 字段用于兼容，但使用计算时的 lat/lng 组装
                        "location": {"lat": device_lat, "lng": device_lng},
                        "distance_weight": distance_weight
                    })
                    results.append(result_item)
            else:
                # 没有位置信息的设备，或用户未提供位置
                results.append(result_item)

        # 按最终得分重新排序，确保返回顺序正确
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    # ---------------------- 查重支持 ----------------------
    @staticmethod
    def _norm(s: Optional[str]) -> str:
        """统一规范化：小写、去两侧空白、压缩空白、去除常见标点差异。

        目的：提高重复判断的匹配稳定性。
        """
        if s is None:
            return ""
        x = str(s).strip().lower()
        # 统一全角括号等
        x = x.replace("（", "(").replace("）", ")").replace("【", "[").replace("】", "]")
        # 压缩连续空白
        x = " ".join(x.split())
        return x

    def find_duplicate(self, *, name: Optional[str], parameters: Optional[str], test_items: Optional[str],
                        unit_name: Optional[str], address: Optional[str]) -> Optional[Dict[str, Any]]:
        """O(1) 查重：基于五要素哈希在去重索引中查找。任一要素缺失返回 None。"""
        key = self._make_dedup_key(name, parameters, test_items, unit_name, address)
        if not key:
            return None
        item_id = self._dup_index.get(key)
        if not item_id:
            return None
        # 返回完整条目（从内存列表取）
        for it in self._items:
            if it.id == item_id:
                return {
                    "id": it.id,
                    "name": it.name,
                    "description": it.description,
                    "tags": it.tags,
                    "image_url": it.image_url,
                    "address": it.address,
                    "lat": it.lat,
                    "lng": it.lng,
                    "metadata": it.metadata,
                    "quantity": it.quantity,
                }
        return None

    # ---------------------- 名称 + metadata 搜索 ----------------------
    def _normalize_string(self, s: Optional[str]) -> str:
        """轻量归一：去空白、全角转半角、统一大小写。

        目的：提升中文/英文混排的匹配稳定性。
        """
        if s is None:
            return ""
        x = str(s)
        # 全角括号等常见符号归一
        x = x.replace("（", "(").replace("）", ")").replace("【", "[").replace("】", "]")
        x = x.strip().lower()
        return x

    @staticmethod
    def _try_parse_float(val: Any) -> Optional[float]:
        """尽量从任意值中解析浮点数；失败返回 None。"""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        import re
        m = re.search(r"[-+]?\d*\.?\d+", str(val))
        try:
            return float(m.group(0)) if m else None
        except Exception:
            return None

    def _match_meta_condition(self, field_value: Any, cond: Dict[str, Any]) -> Tuple[bool, float]:
        """匹配单个 metadata 字段条件，返回 (是否命中, 该字段得分[0~1])。

        评分策略：
        - contains: 命中得 1.0，否则 0
        - eq/neq: 命中得 1.0，否则 0
        - in: 任一命中得 1.0，否则 0
        - 数值比较/区间：满足得 1.0，否则 0
        - exists: 存在且非空得 1.0，否则 0
        """
        op = (cond.get("op") or "contains").lower()
        if op == "exists":
            ok = field_value not in (None, "", [])
            return ok, 1.0 if ok else 0.0

        # 统一字符串比较的归一
        fv_norm = self._normalize_string(field_value if not isinstance(field_value, (list, dict)) else str(field_value))

        if op in ("contains", "eq", "neq"):
            val = cond.get("value")
            val_norm = self._normalize_string(val)
            if op == "contains":
                ok = (val_norm in fv_norm) if (val_norm and fv_norm) else False
                return ok, 1.0 if ok else 0.0
            if op == "eq":
                ok = (val_norm == fv_norm) if (val_norm or fv_norm) else False
                return ok, 1.0 if ok else 0.0
            if op == "neq":
                ok = (val_norm != fv_norm)
                return ok, 1.0 if ok else 0.0

        if op == "in":
            candidates = cond.get("value") or []
            if not isinstance(candidates, list):
                candidates = [candidates]
            for c in candidates:
                c_norm = self._normalize_string(c)
                if c_norm and (c_norm in fv_norm or c_norm == fv_norm):
                    return True, 1.0
            return False, 0.0

        # 数值比较
        fv_num = self._try_parse_float(field_value)
        if op in ("gt", "gte", "lt", "lte", "eq"):
            val_num = self._try_parse_float(cond.get("value"))
            if fv_num is None or val_num is None:
                return False, 0.0
            if op == "gt":
                return (fv_num > val_num), 1.0 if fv_num > val_num else 0.0
            if op == "gte":
                return (fv_num >= val_num), 1.0 if fv_num >= val_num else 0.0
            if op == "lt":
                return (fv_num < val_num), 1.0 if fv_num < val_num else 0.0
            if op == "lte":
                return (fv_num <= val_num), 1.0 if fv_num <= val_num else 0.0
            if op == "eq":
                return (fv_num == val_num), 1.0 if fv_num == val_num else 0.0

        if op == "range":
            min_v = self._try_parse_float(cond.get("min"))
            max_v = self._try_parse_float(cond.get("max"))
            if fv_num is None:
                fv_num = self._try_parse_float(field_value)
            if fv_num is None:
                return False, 0.0
            if (min_v is not None and fv_num < min_v) or (max_v is not None and fv_num > max_v):
                return False, 0.0
            return True, 1.0

        # 未知操作符：不命中
        return False, 0.0

    def search_by_name_and_metadata(self, req: SearchByMetaRequest) -> Dict[str, Any]:
        """名称 + metadata 多维度搜索（模糊/精确），支持分页与排序。

        使用说明：
        - 仅在内存条目上进行过滤与相似度计算，不访问向量索引
        - 适合基于结构化字段与名称的检索需求
        - 返回结构与 /api/rag/items 一致：{"total","page","size","items":[]}
        """

        page = max(1, req.page or 1)
        size = max(1, min(200, req.size or 20))
        logic_and = (req.logic or "AND").upper() == "AND"
        match_mode = (req.match_mode or "fuzzy").lower()
        min_score = max(0.0, min(1.0, req.min_score or 0.0))

        name_query = self._normalize_string(req.name) if req.name else ""

        def name_score_fn(item_name: str) -> float:
            if not name_query:
                return 1.0  # 无名称条件，给满分以便仅靠 metadata 过滤
            name_norm = self._normalize_string(item_name)
            if match_mode == "exact":
                return 1.0 if name_norm == name_query else 0.0
            if match_mode == "contains":
                return 1.0 if (name_query in name_norm) else 0.0
            # fuzzy：使用关键词相似度作为兜底
            try:
                return self._kw_score(name_query, name_norm)
            except Exception:
                return 0.0

        meta_filters: Dict[str, Dict[str, Any]] = {}
        if req.metadata_filters:
            # pydantic 模型到原生 dict
            for k, v in (req.metadata_filters or {}).items():
                meta_filters[k] = v.model_dump() if hasattr(v, "model_dump") else dict(v)

        results: List[Dict[str, Any]] = []
        for it in self._items:
            nscore = name_score_fn(it.name)

            # metadata 匹配
            m_scores: List[float] = []
            m_bools: List[bool] = []
            for field, cond in meta_filters.items():
                field_value = None
                if isinstance(it.metadata, dict) and field in it.metadata:
                    field_value = it.metadata.get(field)
                ok, s = self._match_meta_condition(field_value, cond)
                m_scores.append(s)
                m_bools.append(ok)

            # 逻辑组合
            if meta_filters:
                meta_ok = all(m_bools) if logic_and else any(m_bools)
            else:
                meta_ok = True

            if not meta_ok:
                continue

            # 综合得分（名称与元数据）
            if m_scores:
                meta_score = sum(m_scores) / max(1, len(m_scores))
            else:
                meta_score = 1.0
            score = 0.6 * nscore + 0.4 * meta_score

            if match_mode == "fuzzy" and score < min_score:
                continue

            results.append({
                "id": it.id,
                "name": it.name,
                "description": it.description,
                "tags": it.tags,
                "image_url": it.image_url,
                "address": it.address,
                "lat": it.lat,
                "lng": it.lng,
                "metadata": it.metadata,
                "quantity": it.quantity,
                "score": round(float(score), 4),
            })

        # 排序
        sort_field = (req.sort or {}).get("field", "relevance")
        sort_order = (req.sort or {}).get("order", "desc").lower()
        reverse = (sort_order != "asc")
        if sort_field == "name":
            results.sort(key=lambda x: self._normalize_string(x.get("name")), reverse=reverse)
        else:
            # relevance
            results.sort(key=lambda x: x.get("score", 0.0), reverse=reverse)

        total = len(results)
        start = (page - 1) * size
        end = start + size
        return {"total": total, "page": page, "size": size, "items": results[start:end]}
    
    def _get_raw_item_data(self, item_id: str) -> Optional[Dict]:
        """获取原始JSON数据中的条目信息"""
        try:
            with self._store_file.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            for item in raw.get("items", []):
                if item.get("id") == item_id:
                    return item
        except Exception:
            pass
        return None

    def list_items(self) -> List[Dict[str, Any]]:
        """返回所有条目信息（不包含向量）。"""
        return [
            {
                "id": it.id,
                "name": it.name,
                "description": it.description,
                "tags": it.tags,
                "image_url": it.image_url,
                "address": it.address,
                "lat": it.lat,
                "lng": it.lng,
                "metadata": it.metadata,
                "quantity": it.quantity,
            }
            for it in self._items
        ]

    def delete(self, item_id: str) -> bool:
        """按 id 删除条目，返回是否删除成功。"""
        for idx, it in enumerate(self._items):
            if it.id == item_id:
                # 先从索引中删除
                if self._index is not None:
                    try:
                        self._index.remove_ids(np.array([it.faiss_id], dtype=np.int64))
                    except Exception:
                        pass
                # 从去重索引移除
                try:
                    k = it.dedup_key or self._dedup_key_for_item(it)
                    if k and self._dup_index.get(k) == it.id:
                        self._dup_index.pop(k, None)
                except Exception:
                    pass
                del self._items[idx]
                self._save()
                self._save_index()
                return True
        return False

    def _rebuild_index_from_vectors(self, ids: List[int], vectors: np.ndarray) -> None:
        """用给定向量重建索引（不落盘向量）。"""
        if vectors.size == 0:
            self._index = None
            self._dimension = None
            return
        dim = vectors.shape[1]
        self._ensure_index(dim)
        # 重新构建
        base = faiss.IndexFlatIP(dim)
        self._index = faiss.IndexIDMap2(base)
        faiss.normalize_L2(vectors)
        self._index.add_with_ids(vectors, np.array(ids, dtype=np.int64))

    def clear_all_data(self) -> None:
        """清空所有数据和索引，准备重新导入。"""
        logger.info("rag_store_faiss.clearing_all", extra={"event": "clear_all", "count": len(self._items)})
        
        # 清空内存数据
        self._items.clear()
        self._dup_index.clear()
        
        # 重置索引
        self._index = None
        self._dimension = None
        
        # 删除磁盘文件
        if self._store_file.exists():
            self._store_file.unlink()
            logger.info("rag_store_faiss.deleted_metadata", extra={"event": "delete_metadata"})
        
        if self._index_file.exists():
            self._index_file.unlink()
            logger.info("rag_store_faiss.deleted_index", extra={"event": "delete_index"})
        
        logger.info("rag_store_faiss.cleared", extra={"event": "clear_complete"})

    async def reindex_async(self, embed_async) -> int:
        """使用异步 embed 函数重建全部向量。

        embed_async: Awaitable[str -> List[float]]
        返回：成功重建的条目数量
        """
        vectors_list: List[List[float]] = []
        ids_list: List[int] = []
        updated = 0
        for it in self._items:
            try:
                new_vec = await embed_async(it.text)
                if new_vec:
                    vectors_list.append(list(new_vec))
                    ids_list.append(it.faiss_id)
                    updated += 1
            except Exception:
                continue

        if updated:
            arr = np.array(vectors_list, dtype=np.float32)
            self._rebuild_index_from_vectors(ids_list, arr)
            self._save()
            self._save_index()

        return updated

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        for it in self._items:
            if it.id == item_id:
                return {
                    "id": it.id,
                    "name": it.name,
                    "description": it.description,
                    "tags": it.tags,
                    "image_url": it.image_url,
                    "address": it.address,
                    "lat": it.lat,
                    "lng": it.lng,
                    "metadata": it.metadata,
                    "quantity": it.quantity,
                }
        return None

    def list_items_filtered(self, *, tags: List[str], match: str = "any", q: str = "",
                             page: int = 1, size: int = 20) -> Dict[str, Any]:
        page = max(1, page)
        size = max(1, min(200, size))

        norm_tags = [t.strip().lower() for t in tags if t and t.strip()]

        def match_tags(item: RagItem) -> bool:
            if not norm_tags:
                return True
            item_tags = {t.strip().lower() for t in item.tags}
            if match == "all":
                return all(t in item_tags for t in norm_tags)
            return any(t in item_tags for t in norm_tags)

        items_list: List[Dict[str, Any]] = []
        for it in self._items:
            if not match_tags(it):
                continue
            if q:
                hay = f"{it.name}\n{it.description}\n{' '.join(it.tags)}\n{it.address or ''}"
                if q.lower() not in hay.lower():
                    continue
            items_list.append({
                "id": it.id,
                "name": it.name,
                "description": it.description,
                "tags": it.tags,
                "image_url": it.image_url,
                "address": it.address,
                "lat": it.lat,
                "lng": it.lng,
                "metadata": it.metadata,
                "quantity": it.quantity,
            })

        total = len(items_list)
        start = (page - 1) * size
        end = start + size
        return {"total": total, "page": page, "size": size, "items": items_list[start:end]}


"""
查重方案落盘
### 思路总览
- 在 JSON 中为每个设备持久化五要素哈希，启动时无需重建。
- 运行期在新增/修改/删除时“增量维护”哈希索引，保证 O(1) 查重与一致性。
- 全流程加锁，杜绝并发竞态。

### 字段设计
- 新增到每条设备顶层：
  - dedup_key: 使用五要素规范化后拼接的 sha1
  - dedup_ver: 规范化/拼接规则版本号（CURRENT_DEDUP_VER=1）
- 五要素来源：
  - name
  - parameters: metadata["参数"]
  - test_items: metadata["测试项目"]
  - unit_name: metadata["单位名称"] 或 metadata["单位"]
  - address: 顶层 address 或 metadata["原始地址"]

### 规范化与哈希
- 规范化步骤（建议统一到一个函数 normalize_for_key）：
  - Unicode NFKC 归一
  - 去零宽字符与 NBSP
  - 统一中/英文冒号、括号、破折号等常见标点
  - 去首尾空白、压缩内部空白为单空格、lower
- 拼接：用不可见分隔符（如 \x1f）连接 5 段规范化文本
- 生成：sha1(joined).hex()

### 内存索引
- `self._dup_index: Dict[dedup_key, item_id]`
- 启动加载：
  - 读取 JSON 后：
    - 若条目含 dedup_key 且 dedup_ver==CURRENT_DEDUP_VER → 直接加入 dup_index
    - 否则按新规则生成 key，写回条目并标记需要保存一次
- 运行期维护（加锁）：
  - insert：
    - 计算 new_key（任一要素为空→new_key=None，表示本条无法严格查重）
    - 如果 new_key 且已在 dup_index 且不是本条 → duplicate 命中
    - 否则写入条目，持久化 dedup_key/dedup_ver，并将 new_key→item_id 放入 dup_index
  - update（设备字段变化时必须重新算 key）：
    - 取 old_key=条目原 dedup_key
    - 计算 new_key
    - 若 old_key != new_key：
      - 如果 old_key 存在并映射到本条 → 从 dup_index 移除 old_key
      - 如果 new_key 已被其他 item_id 占用 → 视为 duplicate 冲突（按策略：拒绝更新或合并；建议拒绝并返回 duplicate 标志）
      - 否则将 new_key→item_id 放入 dup_index，并更新条目 dedup_key/dedup_ver
  - delete：
    - 找到条目 old_key，若 dup_index[old_key]==item_id → 从 dup_index 删除
  - clear_all_data：
    - 清空 dup_index

### 服务端接口建议
- 替换“先查重再写入”的链路为“原子 upsert_with_dedup”：
  - 输入：设备完整数据（同 upsert_full）
  - 加锁：计算 new_key → dup_index 命中则返回 duplicate=True、item_id；未命中则新增，返回 created=True
  - 响应新增布尔：created、duplicate（向后兼容保留旧字段）
- check_duplicate：
  - 直接生成 key 后查 `dup_index.get(key)`，O(1)

### 导入器配合
- 依据响应的 created/duplicate 统计，不再把“覆盖更新”算作“新增成功”。
- 同一 Excel 内可本地先去重（可选），减少并发碰撞。

### 日志与可观测性
- 新增事件：
  - dedup.key.created / dedup.key.updated / dedup.key.removed
  - dedup.collision（当更新产生与他条冲突时）
- 打印 key 前 8 位与 item_id，便于排障但不泄露内容。

### 迁移策略
- 首次上线：加载后对缺失 dedup_key 或 ver 不匹配的条目回填计算，统一保存一次。
- 未来规则变更：提升 CURRENT_DEDUP_VER，加载时重算并回写。

### 边界与策略
- 若五要素有任一为空：不生成 dedup_key（条目无法参与“严格查重”），但仍可写入；前端/导入器可提示“该条未参与严格查重”。
- 更新冲突策略：推荐拒绝更新并返回 duplicate 提示，避免“把 A 改成与 B 相同”造成两条数据逻辑重复。

这样做后：
- 启动无需 O(n) 重建（已持久化）；即便重建也只是增量回填一次。
- 查重路径 O(1)，并发链路原子化，行为稳定且可观测。
"""