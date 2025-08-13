"""
对话相关路由

职责划分：
- 接收前端对话请求 -> 调用 Ollama -> 返回回复
"""

from fastapi import APIRouter, HTTPException

from backend.models.schemas import ChatRequest, ChatResponse
from backend.services.ollama_client import OllamaClient
from backend.core.logger import logger


router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    # 记录关键流程，便于排障与统计
    logger.info("api.chat.request", extra={"event": "chat", "len": len(req.prompt)})
    try:
        client = OllamaClient()
        reply = await client.generate(req.prompt)
        await client.aclose()
    except Exception as e:
        logger.error("api.chat.error", extra={"event": "chat_error"})
        raise HTTPException(status_code=500, detail=str(e))

    return ChatResponse(reply=reply)


