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
from typing import Any, Dict, List, Tuple

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


class RagStore:
    """内存 + 文件持久化的简易向量库。"""

    def __init__(self) -> None:
        settings = get_settings()
        self._data_dir = Path(settings.data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._store_file = self._data_dir / "rag_store.json"
        self._items: List[RagItem] = []
        self._load()

    def _load(self) -> None:
        if not self._store_file.exists():
            logger.info("rag_store.init_empty", extra={"event": "rag_store"})
            return
        try:
            with _LOCK, self._store_file.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            self._items = [RagItem(**it) for it in raw.get("items", [])]
            logger.info("rag_store.loaded", extra={"event": "rag_store_load", "count": len(self._items)})
        except Exception:
            logger.error("rag_store.load_failed", extra={"event": "rag_store_load_error"})

    def _save(self) -> None:
        payload = {"items": [asdict(it) for it in self._items]}
        with _LOCK, self._store_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("rag_store.saved", extra={"event": "rag_store_save", "count": len(self._items)})

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        # 计算余弦相似度并做 0 除保护
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
        return float(np.dot(a, b) / denom)

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

        item = RagItem(
            id=str(uuid.uuid4()) if existing_idx < 0 else self._items[existing_idx].id,
            name=name,
            description=description,
            tags=tags,
            image_url=image_url,
            text=text,
            vector=vector,
        )

        if existing_idx >= 0:
            self._items[existing_idx] = item
        else:
            self._items.append(item)

        self._save()
        return item.id

    def search(self, *, query_vector: List[float], top_k: int = 3) -> List[Dict[str, Any]]:
        if not self._items:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        scored: List[Tuple[float, RagItem]] = []
        for it in self._items:
            v = np.asarray(it.vector, dtype=np.float32)
            s = self._cosine(q, v)
            scored.append((s, it))
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
            }
            for it in self._items
        ]

    def delete(self, item_id: str) -> bool:
        """按 id 删除条目，返回是否删除成功。"""
        for idx, it in enumerate(self._items):
            if it.id == item_id:
                del self._items[idx]
                self._save()
                return True
        return False


