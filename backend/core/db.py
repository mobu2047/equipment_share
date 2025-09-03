"""
数据库连接与Session管理（MySQL via SQLAlchemy）

为何：
- 集中管理 Engine/Session，便于在不同模块中复用；
- 统一初始化入口，确保首次启动可自动创建表结构（仅用于开发阶段）。

后续演进：
- 生产环境建议使用 Alembic 做迁移，而非依赖 create_all。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from backend.core.config import get_settings


# 全局 Declarative Base（所有 ORM 模型均应继承该 Base）
Base = declarative_base()


def _build_mysql_url() -> str:
    """构造 MySQL 连接 URL。

    说明：
    - 这里固定使用 pymysql 驱动；
    - 用户名/密码中如包含特殊字符，建议后续改为从 DSN 或 .env 中读取已转义的值；
    - 当前配置使用 config 中的固定值，便于快速起步。
    """

    settings = get_settings()
    return (
        f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
        f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_db}?charset=utf8mb4"
    )


def _build_mysql_server_url() -> str:
    """构造不带数据库名的 MySQL 连接 URL，用于首次创建数据库。"""
    settings = get_settings()
    return (
        f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
        f"@{settings.mysql_host}:{settings.mysql_port}/?charset=utf8mb4"
    )


# 创建 Engine（开启 pool_pre_ping 以提高稳定性）
engine = create_engine(
    _build_mysql_url(), echo=False, pool_pre_ping=True, future=True
)


# Session 工厂（禁用自动提交/刷新）
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True, class_=Session)


@contextmanager
def session_scope() -> Iterator[Session]:
    """提供一个事务性 Session 作用域。

    用法：
    with session_scope() as db:
        ...

    设计：
    - 成功时自动提交；异常时回滚并抛出；最终始终关闭连接。
    """

    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：按请求提供一个独立 Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """在开发阶段初始化数据库表结构。

    为什么放在这里：
    - 集中一个入口在应用启动时调用，避免路由层散落初始化逻辑；
    - 仅用于开发，生产应使用 Alembic 迁移。
    """

    # 1) 确保数据库存在
    try:
        server_engine = create_engine(_build_mysql_server_url(), echo=False, pool_pre_ping=True, future=True)
        db_name = get_settings().mysql_db
        with server_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db_name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
            conn.commit()
    except Exception:
        # 如果没有权限创建数据库，后续 create_all 会报错，交由启动日志提示
        pass

    # 2) 延迟导入模型，避免循环引用
    from backend.models import user as _user_models  # noqa: F401
    from backend.models import lease as _lease_models  # noqa: F401
    from backend.models import forum as _forum_models  # noqa: F401

    # 3) 创建表结构
    Base.metadata.create_all(bind=engine)

    # 4) 种子数据：基础角色
    try:
        from backend.models.user import Role
        with SessionLocal() as db:
            for name in ("TENANT", "PROVIDER", "ADMIN"):
                # 使用 query 检查是否存在，存在则跳过
                exists = db.query(Role).filter(Role.name == name).first()
                if not exists:
                    db.add(Role(name=name, description=name.title()))
            db.commit()
    except Exception:
        # 初始化失败不影响服务启动；后续在角色绑定时再补种
        pass

    # 5) 简单迁移：论坛表新增 attachments_json
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE forum_threads ADD COLUMN IF NOT EXISTS attachments_json TEXT"))
            conn.execute(text("ALTER TABLE forum_posts ADD COLUMN IF NOT EXISTS attachments_json TEXT"))
            conn.execute(text("ALTER TABLE forum_posts ADD COLUMN IF NOT EXISTS floor_no INT NOT NULL DEFAULT 0"))
            conn.commit()
    except Exception:
        # MySQL 8.0 不支持 IF NOT EXISTS for ADD COLUMN，回退为探测列是否存在
        try:
            with engine.connect() as conn:
                # 检测 forum_threads.attachments_json
                res = conn.execute(text("SHOW COLUMNS FROM forum_threads LIKE 'attachments_json'"))
                if res.fetchone() is None:
                    conn.execute(text("ALTER TABLE forum_threads ADD COLUMN attachments_json TEXT"))
                res = conn.execute(text("SHOW COLUMNS FROM forum_posts LIKE 'attachments_json'"))
                if res.fetchone() is None:
                    conn.execute(text("ALTER TABLE forum_posts ADD COLUMN attachments_json TEXT"))
                res = conn.execute(text("SHOW COLUMNS FROM forum_posts LIKE 'floor_no'"))
                if res.fetchone() is None:
                    conn.execute(text("ALTER TABLE forum_posts ADD COLUMN floor_no INT NOT NULL DEFAULT 0"))
                conn.commit()
        except Exception:
            pass


