"""
简单限流（内存滑动窗口）

用途：
- 对短信发送与验证码验证进行基础防刷；
- 可替换为 Redis/slowapi 实现以满足分布式需求。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class SlidingWindowLimiter:
    """滑动窗口限流器：rate 格式如 "5/min"、"10/sec"。"""

    def __init__(self, rate: str) -> None:
        count, unit = rate.split("/")
        self.limit = int(count)
        if unit == "sec":
            self.window = 1.0
        elif unit == "min":
            self.window = 60.0
        elif unit == "hour":
            self.window = 3600.0
        else:
            raise ValueError("unsupported rate unit")
        self.buckets: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self.buckets[key]
        # 过期出队
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) < self.limit:
            q.append(now)
            return True
        return False



