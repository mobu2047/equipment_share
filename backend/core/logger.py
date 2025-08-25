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

        # 合并通过 logging.extra 注入的自定义字段
        # 说明：Python logging 会将 extra 的键直接并入 record.__dict__，本格式化器将其挑出合并到 JSON。
        default_keys = {
            'name','msg','args','levelname','levelno','pathname','filename','module','exc_info','exc_text',
            'stack_info','lineno','funcName','created','msecs','relativeCreated','thread','threadName',
            'processName','process','message','asctime'
        }
        for key, value in record.__dict__.items():
            if key not in default_keys and not key.startswith('_'):
                try:
                    payload[key] = value
                except Exception:
                    # 某些不可序列化对象直接跳过
                    continue

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


