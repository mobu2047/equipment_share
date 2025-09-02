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

    # -------------------- 新增：鉴权/数据库/短信配置 --------------------
    # MySQL 连接配置：建议在此处直接填写固定值；如需动态化，后续可接入 .env
    mysql_host: str = Field(default="127.0.0.1", description="MySQL 主机")
    mysql_port: int = Field(default=3306, description="MySQL 端口")
    mysql_user: str = Field(default="root", description="MySQL 用户名")
    mysql_password: str = Field(default="ysy979257", description="MySQL 密码")
    mysql_db: str = Field(default="equipment_share", description="MySQL 数据库名")

    # JWT 与会话
    jwt_secret: str = Field(default="change-me-please", description="JWT HS256 密钥（生产务必更换）")
    access_expires_minutes: int = Field(default=15, description="Access Token 过期分钟数")
    refresh_expires_days: int = Field(default=14, description="Refresh Token 过期天数")

    # 短信（开发模式）
    sms_dev_mode: bool = Field(default=True, description="是否启用短信开发模式")
    sms_dev_code: str = Field(default="000000", description="开发模式固定验证码")

    # 限流（简单字符串表示，后续可扩展解析）
    rate_limit_sms: str = Field(default="5/min", description="短信发送限流")
    rate_limit_verify: str = Field(default="10/min", description="验证码验证限流")

    # 默认管理员（开发用）。填写手机号后，首次登录将自动授予 ADMIN 角色
    admin_default_phone: str = Field(default="000000", description="开发模式默认管理员手机号（留空则不启用）")

    # 论坛上传限制
    forum_upload_max_mb: int = Field(default=10, description="论坛图片单图最大体积(MB)")
    forum_allowed_content_types: list[str] = Field(default_factory=lambda: [
        "image/jpeg", "image/png", "image/webp", "image/gif"
    ], description="允许的图片 MIME 类型")


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
        # DB 默认值（需根据你的环境修改）
        mysql_host="127.0.0.1",
        mysql_port=3306,
        mysql_user="root",
        mysql_password="ysy979257",
        mysql_db="equipment_share",
        # JWT & 会话
        jwt_secret="change-me-please",
        access_expires_minutes=15,
        refresh_expires_days=14,
        # 短信（开发模式）
        sms_dev_mode=True,
        sms_dev_code="000000",
        # 限流
        rate_limit_sms="5/min",
        rate_limit_verify="10/min",
        # 默认管理员手机号（开发）
        admin_default_phone="000000",
        # 论坛上传限制
        forum_upload_max_mb=5,
        forum_allowed_content_types=["image/jpeg", "image/png", "image/webp", "image/gif"],
    )


