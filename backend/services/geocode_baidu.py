"""
百度地理编码客户端（AK/SK 可选，带回退）

用途：
- 将地址转换为经纬度，用于设备位置信息增强

环境变量（可选）：
- BAIDU_CREDENTIALS：JSON 数组，例如：
  [{"ak":"AK1","sk":"SK1"}, {"ak":"AK2"}]
- 或 BAIDU_AK / BAIDU_SK（单对）
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx


DEFAULT_BAIDU_AKS: List[Tuple[str, Optional[str]]] = [
    ("CSYncH7N4W3TPe6oVGreyESBP9NdyJYQ", "aQVWWSbIol0JlRS9mPyIDBhAdkzOsx29"),
    ("ztdw8fVwy5ATQJuuqAaRM01WPIdGXc1p", None),
]


async def geocode_address(address: str, *, credentials: Optional[List[Tuple[str, Optional[str]]]] = None) -> Dict[str, Optional[float]]:
    if not address:
        return {"lat": None, "lng": None}

    # 构建 AK/SK 列表
    cred_list: List[Tuple[str, Optional[str]]] = []
    if credentials:
        cred_list = [(ak, sk) for ak, sk in credentials if ak]
    else:
        raw = os.getenv("BAIDU_CREDENTIALS", "").strip()
        if raw:
            try:
                arr = json.loads(raw)
                for item in arr:
                    if isinstance(item, dict) and item.get("ak"):
                        cred_list.append((item.get("ak"), item.get("sk")))
            except Exception:
                pass
        ak_env = os.getenv("BAIDU_AK")
        if ak_env and not cred_list:
            cred_list.append((ak_env, os.getenv("BAIDU_SK")))

    if not cred_list:
        cred_list = DEFAULT_BAIDU_AKS[:]

    url_base = "https://api.map.baidu.com"
    path = "/geocoding/v3/"
    async with httpx.AsyncClient(timeout=10.0) as client:
        for ak, sk in cred_list:
            try:
                params = [
                    ("address", address),
                    ("output", "json"),
                    ("ak", ak),
                ]
                query: Dict[str, Any] = dict(params)
                if sk:
                    # 生成 sn
                    from urllib.parse import urlencode, quote_plus
                    query_str = urlencode(params)
                    raw = f"{path}?{query_str}{sk}"
                    sn = __md5(quote_plus(raw))
                    query["sn"] = sn
                resp = await client.get(url_base + path, params=query)
                resp.raise_for_status()
                try:
                    data = resp.json()
                except Exception:
                    data = json.loads(resp.text)
                if isinstance(data, dict) and data.get("status") == 0:
                    if isinstance(data.get("location"), dict):
                        loc = data["location"]
                    else:
                        loc = (data.get("result") or {}).get("location", {})
                    if loc:
                        return {"lat": loc.get("lat"), "lng": loc.get("lng")}
            except Exception:
                continue

    return {"lat": None, "lng": None}


def __md5(text: str) -> str:
    import hashlib
    return hashlib.md5(text.encode("utf-8")).hexdigest()


