"""
应用配置模块

为何需要：
- 统一集中管理环境变量，便于在不同部署环境（开发/测试/生产）之间切换
- 避免在业务代码中散落硬编码常量

如何使用：
- 从此模块导入 `get_settings()` 获取单例配置对象
"""

from functools import lru_cache
import os
from pydantic import BaseModel, Field


class Settings(BaseModel):
    """应用运行时配置。使用 Pydantic 模型保证类型安全与默认值。

    选择 Pydantic 的原因：
    - 提供清晰的字段校验与默认值
    - 便于未来扩展（如从 .env、Secrets 管理中加载）
    """

    api_host: str = Field(default="0.0.0.0", description="FastAPI 监听地址")
    api_port: int = Field(default=8000, description="FastAPI 端口")

    ollama_host: str = Field(default="http://localhost:11434", description="Ollama 服务地址")
    ollama_model: str = Field(default="llama3.1:8b", description="默认模型名称")
    ollama_embed_model: str = Field(default="bge-m3", description="Embedding 模型名称")

    log_level: str = Field(default="INFO", description="日志等级：DEBUG/INFO/WARNING/ERROR")

    # 数据与上传目录
    data_dir: str = Field(default="data", description="RAG 数据存储目录")
    uploads_dir: str = Field(default="static/uploads", description="图片上传目录（挂载到 /static/uploads）")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局唯一的配置实例。

    使用 lru_cache 保证单例，避免重复构建与多处导入造成的配置不一致。
    注意：不再读取环境变量，直接使用配置文件中的固定值。
    """

    # 直接使用固定配置值，不受环境变量影响
    return Settings(
        api_host="0.0.0.0",
        api_port=8000,
        ollama_host="http://localhost:11434",
        ollama_model="llama3.1:8b",
        ollama_embed_model="bge-m3",
        log_level="INFO",
        data_dir="data",
        uploads_dir="static/uploads",
    )


