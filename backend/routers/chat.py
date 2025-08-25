"""
对话相关路由

职责划分：
- 接收前端对话请求 -> 调用 Ollama -> 返回回复
"""

from fastapi import APIRouter, HTTPException

from backend.models.schemas import ChatRequest, ChatResponse
from backend.services.ollama_client import OllamaClient
from backend.services.embeddings_client import EmbeddingsClient
from backend.services.rag_store_faiss import RagStoreFaiss
from backend.core.logger import logger


router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    # 记录关键流程，便于排障与统计
    logger.info("api.chat.request", extra={"event": "chat", "len": len(req.prompt)})
    # 优先尝试 RAG：若命中则带上下文回答
    try:
        emb = EmbeddingsClient()
        qvec = await emb.embed(req.prompt)
        await emb.aclose()

        store = RagStoreFaiss()
        docs = store.search(query_vector=qvec, top_k=3, query_text=req.prompt)
        if docs:
            context = "\n\n".join([
                f"[{i+1}] 名称: {d['name']}\n描述: {d['description']}\n标签: {', '.join(d['tags'])}"
                for i, d in enumerate(docs)
            ])
            system = (
                "你是实验设备共享平台的助手。要求：先给简短结论；"
                "随后输出《推荐设备》列表：- [编号] 名称：1句理由；最后给引用编号。"
            )
            prompt = (
                "已知设备资料：\n" + context + "\n\n"
                + "用户问题：" + req.prompt + "\n请按要求生成答案。"
            )
            try:
                llm = OllamaClient()
                reply = await llm.generate(prompt, system=system)
                await llm.aclose()
                return ChatResponse(reply=reply)
            except Exception:
                # 如果 LLM 调用失败，返回一个包含推荐列表的简要文本，避免 500
                rec_lines = [f"- [{i+1}] {d['name']}" for i, d in enumerate(docs)]
                return ChatResponse(reply="推荐设备:\n" + "\n".join(rec_lines))
    except Exception:
        # RAG 失败时静默回退为普通对话
        pass

    # 回退：普通对话
    try:
        client = OllamaClient()
        reply = await client.generate(req.prompt)
        await client.aclose()
    except Exception as e:
        logger.error("api.chat.error", extra={"event": "chat_error"})
        raise HTTPException(status_code=500, detail=str(e))

    return ChatResponse(reply=reply)


