"""
简易本地向量存储（RAG Store）

设计目标：
- 纯 Python + 本地文件持久化，便于快速迭代与调试
- 支持增量写入与查询，适合中小规模数据集

实现要点：
- 使用 numpy 计算余弦相似度
- 将条目与向量一并持久化到 JSON 文件（小规模可接受）
"""

import json
import os
import threading
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Set

import numpy as np

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
    vector: List[float]
    # 扩展：地址/坐标/附加元信息
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    quantity: Optional[int] = None


class RagStore:
    """内存 + 文件持久化的简易向量库。"""

    def __init__(self) -> None:
        settings = get_settings()
        # 将数据目录锚定到项目根，避免因工作目录变化导致找不到数据
        project_root = Path(__file__).resolve().parents[2]
        configured = Path(settings.data_dir)
        self._data_dir = configured if configured.is_absolute() else (project_root / configured)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._store_file = self._data_dir / "rag_store.json"
        self._items: List[RagItem] = []
        # tags -> set(ids) 倒排索引，加速多标签过滤
        self._tag_to_ids: Dict[str, Set[str]] = {}
        self._load()
        self._rebuild_index()

    def _load(self) -> None:
        if not self._store_file.exists():
            logger.info("rag_store.init_empty", extra={"event": "rag_store"})
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
                    vector = it.get("vector") or []
                    if not isinstance(vector, list):
                        vector = []
                    address = it.get("address")
                    lat = it.get("lat")
                    lng = it.get("lng")
                    metadata = it.get("metadata") if isinstance(it.get("metadata"), dict) else None
                    item = RagItem(
                        id=it.get("id") or str(uuid.uuid4()),
                        name=name,
                        description=description,
                        tags=tags,
                        image_url=image_url,
                        text=text,
                        vector=vector,
                        address=address,
                        lat=lat,
                        lng=lng,
                        metadata=metadata,
                    )
                    loaded.append(item)
                except Exception:
                    # 单条容错，避免整体加载失败
                    continue
            self._items = loaded
            logger.info("rag_store.loaded", extra={"event": "rag_store_load", "count": len(self._items)})
        except Exception:
            logger.error("rag_store.load_failed", extra={"event": "rag_store_load_error"})

    def _save(self) -> None:
        payload = {"items": [asdict(it) for it in self._items]}
        with _LOCK, self._store_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("rag_store.saved", extra={"event": "rag_store_save", "count": len(self._items)})
        # 保存后重建索引，保证一致
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._tag_to_ids = {}
        for it in self._items:
            for t in it.tags:
                key = t.strip().lower()
                if not key:
                    continue
                self._tag_to_ids.setdefault(key, set()).add(it.id)

    def _index_item(self, item: RagItem) -> None:
        for t in item.tags:
            key = t.strip().lower()
            if not key:
                continue
            self._tag_to_ids.setdefault(key, set()).add(item.id)

    def _unindex_item(self, item: RagItem) -> None:
        for t in item.tags:
            key = t.strip().lower()
            s = self._tag_to_ids.get(key)
            if s and item.id in s:
                s.remove(item.id)

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        # 计算余弦相似度并做 0 除保护
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
        return float(np.dot(a, b) / denom)

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
               metadata: Optional[Dict[str, Any]] = None,
               quantity: Optional[int] = None) -> str:
        """新增或更新条目：用 name+tags 作为简易键去重，如重名则覆盖。"""
        key = (name.strip().lower(), tuple(sorted(t.strip().lower() for t in tags)))
        text = f"{name}\n{description}\nTags: {', '.join(tags)}"

        # 查找是否已有同键条目
        existing_idx = -1
        for idx, it in enumerate(self._items):
            if (it.name.strip().lower(), tuple(sorted(t.strip().lower() for t in it.tags))) == key:
                existing_idx = idx
                break

        item = RagItem(
            id=str(uuid.uuid4()) if existing_idx < 0 else self._items[existing_idx].id,
            name=name,
            description=description,
            tags=tags,
            image_url=image_url,
            text=text,
            vector=vector,
            address=address,
            lat=lat,
            lng=lng,
            metadata=metadata,
            quantity=quantity,
        )

        if existing_idx >= 0:
            # 更新索引：先移除旧条目，再加入新条目
            self._unindex_item(self._items[existing_idx])
            self._items[existing_idx] = item
            self._index_item(item)
        else:
            self._items.append(item)
            self._index_item(item)

        self._save()
        return item.id

    def search(self, *, query_vector: List[float], top_k: int = 3, query_text: str = "") -> List[Dict[str, Any]]:
        if not self._items:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        scored: List[Tuple[float, RagItem]] = []
        for it in self._items:
            # 允许兜底：向量无效时采用关键词相似
            cos = 0.0
            if it.vector:
                v = np.asarray(it.vector, dtype=np.float32)
                if v.ndim == 1 and q.shape == v.shape:
                    cos = self._cosine(q, v)
            cos01 = (cos + 1.0) / 2.0  # -1..1 -> 0..1
            kw = self._kw_score(query_text or "", it.text)
            score = 0.75 * cos01 + 0.25 * kw
            scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, it in scored[: max(1, top_k)]:
            out.append({
                "id": it.id,
                "name": it.name,
                "description": it.description,
                "tags": it.tags,
                "image_url": it.image_url,
                "score": float(s),
            })
        return out

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
            }
            for it in self._items
        ]

    def delete(self, item_id: str) -> bool:
        """按 id 删除条目，返回是否删除成功。"""
        for idx, it in enumerate(self._items):
            if it.id == item_id:
                self._unindex_item(it)
                del self._items[idx]
                self._save()
                return True
        return False

    async def reindex_async(self, embed_async) -> int:
        """使用异步 embed 函数重建全部向量。

        embed_async: Awaitable[str -> List[float]]
        返回：成功重建的条目数量
        """
        updated = 0
        for idx, it in enumerate(self._items):
            try:
                new_vec = await embed_async(it.text)
                self._items[idx].vector = list(new_vec)
                updated += 1
            except Exception:
                continue
        if updated:
            self._save()
        return updated

    # 读取单条
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
                }
        return None

    # 过滤 + 分页
    def list_items_filtered(self, *, tags: List[str], match: str = "any", q: str = "",
                             page: int = 1, size: int = 20) -> Dict[str, Any]:
        page = max(1, page)
        size = max(1, min(200, size))

        candidate_ids: Optional[Set[str]] = None
        norm_tags = [t.strip().lower() for t in tags if t and t.strip()]
        if norm_tags:
            sets = [self._tag_to_ids.get(t, set()) for t in norm_tags]
            if match == "all":
                # 交集
                if sets:
                    s = sets[0].copy()
                    for st in sets[1:]:
                        s &= st
                    candidate_ids = s
            else:
                # 并集
                s: Set[str] = set()
                for st in sets:
                    s |= st
                candidate_ids = s

        # 收集并按简单关键词过滤
        items_list = []
        for it in self._items:
            if candidate_ids is not None and it.id not in candidate_ids:
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


