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

from backend.core.config import get_settings
from backend.core.logger import logger


_LOCK = threading.Lock()


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
        
        self._load()
        self._init_index()

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
                    item = RagItem(
                        id=item_id,
                        name=name,
                        description=description,
                        tags=tags,
                        image_url=image_url,
                        text=text,
                        faiss_id=faiss_id,
                    )
                    loaded.append(item)
                except Exception:
                    # 单条容错，避免整体加载失败
                    continue
            self._items = loaded
            logger.info("rag_store_faiss.loaded", extra={"event": "rag_store_faiss_load", "count": len(self._items)})
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
            payload_items.append(obj)
        payload = {"items": payload_items}
        with _LOCK, self._store_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("rag_store_faiss.saved", extra={"event": "rag_store_faiss_save", "count": len(self._items)})

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

    def upsert(self, *, name: str, description: str, tags: List[str], image_url: str, vector: List[float]) -> str:
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
        item = RagItem(
            id=item_id,
            name=name,
            description=description,
            tags=tags,
            image_url=image_url,
            text=text,
            faiss_id=faiss_id,
        )

        if existing_idx >= 0:
            # 更新现有条目
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
                "score": content_score,
                "content_score": content_score,
            }
            
            # 添加位置信息和距离计算
            if hasattr(item, 'location') and item.location:
                # 从JSON加载的数据，location作为字典属性
                location_data = getattr(item, 'location', None)
                if not location_data:
                    # 尝试从内存数据中查找location
                    for mem_item in self._items:
                        if mem_item.id == item.id:
                            # 检查是否有location属性（从JSON加载的额外数据）
                            raw_data = self._get_raw_item_data(item.id)
                            if raw_data and 'location' in raw_data:
                                location_data = raw_data['location']
                            break
                
                if location_data and user_location:
                    device_lat = location_data.get('lat', 0)
                    device_lng = location_data.get('lng', 0)
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
                            "location": location_data,
                            "distance_weight": distance_weight
                        })
                        results.append(result_item)
                else:
                    # 没有位置信息的设备
                    results.append(result_item)
            else:
                # 没有位置信息的设备
                results.append(result_item)

        # 按最终得分重新排序，确保返回顺序正确
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
    
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
