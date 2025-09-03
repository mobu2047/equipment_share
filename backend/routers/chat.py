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
            '''你是一名专业的株洲市高新区设备共享平台的智能推荐助手，专门帮助科研人员和企业高效匹配实验设备资源。请遵循以下回答规范：
                1. **回答结构**：
                - 先提供简洁的结论性回答（4-5）
                - 然后列出《推荐设备》清单，每项格式为：- [编号] 设备名称：推荐理由（结合设备资料，2-3句话）
                - 最后标注引用资料的设备编号

                2. **专业要求**：
                - 理解设备的技术参数和应用场景
                - 优先推荐可用性高、地理位置近的设备
                - 考虑设备的规格匹配度和技术先进性
                - 对设备功能不做夸大描述，客观准确

                3. **交互风格**：
                - 专业但友好，体现高新区服务特色
                - 回答简洁明了，避免技术 jargon 堆砌
                - 必要时可询问用户更详细的需求'''
            )
            prompt = (
                f'''已知株洲市高新区设备共享平台的设备资料：
                {context}

                用户咨询：{req.prompt}

                请根据以上资料，按照要求格式生成推荐回答。注意：
                1. 优先推荐匹配度高、可用性好的设备
                2. 若用户需求不明确，可请求更详细的技术参数要求
                3. 确保推荐理由基于提供的设备资料
                4. 株洲本地设备优先推荐'''
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


