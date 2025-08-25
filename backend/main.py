"""
FastAPI 入口

说明：
- 提供 API 与静态页面托管
- 集成 CORS（如需跨域可打开）
"""

from pathlib import Path
import sys

from fastapi import FastAPI, Request
from starlette.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import traceback
import uuid

# 允许直接运行此文件：将项目根目录加入 sys.path，确保 `backend.*` 可被解析
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import get_settings
from backend.core.logger import logger
from backend.routers.chat import router as chat_router
from backend.routers.equipment import router as equipment_router
from backend.api import router as rag_router


def create_app() -> FastAPI:
    app = FastAPI(title="Equipment Share API")

    # CORS 可视需求开放
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由
    app.include_router(chat_router)
    app.include_router(equipment_router)
    app.include_router(rag_router)

    # 静态文件
    static_dir = Path(__file__).resolve().parents[1] / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 根路径返回首页
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(static_dir / "index.html"))

    @app.on_event("startup")
    async def _startup() -> None:
        logger.info("startup", extra={"event": "startup"})
        # 确保数据与上传目录存在
        settings = get_settings()
        (Path(settings.data_dir)).mkdir(parents=True, exist_ok=True)
        (Path(settings.uploads_dir)).mkdir(parents=True, exist_ok=True)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        err_id = str(uuid.uuid4())[:8]
        logger.error(
            "unhandled_exception",
            extra={
                "event": "unhandled_exception",
                "error_id": err_id,
                "path": str(request.url),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error", "error_id": err_id, "error": str(exc)})

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        logger.info("shutdown", extra={"event": "shutdown"})

    return app


app = create_app()

if __name__ == "__main__":
    # 便于直接运行：python backend/main.py
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )


