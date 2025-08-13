"""
结构化日志模块（类 Winston 风格）

为何需要：
- 统一日志格式，方便在控制台或集中式日志系统中检索与聚合
- 提供多级别日志接口：debug/info/warning/error

如何使用：
- from backend.core.logger import logger
- logger.info("message", extra={"event": "startup"})
"""

import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict


class JsonFormatter(logging.Formatter):
    """将日志记录格式化为 JSON 文本，便于机器检索。

    设计思路：
    - 统一时间戳为 ISO8601，保留毫秒
    - 保留 logger 名称、级别、消息与可选的上下文字段
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 合并 extra 字段
        for attr in ("extra",):
            value = getattr(record, attr, None)
            if isinstance(value, dict):
                payload.update(value)

        # 异常堆栈
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("equipment_share")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    # 避免向上冒泡到 root 造成重复打印
    logger.propagate = False
    return logger


# 导出的全局 logger
logger = _build_logger()


