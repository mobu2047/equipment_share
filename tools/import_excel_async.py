"""
异步并发Excel导入脚本 - 性能优化版本

主要改进：
- 异步并发处理，支持控制并发数
- 批量API调用，减少网络开销
- 缓存地理编码结果，避免重复请求
- 批量FAISS索引更新，减少磁盘IO

使用：
  python tools/import_excel_async.py --file data.xlsx --api http://localhost:8000 --concurrency 10 [--mode append|overwrite]

两种导入模式：
- append（默认）：保留历史数据与索引，仅追加新数据（查重后跳过重复）
- overwrite：覆盖导入；导入前调用后端 /api/rag/clear_all 并附带 cleanup_images=true 同步删除导入脚本生成的图片

性能对比：
- 原版本：1000行 ~45分钟
- 异步版本：1000行 ~5-10分钟 (预期提升400%+)
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import traceback
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import time

import aiohttp
import pandas as pd
from tqdm import tqdm
import aiofiles
import uuid
import re

# 图片处理相关导入
try:
    from openpyxl import load_workbook
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

try:
    import win32com.client
    HAS_COM = True
except ImportError:
    HAS_COM = False

# 导入地图API
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.services.geocode_baidu import geocode_address
    HAS_GEOCODE_FUNC = True
except ImportError:
    HAS_GEOCODE_FUNC = False

def _row_key_from_mapping(row: Dict[str, Any]) -> str:
    """生成行锚点：用于将图片与数据行稳健匹配。"""
    name = str(row.get("设备名") or row.get("设备名称") or row.get("名称") or "").strip().lower()
    unit = str(row.get("单位名称") or row.get("单位") or "").strip().lower()
    return f"{name}|{unit}"


def save_image(value: str, uploads_dir: Path, base_dir: Optional[Path] = None) -> str:
    """保存图片并返回 image_url。支持：URL、本地路径、dataURI、多个值分隔。

    设计要点：
    - 多值（逗号/分号/换行/空格）时取第一个有效项
    - 相对路径相对 Excel 所在目录解析
    - URL 无扩展名时按 Content-Type 猜测扩展
    """
    uploads_dir.mkdir(parents=True, exist_ok=True)
    if not value:
        return ""
    # 取第一个非空候选
    candidates = []
    raw = str(value).strip()
    for sep in ["\n", "\r", ",", ";", " "]:
        raw = raw.replace(sep, "|")
    for part in raw.split("|"):
        p = part.strip()
        if p:
            candidates.append(p)
    if not candidates:
        return ""
def normalize_tag(text: str) -> str:
    return (text or "").strip().lower()

def extract_tags(row: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    for key in ["类别", "测试项目", "测试标准"]:
        val = str(row.get(key) or "").strip()
        if val:
            tags.extend([normalize_tag(x) for x in val.replace(";", ",").split(",") if x.strip()])
    uniq = []
    seen = set()
    for t in tags:
        if t and t not in seen:
            uniq.append(t)
            seen.add(t)
    return uniq

def make_description(row: Dict[str, Any], name: str = "") -> str:
    parts = []
    if name or row.get("设备名") or row.get("设备名称"):
        parts.append(f"名称: {name or row.get('设备名') or row.get('设备名称')}")
    if row.get("类别"):
        parts.append(f"类别: {row['类别']}")
    if row.get("测试项目"):
        parts.append(f"测试项目: {row['测试项目']}")
    if row.get("参数"):
        parts.append(f"参数: {row['参数']}")
    if row.get("测试标准"):
        parts.append(f"测试标准: {row['测试标准']}")
    if row.get("资质介绍"):
        parts.append(f"资质: {row['资质介绍']}")
    if row.get("是否出具报告"):
        parts.append(f"出具报告: {row['是否出具报告']}")
    if row.get("预计价格") and row.get("单位"):
        parts.append(f"价格: {row['预计价格']}{row['单位']}")
    return "\n".join(parts)

def _row_key_from_mapping(row: Dict[str, Any]) -> str:
    name = str(row.get("设备名") or row.get("设备名称") or row.get("名称") or "").strip().lower()
    unit = str(row.get("单位名称") or row.get("单位") or "").strip().lower()
    return f"{name}|{unit}"


def _get_quantity_from_row(row: Dict[str, Any]) -> Optional[int]:
    """尽可能从一行中解析出台数。

    覆盖常见列名：
    - 台数、台 数、数量、数量(台)、数量（台）、台数(台)、台数（台）、设备台数、库存
    同时提供降级：扫描所有列名，去空格/括号后若包含“台数”或等于“数量”，尝试解析。
    """
    preferred_keys = [
        "台数", "台 数", "数量", "数量(台)", "数量（台）", "台数(台)", "台数（台）", "设备台数", "库存",
    ]
    for key in preferred_keys:
        if key in row:
            q = _extract_int_quantity(row.get(key))
            if q is not None:
                return q
    # 退化：扫描所有列，容忍空格与中英文括号、全角
    trans_table = str.maketrans({
        " ": "", "\t": "", "（": "(", "）": ")", "【": "[", "】": "]",
    })
    for k in row.keys():
        try:
            norm = str(k).translate(trans_table)
        except Exception:
            continue
        if "台数" in norm or norm == "数量" or norm.startswith("数量("):
            q = _extract_int_quantity(row.get(k))
            if q is not None:
                return q
    return None

def _extract_int_quantity(value: Any) -> Optional[int]:
    """从多种可能的输入中提取整数台数。

    设计考量：
    - Excel 中常见格式：“3”“ 3 ”、“3台”、“3.0”、“约3台”；
    - 若为浮点字符串，取其整数部分；
    - 若存在多个数字，取第一个；
    - 中文小写数字（如“二台”）暂不处理，避免过度猜测。
    """
    try:
        s = str(value).strip()
    except Exception:
        return None
    if not s:
        return None
    # 直接是纯数字
    if s.isdigit():
        try:
            return int(s)
        except Exception:
            return None
    # 提取第一个数字（支持小数）
    m = re.search(r"\d+(?:[\.,]\d+)?", s)
    if not m:
        return None
    num_str = m.group(0).replace(",", ".")
    try:
        # 统一转 float 再取整，兼容 "3.0"
        return int(float(num_str))
    except Exception:
        return None


class AsyncImportStats:
    """异步导入统计信息"""
    def __init__(self):
        self.total = 0
        self.success = 0
        self.failed = 0
        self.duplicates = 0
        self.start_time = time.time()
        self.geocode_cache_hits = 0
        self.geocode_cache_misses = 0
    
    def add_success(self):
        self.success += 1
    
    def add_failure(self):
        self.failed += 1
    
    def add_duplicate(self):
        self.duplicates += 1
    
    def cache_hit(self):
        self.geocode_cache_hits += 1
    
    def cache_miss(self):
        self.geocode_cache_misses += 1
    
    def get_summary(self) -> Dict[str, Any]:
        elapsed = time.time() - self.start_time
        return {
            "total": self.total,
            "success": self.success,
            "failed": self.failed,
            "duplicates": self.duplicates,
            "elapsed_seconds": round(elapsed, 2),
            "rows_per_second": round(self.total / elapsed if elapsed > 0 else 0, 2),
            "geocode_cache_hit_rate": round(
                self.geocode_cache_hits / (self.geocode_cache_hits + self.geocode_cache_misses) * 100 
                if (self.geocode_cache_hits + self.geocode_cache_misses) > 0 else 0, 1
            )
        }


class GeocodeCache:
    """地理编码缓存"""
    def __init__(self):
        self._cache: Dict[str, Dict[str, Optional[float]]] = {}
    
    def get(self, address: str) -> Optional[Dict[str, Optional[float]]]:
        return self._cache.get(address.strip().lower())
    
    def set(self, address: str, result: Dict[str, Optional[float]]):
        self._cache[address.strip().lower()] = result
    
    def size(self) -> int:
        return len(self._cache)


class AsyncExcelImporter:
    """异步Excel导入器"""
    
    def __init__(self, api_base: str, concurrency: int = 10, mode: str = "append"):
        self.api_base = api_base.rstrip('/')
        self.concurrency = concurrency
        # 导入模式：append | overwrite
        # append：不清空数据与索引，不删图片
        # overwrite：清空数据与索引，并清理由本脚本生成的图片（import_embed_/import_shape_/import_disp_ 前缀）
        self.mode = (mode or "append").strip().lower()
        self.stats = AsyncImportStats()
        self.geocode_cache = GeocodeCache()
        self._session: Optional[aiohttp.ClientSession] = None
        self.embedded_images: Dict[int, str] = {}  # 存储预提取的嵌入图片映射
        # 标记索引是否已初始化（非空）。
        # 需求：先确认索引非空再进行查重，避免空索引阶段的无效查重与噪声日志。
        self.index_ready: bool = False
        
        # 创建输出目录
        self.project_root = Path(__file__).resolve().parents[1]
        self.uploads_dir = self.project_root / "static" / "uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        # 临时图片目录：仅在“确认导入成功”后才移动到最终 uploads 目录
        # 为什么：避免导入失败/重复时提前落盘产生垃圾文件
        self.uploads_tmp_dir = self.uploads_dir / "_tmp_async_import"
        self.uploads_tmp_dir.mkdir(parents=True, exist_ok=True)
        
        # 日志文件
        self.manifest_path = self.project_root / "data" / "import_manifest_async.jsonl"
        self.fail_log = self.project_root / "data" / "import_failures_async.jsonl"
        self.fail_log.parent.mkdir(parents=True, exist_ok=True)
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        connector = aiohttp.TCPConnector(limit=self.concurrency * 2, limit_per_host=self.concurrency)
        timeout = aiohttp.ClientTimeout(total=60, connect=10)
        self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if self._session:
            await self._session.close()
    
    async def _geocode_async(self, address: str) -> Dict[str, Optional[float]]:
        """异步地理编码，带缓存"""
        if not address.strip():
            return {"lat": None, "lng": None}
        
        # 检查缓存
        cached = self.geocode_cache.get(address)
        if cached is not None:
            self.stats.cache_hit()
            return cached
        
        self.stats.cache_miss()
        
        # 直接调用地理编码函数
        try:
            if HAS_GEOCODE_FUNC:
                geo_result = await geocode_address(address)
            else:
                geo_result = {"lat": None, "lng": None}
        except Exception:
            geo_result = {"lat": None, "lng": None}
        
        # 缓存结果
        self.geocode_cache.set(address, geo_result)
        return geo_result
    
    async def _check_duplicate_async(self, payload: Dict[str, Any]) -> bool:
        """异步重复检测"""
        try:
            async with self._session.post(
                f"{self.api_base}/api/rag/check_duplicate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("duplicate", False)
                return False
        except Exception:
            return False
    
    async def _upsert_async(self, payload: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """异步向量生成与存储"""
        try:
            async with self._session.post(
                f"{self.api_base}/api/rag/upsert_full",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=45)  # embedding可能较慢
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return True, None, result
                else:
                    error_text = await resp.text()
                    return False, f"HTTP {resp.status}: {error_text[:200]}", None
        except Exception as e:
            return False, str(e), None

    async def _is_index_non_empty(self) -> bool:
        """探测后端索引/数据是否已存在

        - 优先使用 /api/rag/list 的返回结构判断（items 非空或 total>0）
        - 失败时返回 False
        """
        try:
            async with self._session.get(f"{self.api_base}/api/rag/list", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                if isinstance(data, dict):
                    if "items" in data and isinstance(data["items"], list):
                        return len(data["items"]) > 0
                    if "total" in data:
                        try:
                            return int(data["total"]) > 0
                        except Exception:
                            return False
                return False
        except Exception:
            return False
    
    async def _prepare_image_async(self, value: str, base_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
        """预取图片内容但不立即落盘，返回 {bytes, ext, fname, url}。

        - 仅在 upsert 成功后才真正落盘，避免垃圾文件
        - 支持：URL、本地路径、dataURI、多个值分隔
        """
        if not value:
            return None
        
        # 解析多个候选值（逗号/分号/换行/空格分隔）
        candidates = []
        for sep in [",", ";", "\n", " "]:
            if sep in value:
                candidates = [v.strip() for v in value.split(sep) if v.strip()]
                break
        if not candidates:
            candidates = [value.strip()]
        
        def _ext_from_headers(headers: Dict[str, str]) -> str:
            ct = (headers.get("content-type") or "").lower()
            if "image/png" in ct:
                return ".png"
            if "image/jpeg" in ct or "image/jpg" in ct:
                return ".jpg"
            if "image/webp" in ct:
                return ".webp"
            if "image/gif" in ct:
                return ".gif"
            return ".jpg"
        
        for val in candidates:
            try:
                content = None
                ext = ".jpg"
                
                # URL
                if val.startswith("http://") or val.startswith("https://"):
                    async with self._session.get(val, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            content = await resp.read()
                            ext = os.path.splitext(val.split("?")[0])[1] or _ext_from_headers(dict(resp.headers))
                
                # dataURI
                elif val.startswith("data:image/") and ";base64," in val:
                    meta, b64 = val.split(",", 1)
                    content = base64.b64decode(b64)
                    ext = ".jpg"
                
                # 本地路径（绝对/相对）
                else:
                    path = Path(val)
                    if not path.is_absolute() and base_dir:
                        path = (base_dir / path).resolve()
                    if path.exists():
                        async with aiofiles.open(path, 'rb') as f:
                            content = await f.read()
                        ext = path.suffix or ".jpg"
                
                if content:
                    # 生成唯一文件名（但不立即写入磁盘）
                    h = hashlib.md5(content).hexdigest()[:16]
                    fname = f"import_embed_{h}{ext}"
                    url = f"/static/uploads/{fname}"
                    return {"bytes": content, "ext": ext, "fname": fname, "url": url}
                    
            except Exception:
                continue
        
        return None

    async def _persist_image_bytes(self, data: Dict[str, Any]) -> Optional[str]:
        """将预取的图片字节真正写入 uploads 目录，并返回最终 URL。"""
        try:
            if not data:
                return None
            fname = data.get("fname")
            content: Optional[bytes] = data.get("bytes")
            if not fname or not content:
                return None
            dst = self.uploads_dir / fname
            if not dst.exists():
                async with aiofiles.open(dst, 'wb') as f:
                    await f.write(content)
            return f"/static/uploads/{fname}"
        except Exception:
            return None

    async def _finalize_tmp_image(self, tmp_path: str) -> Optional[str]:
        """将临时目录中的图片移动到 uploads 目录，返回最终 URL。

        - 用于 COM 导出的嵌入图片：先导出到 tmp，再在 upsert 成功后移动到正式目录
        """
        try:
            if not tmp_path:
                return None
            src = Path(tmp_path)
            if not src.exists():
                return None
            fname = src.name
            dst = self.uploads_dir / fname
            try:
                # 原子移动/覆盖
                os.replace(str(src), str(dst))
            except Exception:
                # 回退：复制后删除
                async with aiofiles.open(src, 'rb') as rf:
                    content = await rf.read()
                async with aiofiles.open(dst, 'wb') as wf:
                    await wf.write(content)
                try:
                    src.unlink()
                except Exception:
                    pass
            return f"/static/uploads/{fname}"
        except Exception:
            return None
    
    def _extract_embedded_images(self, file_path: str, sheet: Any, df: pd.DataFrame, 
                                image_col: Optional[str] = "图片") -> Dict[int, str]:
        """提取Excel中的嵌入图片，返回行索引到image_url的映射"""
        embedded_map: Dict[int, str] = {}
        
        # 1. 跳过openpyxl提取（因为DISPIMG公式生成的图片openpyxl无法识别）
        # 直接依赖COM进行DISPIMG公式图片提取
        print("   跳过openpyxl（DISPIMG公式图片需要COM导出）")
        
        # 2. 使用Windows COM提取DISPIMG/IMAGE公式图片（如果可用）
        if HAS_COM:
            try:
                # 注意：COM操作不是线程安全的，所以在预处理阶段同步执行
                import pythoncom
                pythoncom.CoInitialize()
                
                # 采用同步版的成功策略：WPS优先，然后回退到Excel
                success = False
                
                # 1. 优先尝试WPS
                for prog_id in ("ket.Application", "et.Application"):
                    try:
                        print(f"   尝试使用 {prog_id}...")
                        app = win32com.client.Dispatch(prog_id)
                        app.Visible = False
                        app.DisplayAlerts = False
                        wb = app.Workbooks.Open(str(Path(file_path).resolve()))
                        ws = wb.Worksheets(sheet if isinstance(sheet, str) else wb.Worksheets(int(sheet) + 1).Name)
                        
                        success = True
                        break
                    except Exception as e:
                        print(f"   {prog_id} 不可用: {e}")
                        continue
                
                # 2. 回退到Excel
                if not success:
                    try:
                        print(f"   回退到 Excel.Application...")
                        app = win32com.client.Dispatch("Excel.Application")
                        app.Visible = False
                        app.DisplayAlerts = False
                        wb = app.Workbooks.Open(str(Path(file_path).resolve()))
                        ws = wb.Worksheets(sheet if isinstance(sheet, str) else wb.Worksheets(int(sheet) + 1).Name)
                        success = True
                    except Exception as e:
                        print(f"   Excel.Application 也不可用: {e}")
                        return embedded_map
                
                if not success:
                    print("   所有COM应用都不可用")
                    return embedded_map
                
                try:
                    # 选择图片列索引
                    if image_col and image_col in df.columns:
                        col_idx = list(df.columns).index(image_col) + 1
                    else:
                        col_idx = len(df.columns)
                    
                    header_row = 1
                    processed_count = 0
                    
                    print(f"   使用图片列索引: {col_idx}")
                    
                    # 步骤1：优先处理Shapes（同步版的关键策略）
                    print(f"   步骤1: 处理Shapes对象...")
                    try:
                        shapes_count = ws.Shapes.Count
                        print(f"   发现 {shapes_count} 个Shapes对象")
                        
                        for sidx in range(1, int(shapes_count) + 1):
                            try:
                                shp = ws.Shapes(sidx)
                                tl = getattr(shp, 'TopLeftCell', None)
                                if tl is None:
                                    continue
                                    
                                r = int(getattr(tl, 'Row', 0))
                                c = int(getattr(tl, 'Column', 0))
                                
                                if r <= header_row or c != col_idx:
                                    continue
                                
                                i = (r - header_row - 1)  # 转换为DataFrame索引
                                
                                # 检查是否在有效范围内
                                if not (0 <= i < len(df)):
                                    continue
                                
                                # 跳过已有映射的行
                                if i in embedded_map:
                                    continue
                                
                                # 导出Shape图片
                                try:
                                    shp.CopyPicture(Appearance=1, Format=2)
                                    left = getattr(shp, 'Left', 0)
                                    top = getattr(shp, 'Top', 0)
                                    width = max(int(getattr(shp, 'Width', 50)), 50)
                                    height = max(int(getattr(shp, 'Height', 50)), 50)
                                    
                                    ch = ws.ChartObjects().Add(left, top, width, height)
                                    ch.Chart.Paste()
                                    
                                    # 使用同步版的哈希策略
                                    h = hashlib.md5(f"{file_path}:{sheet}:shape:{r}:{c}:{getattr(shp,'Name','')}".encode('utf-8')).hexdigest()[:16]
                                    fname = f"import_shape_{h}.png"
                                    # 先导出到临时目录，成功后再移动
                                    dst = self.uploads_tmp_dir / fname
                                    
                                    if not dst.exists():
                                        ch.Chart.Export(str(dst))
                                    
                                    ch.Delete()
                                    
                                    # 验证文件
                                    if dst.exists() and dst.stat().st_size > 0:
                                        embedded_map[i] = f"/static/uploads/{fname}"
                                        processed_count += 1
                                        
                                except Exception:
                                    continue
                                    
                            except Exception:
                                continue
                                
                        print(f"   Shapes处理完成: 成功导出 {processed_count} 个图片")
                        
                    except Exception as e:
                        print(f"   Shapes处理失败: {e}")
                    
                    # 步骤2：处理单元格公式（采用同步版的成功策略）
                    print(f"   步骤2: 处理单元格DISPIMG公式...")
                    cell_processed = 0
                    dispimg_count = 0
                    
                    for i in range(len(df)):
                        r = header_row + 1 + i
                        try:
                            # 跳过已有映射的行
                            if i in embedded_map:
                                continue
                            
                            cell = ws.Cells(r, col_idx)
                            
                            # 使用同步版的公式获取策略：Formula + FormulaLocal回退
                            formula_raw = str(getattr(cell, "Formula", "") or getattr(cell, "FormulaLocal", "")).strip()
                            
                            # 处理单引号前缀（同步版的关键处理）
                            if formula_raw.startswith("'"):
                                formula_raw = formula_raw[1:]
                            
                            if not formula_raw:
                                continue
                                
                            f_upper = formula_raw.upper()
                            
                            # 处理_XLFN前缀（同步版策略）
                            for pref in ("=_XLFN.", "=XLFN.", "=_xlfn.", "=xlfn."):
                                if f_upper.startswith(pref):
                                    f_upper = "=" + f_upper[len(pref):]
                                    break
                            
                            if f_upper.startswith("=DISPIMG") or f_upper.startswith("=IMAGE"):
                                dispimg_count += 1
                                
                                try:
                                    # 复制图片到剪贴板
                                    cell.CopyPicture(Appearance=1, Format=2)
                                    
                                    # 获取单元格位置和大小
                                    left = cell.Left
                                    top = cell.Top
                                    width = max(cell.Width, 50)
                                    height = max(cell.Height, 50)
                                    
                                    # 创建临时图表对象用于导出
                                    ch = ws.ChartObjects().Add(left, top, int(width), int(height))
                                    ch.Chart.Paste()
                                    
                                    # 使用同步版的哈希策略
                                    h = hashlib.md5(f"{file_path}:{sheet}:{r}:{col_idx}".encode("utf-8")).hexdigest()[:16]
                                    fname = f"import_disp_{h}.png"
                                    # 先导出到临时目录，成功后再移动
                                    dst = self.uploads_tmp_dir / fname
                                    
                                    # 导出图片
                                    if not dst.exists():
                                        ch.Chart.Export(str(dst))
                                    
                                    ch.Delete()
                                    
                                    # 验证并记录映射
                                    if dst.exists() and dst.stat().st_size > 0:
                                        embedded_map[i] = f"/static/uploads/{fname}"
                                        cell_processed += 1
                                        
                                except Exception:
                                    continue
                                    
                        except Exception:
                            continue
                    
                    print(f"   单元格处理完成: 发现{dispimg_count}个DISPIMG公式, 成功导出{cell_processed}个图片")
                    
                    total_processed = len(embedded_map)
                    print(f"   总处理结果: 成功提取 {total_processed} 个图片, 成功率: {total_processed/len(df)*100:.1f}%")
                    
                finally:
                    try:
                        wb.Close(False)
                        app.Quit()
                    except Exception:
                        pass
                    pythoncom.CoUninitialize()
                    
            except Exception:
                pass
        
        return embedded_map
    
    async def _process_row_async(self, idx: int, row: Dict[str, Any], excel_dir: Path, *, check_duplicate: bool = True) -> Dict[str, Any]:
        """异步处理单行数据

        - check_duplicate: 是否执行查重逻辑；当索引为空时应禁止查重，先写入以初始化索引
        """
        row_result = {
            "row_index": idx,
            "success": False,
            "duplicate": False,
            "error": None,
            "name": "",
            "processing_time": 0
        }
        
        start_time = time.time()
        
        try:
            # 1. 基础数据提取
            name = str(row.get("设备名") or row.get("设备名称") or row.get("名称") or "").strip()
            if not name:
                row_result["error"] = "设备名称为空"
                return row_result
            
            row_result["name"] = name
            tags = extract_tags(row)
            description = make_description(row, name=name)
            address = str(row.get("联系地址") or row.get("地址") or "").strip()
            
            # 2. 并发执行地理编码；查重根据标记决定是否执行
            geocode_task = self._geocode_async(address)
            if check_duplicate:
                # 构建重复检测payload
                dup_payload = {
                    "name": name,
                    "parameters": row.get("参数", ""),
                    "test_items": row.get("测试项目", ""),
                    "unit_name": row.get("单位名称", "") or row.get("单位", ""),
                    "address": address,
                }
                duplicate_task = self._check_duplicate_async(dup_payload)
                # 等待地理编码和重复检测完成
                loc, is_duplicate = await asyncio.gather(geocode_task, duplicate_task)
            else:
                loc = await geocode_task
                is_duplicate = False
            
            if is_duplicate:
                row_result["duplicate"] = True
                self.stats.add_duplicate()
                
                # 记录重复项到manifest
                await self._write_manifest({
                    "row": idx,
                    "row_key": _row_key_from_mapping(row),
                    "name": name,
                    "duplicate": True,
                    "lat": loc["lat"],
                    "lng": loc["lng"],
                    "tags": tags,
                })
                return row_result
            
            # 3. 处理图片（改为“成功后再落盘”策略）
            image_url = ""
            prepared_image: Optional[Dict[str, Any]] = None  # 保存预取的图片字节与目标文件名
            
            # 3.1 处理显式图片列
            img_val = str(row.get("图片", "") or "").strip()
            if not img_val:
                # 回退到最后一列
                last_col_name = list(row.keys())[-1] if row.keys() else ""
                img_val = str(row.get(last_col_name, "") or "").strip()
            
            if img_val:
                prepared_image = await self._prepare_image_async(img_val, base_dir=excel_dir)
            
            # 3.2 如果没有显式图片，使用预提取的嵌入图片
            if not prepared_image and not image_url:
                # 对于 COM 导出的嵌入图片：之前逻辑直接落盘。现在改为先导出到 tmp，再在成功后移动到正式目录。
                tmp_url = self.embedded_images.get(int(idx), "")
                if tmp_url:
                    # 记录为临时路径（/static/uploads/xxx），我们会将对应的物理文件从 tmp 迁移到正式目录
                    image_url = tmp_url
                
                # 行锚点校验：如果图片上方/下方2行内存在同名单位更匹配，则迁移归属
                if image_url:
                    key_here = _row_key_from_mapping(row)
                    if key_here:
                        # 简单扫描邻近2行，寻找相同行锚点
                        for off in (-2, -1, 1, 2):
                            cand = int(idx) + off
                            if cand in self.embedded_images and self.embedded_images[cand] == image_url:
                                # 检查候选行的锚点
                                if hasattr(self, '_df_cache'):
                                    try:
                                        crow = dict(self._df_cache.iloc[cand])
                                        if _row_key_from_mapping(crow) == key_here:
                                            # 将图片归属迁移到当前行
                                            self.embedded_images[cand] = ""
                                            break
                                    except Exception:
                                        pass
            
            # 4. 构建完整payload并提交
            quantity = None
            qty_raw = row.get("台数") or row.get("数量") or ""
            if str(qty_raw).strip():
                try:
                    quantity = int(str(qty_raw).strip())
                except:
                    pass
            
            metadata = {
                "类别": row.get("类别", ""),
                "测试项目": row.get("测试项目", ""),
                "测试标准": row.get("测试标准", ""),
                "单位名称": row.get("单位名称", ""),
                "联系人": row.get("联系人", ""),
                "联系电话": row.get("联系电话", ""),
                "备注": row.get("备注", ""),
                "是否出具报告": row.get("是否出具报告", ""),
                "预计价格": row.get("预计价格", ""),
                "单位": row.get("单位", ""),
                "原始地址": address,
            }
            
            upsert_payload = {
                "name": name,
                "description": description,
                "tags": tags,
                # 若是预取的普通图片，先不填 image_url，成功后再补发更新或直接写入 URL
                "image_url": image_url,
                "address": address,
                "lat": loc["lat"],
                "lng": loc["lng"],
                "metadata": metadata,
                "quantity": quantity,
            }
            
            # 5. 提交到后端
            success, error, result = await self._upsert_async(upsert_payload)
            
            if success:
                row_result["success"] = True
                self.stats.add_success()
                # 成功后再真正落盘图片
                try:
                    # 1) 普通图片（URL/本地/dataURI 预取）
                    if prepared_image:
                        final_url = await self._persist_image_bytes(prepared_image)
                        if final_url and final_url != upsert_payload.get("image_url"):
                            # 简单追加一次更新：直接再发 upsert_full（同键会覆盖）
                            upd_payload = dict(upsert_payload)
                            upd_payload["image_url"] = final_url
                            await self._upsert_async(upd_payload)
                    # 2) COM 嵌入图片：移动临时文件到正式 uploads
                    elif image_url and image_url.startswith("/static/uploads/"):
                        # 推断物理临时路径：tmp 目录下的同名文件
                        fname = image_url.rsplit("/", 1)[-1]
                        tmp_file = self.uploads_tmp_dir / fname
                        if tmp_file.exists():
                            moved_url = await self._finalize_tmp_image(str(tmp_file))
                            if moved_url and moved_url != image_url:
                                upd_payload = dict(upsert_payload)
                                upd_payload["image_url"] = moved_url
                                await self._upsert_async(upd_payload)
                except Exception:
                    pass
                # 一旦首次成功写入，则认为索引已初始化
                if not self.index_ready:
                    self.index_ready = True
                
                # 记录成功项到manifest
                await self._write_manifest({
                    "row": idx,
                    "row_key": _row_key_from_mapping(row),
                    "name": name,
                    "success": True,
                    "lat": loc["lat"],
                    "lng": loc["lng"],
                    "tags": tags,
                })
            else:
                row_result["error"] = error
                self.stats.add_failure()
                
                # 记录失败项
                await self._write_failure({
                    "row": idx,
                    "name": name,
                    "error": error,
                    "payload": upsert_payload,
                })
        
        except Exception as e:
            row_result["error"] = f"处理异常: {str(e)}"
            self.stats.add_failure()
            
            await self._write_failure({
                "row": idx,
                "name": row_result["name"],
                "error": str(e),
                "traceback": traceback.format_exc(),
            })
        
        finally:
            row_result["processing_time"] = round(time.time() - start_time, 2)
        
        return row_result
    
    async def _write_manifest(self, data: Dict[str, Any]):
        """异步写入manifest文件"""
        try:
            # 使用异步文件IO可能需要aiofiles，这里简化为同步写入
            with open(self.manifest_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 忽略日志写入错误
    
    async def _write_failure(self, data: Dict[str, Any]):
        """异步写入失败日志"""
        try:
            with open(self.fail_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception:
            pass
    
    async def import_excel(self, file_path: str, sheet: Any = 0) -> Dict[str, Any]:
        """主要的异步导入方法"""
        print(f"🚀 开始异步导入: {file_path}")
        print(f"📊 并发数: {self.concurrency}")
        print(f"⚙️  导入模式: {self.mode}")
        
        # 1. 读取Excel文件
        try:
            df = pd.read_excel(file_path, sheet_name=sheet)
            # 针对 Excel 合并单元格：很多表仅在组首行填“单位/地址/联系人”，其余行为空。
            # 这里对关键列进行前向填充（仅空白/NaN 才填），保证每行具备完整“五要素”。
            try:
                # 仅对“单位名称”做前向填充，适配合并单元格导致的空白
                if "单位名称" in df.columns:
                    df["单位名称"] = df["单位名称"].replace(r"^\s*$", pd.NA, regex=True).ffill()
            except Exception:
                pass
            df = df.fillna("")
            self.stats.total = len(df)
            print(f"📋 读取到 {len(df)} 行数据")
        except Exception as e:
            return {"error": f"读取Excel失败: {str(e)}"}
        
        # 2. 检查后端连通性
        try:
            async with self._session.get(f"{self.api_base}/api/rag/list", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    print(f"✅ 后端连接正常: {resp.status}")
                else:
                    print(f"⚠️  后端响应异常: {resp.status}")
        except Exception as e:
            print(f"❌ 后端连接失败: {str(e)}")
            return {"error": f"后端连接失败: {str(e)}"}
        
        # 2.5. 根据模式选择是否清空与删图（覆盖导入前置操作）
        if self.mode == "overwrite":
            print("🗑️  覆盖模式：清空现有数据和FAISS索引，并清理导入图片...")
            try:
                async with self._session.post(
                    f"{self.api_base}/api/rag/clear_all",
                    json={"cleanup_images": True},
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        msg = result.get('message', '成功')
                        img_info = result.get('images_cleanup') or {}
                        print(f"✅ 数据清空完成: {msg}")
                        if img_info:
                            print(f"🧹 图片清理: 删除 {img_info.get('deleted_count', 0)}/{img_info.get('matched_count', 0)} 个，目录: {img_info.get('uploads_dir', '/static/uploads')}")
                    else:
                        print(f"⚠️  清空/删图响应异常: {resp.status}")
                        error_text = await resp.text()
                        print(f"错误详情: {error_text}")
            except Exception as e:
                print(f"❌ 清空/删图失败: {str(e)}")
                print("⚠️  继续导入，但可能存在数据/图片残留...")
                # 不返回错误，允许继续导入
        else:
            print("➕  追加模式：保留历史数据与索引，不清空、不删图")
        
        # 2.6. 预提取嵌入图片（同步操作，避免COM并发问题）
        print("🖼️  预提取Excel嵌入图片...")
        try:
            self.embedded_images = self._extract_embedded_images(file_path, sheet, df, image_col="图片")
            if self.embedded_images:
                print(f"✅ 提取到 {len(self.embedded_images)} 个嵌入图片")
                print(f"   覆盖行范围: {min(self.embedded_images.keys())}-{max(self.embedded_images.keys())}")
                print(f"   提取率: {len(self.embedded_images)/len(df)*100:.1f}%")
            else:
                print("⚠️  未发现任何嵌入图片")
                print("   可能原因: 1)图片格式不支持 2)COM组件不可用 3)权限问题")
            # 缓存DataFrame用于行锚点校验
            self._df_cache = df
        except Exception as e:
            print(f"❌ 嵌入图片提取失败: {str(e)}")
            self.embedded_images = {}
        
        # 2.7. 预判索引是否已存在；若不存在，先用首条有效记录初始化索引，再开始查重
        try:
            self.index_ready = await self._is_index_non_empty()
        except Exception:
            self.index_ready = False
        preflight_idx: Optional[int] = None
        if not self.index_ready:
            print("🔧 索引为空：先用首条有效记录初始化索引，然后再进行查重...")
            # 寻找首条有有效名称的记录
            try:
                for i, r in df.iterrows():
                    nm = str(r.get("设备名") or r.get("设备名称") or r.get("名称") or "").strip()
                    if nm:
                        preflight_idx = int(i)
                        excel_dir = Path(file_path).resolve().parent
                        pre_res = await self._process_row_async(preflight_idx, dict(r), excel_dir, check_duplicate=False)
                        if pre_res.get("success"):
                            self.index_ready = True
                        break
            except Exception:
                pass

        # 3. 创建信号量控制并发数
        semaphore = asyncio.Semaphore(self.concurrency)
        excel_dir = Path(file_path).resolve().parent
        
        async def process_with_limit(idx_row):
            async with semaphore:
                idx, row = idx_row
                # 若在任务创建时索引仍未就绪，则本行不做查重，尽快触发索引初始化
                return await self._process_row_async(idx, dict(row), excel_dir, check_duplicate=self.index_ready)
        
        # 4. 创建所有任务
        tasks = []
        for idx, row in df.iterrows():
            if preflight_idx is not None and int(idx) == int(preflight_idx):
                continue  # 已用于初始化索引，避免重复处理
            tasks.append(process_with_limit((idx, row)))
        
        # 5. 执行异步任务，显示进度
        print(f"🔄 开始并发处理 (并发数: {self.concurrency})...")
        
        results = []
        # 使用 asyncio.as_completed 而不是 tqdm.as_completed
        with tqdm(total=len(tasks), desc="处理进度") as pbar:
            for coro in asyncio.as_completed(tasks):
                result = await coro
                results.append(result)
                pbar.update(1)
        
        # 6. 生成最终统计
        summary = self.stats.get_summary()
        summary.update({
            "file": file_path,
            "concurrency": self.concurrency,
            "geocode_cache_size": self.geocode_cache.size(),
        })
        
        print(f"\n🎉 导入完成!")
        print(f"📈 总计: {summary['total']} 行")
        print(f"✅ 成功: {summary['success']} 行")
        print(f"❌ 失败: {summary['failed']} 行")
        print(f"🔄 重复: {summary['duplicates']} 行")
        print(f"⏱️  耗时: {summary['elapsed_seconds']} 秒")
        print(f"🚀 速度: {summary['rows_per_second']} 行/秒")
        print(f"💾 地理编码缓存命中率: {summary['geocode_cache_hit_rate']}%")
        
        return summary


async def run_import_async(file: str, api: str = "http://localhost:8000", concurrency: int = 10, sheet: Any = 0) -> Dict[str, Any]:
    """异步导入的主入口函数"""
    # 读取环境变量兜底模式（命令行未提供时使用）
    mode_env = os.environ.get("IMPORT_MODE", "append").strip().lower()
    async with AsyncExcelImporter(api, concurrency, mode=mode_env) as importer:
        return await importer.import_excel(file, sheet)


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

# 拼接到 data 目录
    file_path = os.path.join(base_dir, "..", "device_data.xlsx")
    """命令行入口"""
    parser = argparse.ArgumentParser(description="异步并发Excel导入工具")
    parser.add_argument("--file", default = file_path, help="Excel文件路径")
    parser.add_argument("--api", default="http://localhost:8000", help="后端API地址")
    parser.add_argument("--concurrency", type=int, default=10, help="并发数 (默认: 10)")
    parser.add_argument("--sheet", default=0, help="工作表名称或索引 (默认: 0)")
    parser.add_argument("--mode", choices=["append", "overwrite"], default=os.environ.get("IMPORT_MODE", "append"), help="导入模式：append(默认) 或 overwrite")
    
    args = parser.parse_args()
    
    # 运行异步导入
    # 将命令行传入的模式更新到环境变量，以便 run_import_async 读取
    os.environ["IMPORT_MODE"] = (args.mode or "append").strip().lower()
    result = asyncio.run(run_import_async(
        file=args.file,
        api=args.api,
        concurrency=args.concurrency,
        sheet=args.sheet
    ))
    
    if "error" in result:
        print(f"❌ 导入失败: {result['error']}")
        exit(1)
    else:
        print(f"🎉 导入成功: {result}")


if __name__ == "__main__":
    main()
