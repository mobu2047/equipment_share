"""
Ollama 客户端服务

为何需要：
- 对 Ollama HTTP 接口做一层轻封装，便于替换模型或增加重试/超时策略

如何使用：
- from backend.services.ollama_client import OllamaClient
- reply = await OllamaClient().generate("你的问题")
"""

from typing import Any, Dict, Optional

import httpx

from backend.core.config import get_settings
from backend.core.logger import logger


class OllamaClient:
    """轻量封装 Ollama /api/generate 接口。"""

    def __init__(self, *, base_url: Optional[str] = None, model: Optional[str] = None) -> None:
        settings = get_settings()
        self._base_url = base_url or settings.ollama_host
        self._model = model or settings.ollama_model
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=60.0)

    async def generate(self, prompt: str, *, system: Optional[str] = None) -> str:
        """调用 Ollama 文本生成。

        约定：
        - 使用非流式（stream=false）方便后端聚合，前端只需一次响应
        """

        body: Dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            body["system"] = system

        logger.info("ollama.generate.request", extra={"event": "ollama_generate", "model": self._model})
        resp = await self._client.post("/api/generate", json=body)
        resp.raise_for_status()
        data = resp.json()
        reply = data.get("response", "")
        logger.info("ollama.generate.success", extra={"event": "ollama_generate_ok", "chars": len(reply)})
        return reply

    async def aclose(self) -> None:
        await self._client.aclose()


