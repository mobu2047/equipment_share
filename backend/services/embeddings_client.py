"""
Ollama Embeddings 客户端

为何需要：
- 使用本地 Ollama 的 embedding 模型（如 nomic-embed-text）生成向量，用于 RAG 检索

如何使用：
- from backend.services.embeddings_client import EmbeddingsClient
- vec = await EmbeddingsClient().embed("some text")
"""

from typing import List, Optional

import httpx

from backend.core.config import get_settings
from backend.core.logger import logger


class EmbeddingsClient:
    """调用 Ollama /api/embeddings 接口的轻量封装。"""

    def __init__(self, *, base_url: Optional[str] = None, model: Optional[str] = None) -> None:
        settings = get_settings()
        self._base_url = base_url or settings.ollama_host
        self._model = model or settings.ollama_embed_model
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=60.0)

    async def embed(self, text: str) -> List[float]:
        """为输入文本生成 embedding 向量。

        兼容 Ollama 的 embeddings API：
        - 使用字段 `prompt`（不是 `input`）。
        - 响应通常包含 `embedding` 字段。
        """
        body = {"model": self._model, "prompt": text}
        logger.info("embeddings.request", extra={"event": "embed", "model": self._model})
        resp = await self._client.post("/api/embeddings", json=body)
        resp.raise_for_status()
        data = resp.json()
        # 先处理常见的错误字段
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Embedding API error: {data.get('error')}")

        vec = data.get("embedding") or (data.get("data", [{}])[0].get("embedding") if isinstance(data.get("data"), list) and data.get("data") else None)
        if not vec:
            raise RuntimeError("Embedding response malformed")
        logger.info("embeddings.success", extra={"event": "embed_ok", "dim": len(vec)})
        return vec

    async def aclose(self) -> None:
        await self._client.aclose()


