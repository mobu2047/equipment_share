"""
统一的后端 API 客户端（供脚本/服务内部复用）

为何需要：
- 将所有 HTTP 调用集中在一个文件中，后续新增/修改/删除 API 统一维护
- 提供一致的错误处理与超时控制，减少重复代码

用法：
    from api import EquipmentShareAPI
    api = EquipmentShareAPI(base_url="http://localhost:8000")
    api.upsert_full(name="电子显微镜", tags=["sem"], image_url="/static/uploads/a.png")

约定：
- 返回尽量为后端 JSON（dict），异常抛出 RuntimeError（附带响应体截断）
- 后续新 API 直接在此文件内新增方法
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import os
import requests


class EquipmentShareAPI:
    """后端 API 客户端。

    设计考虑：
    - base_url、timeout、api_key 可通过环境变量覆盖，方便在 CI/生产环境配置。
    - 统一 _handle 响应，抛出包含状态码与 body 片段的 RuntimeError 以便排障。
    """

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0, api_key: Optional[str] = None) -> None:
        self.base_url = (base_url or os.getenv("EQUIPMENT_SHARE_API", "http://localhost:8000")).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        token = api_key or os.getenv("EQUIPMENT_SHARE_API_KEY")
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})

    # ----------------------------- 内部工具 -----------------------------
    def _handle(self, resp: requests.Response) -> Any:
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            body = resp.text
            body_preview = body[:500] + ("..." if len(body) > 500 else "")
            raise RuntimeError(f"HTTP {resp.status_code} {resp.request.method} {resp.request.url} -> {body_preview}") from e
        ctype = resp.headers.get("content-type", "")
        return resp.json() if "application/json" in ctype else resp.text

    # ----------------------------- RAG：设备 CRUD -----------------------------
    def upsert_full(
        self,
        *,
        name: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        image_url: Optional[str] = None,
        address: Optional[str] = None,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        quantity: Optional[int] = None,
    ) -> Dict[str, Any]:
        payload = {
            "name": name,
            "description": description or "",
            "tags": tags or [],
            "image_url": image_url,
            "address": address,
            "lat": lat,
            "lng": lng,
            "metadata": metadata,
            "quantity": quantity,
        }
        r = self.session.post(f"{self.base_url}/api/rag/upsert_full", json=payload, timeout=self.timeout)
        return self._handle(r)

    def upsert_file(
        self,
        *,
        name: str,
        description: str = "",
        tags_csv: str = "",
        image_path: Optional[str] = None,
        address: Optional[str] = None,
        quantity: Optional[int] = None,
    ) -> Dict[str, Any]:
        data = {
            "name": name,
            "description": description,
            "tags": tags_csv,
            "address": address or "",
        }
        if quantity is not None:
            data["quantity"] = str(quantity)
        files = {}
        if image_path:
            files["image"] = (os.path.basename(image_path), open(image_path, "rb"), "application/octet-stream")
        r = self.session.post(f"{self.base_url}/api/rag/upsert", data=data, files=files or None, timeout=self.timeout)
        return self._handle(r)

    def get_item(self, item_id: str) -> Dict[str, Any]:
        r = self.session.get(f"{self.base_url}/api/rag/item/{item_id}", timeout=self.timeout)
        return self._handle(r)

    def update_item(self, item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = self.session.put(f"{self.base_url}/api/rag/item/{item_id}", json=payload, timeout=self.timeout)
        return self._handle(r)

    def delete_item(self, item_id: str) -> Dict[str, Any]:
        r = self.session.delete(f"{self.base_url}/api/rag/delete/{item_id}", timeout=self.timeout)
        return self._handle(r)

    def list_items(self, *, tags: Optional[List[str]] = None, match: str = "any", q: str = "", page: int = 1, size: int = 20) -> Dict[str, Any]:
        params = {"tags": ",".join(tags or []), "match": match, "q": q, "page": page, "size": size}
        r = self.session.get(f"{self.base_url}/api/rag/items", params=params, timeout=self.timeout)
        return self._handle(r)

    # ----------------------------- RAG：检索/问答 -----------------------------
    def search(self, *, query: str, top_k: int = 3) -> Dict[str, Any]:
        r = self.session.get(f"{self.base_url}/api/rag/search", params={"query": query, "top_k": top_k}, timeout=self.timeout)
        return self._handle(r)

    def ask(self, *, query: str, top_k: int = 3) -> Dict[str, Any]:
        r = self.session.post(f"{self.base_url}/api/rag/ask", params={"query": query, "top_k": top_k}, timeout=self.timeout)
        return self._handle(r)

    # ----------------------------- 基础对话/推荐（兜底） -----------------------------
    def chat(self, *, prompt: str) -> Dict[str, Any]:
        r = self.session.post(f"{self.base_url}/api/chat", json={"prompt": prompt}, timeout=self.timeout)
        return self._handle(r)

    def recommend(self, *, experiment: str) -> Dict[str, Any]:
        r = self.session.post(f"{self.base_url}/api/recommend", json={"experiment": experiment}, timeout=self.timeout)
        return self._handle(r)


