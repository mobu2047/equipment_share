"""
Excel 导入脚本

功能：
- 读取 Excel（列头见 README/图片示例），按列映射生成设备信息
- 从“参数/测试标准/资质介绍”等整理描述，并抽取 tags
- 调用百度地理编码将地址转经纬度，写入 lat/lng
- 处理图片列（本地路径/URL/Base64），保存到 static/uploads 并生成 image_url
- 调用后端 /api/rag/upsert_full 完成入库与向量生成

使用：
  python tools/import_excel.py --file data.xlsx --api http://localhost:8000 --ak <BAIDU_MAP_AK>

注意：
- 建议在虚拟环境下运行，确保 requirements 已安装
- 对外网调用有限速，必要时添加 --sleep 0.2 之类的节流
"""

import argparse
import base64
import hashlib
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
import re  # 提升“台数/数量”解析的鲁棒性：支持如“3台”“3.0”等格式
import json
import traceback
import asyncio

import pandas as pd
import requests
from tqdm import tqdm
from openpyxl import load_workbook
# 统一调用后端的地理编码实现；若直接运行脚本导致包路径不可见，则回退添加项目根
try:
    from backend.services.geocode_baidu import geocode_address
except Exception:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.services.geocode_baidu import geocode_address
try:
    # 可选：Windows 下使用 COM 提取由公式 DISPIMG/IMAGE 渲染的图片
    import win32com.client  # type: ignore
    HAS_COM = True
except Exception:
    HAS_COM = False

# 去除本地重复的地理编码实现，统一调用 backend.services.geocode_baidu


def normalize_tag(text: str) -> str:
    return (text or "").strip().lower()


def extract_tags(row: Dict[str, Any]) -> List[str]:
    """仅使用显式列生成标签；不再从参数里猜测关键词。"""
    tags: List[str] = []
    for key in ["类别", "测试项目", "测试标准"]:
        val = str(row.get(key) or "").strip()
        if val:
            tags.extend([normalize_tag(x) for x in val.replace(";", ",").split(",") if x.strip()])
    # 去重
    uniq = []
    seen = set()
    for t in tags:
        if t and t not in seen:
            uniq.append(t)
            seen.add(t)
    return uniq


def make_description(row: Dict[str, Any], name: str = "") -> str:
    """生成更详细的描述。

    设计原则：
    - 先给出核心识别信息（名称/类别/测试项目），便于阅读与检索
    - 再补充技术要点（参数/测试标准/资质/报告/价格）
    """
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
            # URL
            if val.startswith("http://") or val.startswith("https://"):
                resp = requests.get(val, timeout=30)
                resp.raise_for_status()
                content = resp.content
                ext = os.path.splitext(val.split("?")[0])[1] or _ext_from_headers(resp.headers)
            # dataURI
            elif val.startswith("data:image/") and ";base64," in val:
                meta, b64 = val.split(",", 1)
                content = base64.b64decode(b64)
                ext = ".jpg"
            else:
                # 本地路径（绝对/相对）
                path = Path(val)
                if not path.is_absolute() and base_dir:
                    path = (base_dir / path).resolve()
                if not path.exists():
                    continue
                with open(path, "rb") as f:
                    content = f.read()
                ext = os.path.splitext(str(path))[-1] or ".jpg"

            h = hashlib.md5(content).hexdigest()[:16]
            fname = f"import_{h}{ext}"
            dst = uploads_dir / fname
            with open(dst, "wb") as f:
                f.write(content)
            return f"/static/uploads/{fname}"
        except Exception:
            continue
    return ""


def _baidu_sign(path: str, params: List[tuple], sk: str) -> str:
    """计算百度开放平台 sn。path 形如 "/geocoding/v3/"，params 为有序列表。"""
    from urllib.parse import urlencode, quote_plus
    # 注意顺序保持
    query_str = urlencode(params)
    raw = f"{path}?{query_str}{sk}"
    return hashlib.md5(quote_plus(raw).encode("utf-8")).hexdigest()


def geocode_sync(address: str, ak: Any) -> Dict[str, Optional[float]]:
    """同步包装：统一调用 backend.services.geocode_baidu.geocode_address。

    ak 接受：None / "AK" / ["AK1","AK2"] / [{"ak":"AK","sk":"SK"}, ...]
    未提供时由服务端脚本自行从环境变量读取（BAIDU_CREDENTIALS / BAIDU_AK,BAIDU_SK）。
    """
    if not address:
        return {"lat": None, "lng": None}

    cred_list: List[tuple] = []
    if isinstance(ak, list):
        for item in ak:
            if isinstance(item, dict) and item.get("ak"):
                cred_list.append((item.get("ak"), item.get("sk")))
            elif isinstance(item, (list, tuple)) and len(item) > 0:
                cred_list.append((item[0], item[1] if len(item) > 1 else None))
            elif isinstance(item, str):
                cred_list.append((item, None))
    elif isinstance(ak, dict) and ak.get("ak"):
        cred_list.append((ak.get("ak"), ak.get("sk")))
    elif isinstance(ak, str) and ak.strip():
        cred_list.append((ak.strip(), None))

    try:
        return asyncio.run(geocode_address(address, credentials=cred_list or None))
    except Exception:
        return {"lat": None, "lng": None}


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


def run_import(file: str, api: str = "http://localhost:8000", ak: Any = None, sheet: Any = 0,
               image_col: Optional[str] = "图片") -> Dict[str, int]:
    """以编程方式执行导入。

    参数：
    - file: Excel 路径
    - api: 后端基地址
    - ak: 备用的百度 AK（当前未使用）
    - sheet: sheet 名称或索引

    返回：{"ok": 成功条数, "fail": 失败条数}
    """

    df = pd.read_excel(file, sheet_name=sheet)
    df = df.fillna("")

    # 预创建上传目录
    project_root = Path(__file__).resolve().parents[1]
    uploads_dir = project_root / "static" / "uploads"
    excel_dir = Path(file).resolve().parent
    fail_log = project_root / "data" / "import_failures.jsonl"
    fail_log.parent.mkdir(parents=True, exist_ok=True)

    # 连接性预检：打印后端连通性与版本信息
    try:
        ping = requests.get(f"{api}/api/rag/list", timeout=10)
        print("[check] backend reachable:", ping.status_code)
    except Exception as e:
        print("[check] backend unreachable:", repr(e))

    # 尝试提取嵌入图片并按行建立映射（df 行索引 -> image_url）
    embedded_map: Dict[int, str] = {}
    try:
        wb = load_workbook(filename=file, data_only=True)
        ws = wb[sheet] if isinstance(sheet, str) else wb.worksheets[int(sheet)]
        for img in getattr(ws, "_images", []):
            try:
                a = getattr(img, "anchor", None)
                r0 = getattr(getattr(a, "_from", None), "row", None)
                if r0 is None:
                    continue
                # openpyxl anchor 行通常从 0 开始；DataFrame 行索引通常从 0 对应 Excel 第2行
                excel_row_1based = int(r0) + 1
                candidates = [excel_row_1based - 2, int(r0) - 1]
                # 选择目标行：无显式图片列值且未已有映射
                chosen_idx = None
                for ci in candidates:
                    if 0 <= ci < len(df):
                        if image_col and image_col in df.columns and str(df.iloc[ci].get(image_col) or "").strip():
                            continue
                        if ci in embedded_map:
                            continue
                        chosen_idx = ci
                        break
                if chosen_idx is None:
                    continue
                # 读取图片二进制
                content = None
                if hasattr(img, "_data"):
                    try:
                        content = img._data()
                    except Exception:
                        content = None
                # 某些版本 _data 返回对象，尝试 bytes()
                try:
                    if content is not None and not isinstance(content, (bytes, bytearray)):
                        content = bytes(content)
                except Exception:
                    content = None
                if not content:
                    continue
                h = hashlib.md5(content).hexdigest()[:16]
                fname = f"import_embed_{h}.png"
                dst = uploads_dir / fname
                if not dst.exists():
                    with open(dst, "wb") as f:
                        f.write(content)
                url = f"/static/uploads/{fname}"
                embedded_map[chosen_idx] = url
            except Exception:
                continue
    except Exception:
        pass

    # 进一步：优先用 WPS（et.Application/ket.Application）渲染 DISPIMG，再回退到 Excel COM
    if HAS_COM:
        # WPS 优先
        for prog_id in ("ket.Application", "et.Application"):
            try:
                et = win32com.client.Dispatch(prog_id)
                et.Visible = False
                et.DisplayAlerts = False
                wb = et.Workbooks.Open(str(Path(file).resolve()))
                ws = wb.Worksheets(sheet if isinstance(sheet, str) else wb.Worksheets(int(sheet) + 1).Name)

                if image_col and image_col in df.columns:
                    col_idx = list(df.columns).index(image_col) + 1
                else:
                    col_idx = len(df.columns)

                header_row = 1
                # 先遍历 Shapes（多数 DISPIMG 在 WPS 会生成形状）
                try:
                    scount = ws.Shapes.Count
                    for sidx in range(1, int(scount) + 1):
                        try:
                            shp = ws.Shapes(sidx)
                            tl = getattr(shp, 'TopLeftCell', None)
                            if tl is None:
                                continue
                            r = int(getattr(tl, 'Row', 0))
                            c = int(getattr(tl, 'Column', 0))
                            if r <= header_row or c != col_idx:
                                continue
                            i = (r - header_row - 1)
                            # 若该行已有显式图片列或已有映射，则跳过，避免重复导出
                            if 0 <= i < len(df):
                                if image_col and image_col in df.columns and str(df.iloc[i].get(image_col) or "").strip():
                                    continue
                                if i in embedded_map:
                                    continue
                            shp.CopyPicture(Appearance=1, Format=2)
                            left = getattr(shp, 'Left', 0)
                            top = getattr(shp, 'Top', 0)
                            width = max(int(getattr(shp, 'Width', 50)), 50)
                            height = max(int(getattr(shp, 'Height', 50)), 50)
                            ch = ws.ChartObjects().Add(left, top, width, height)
                            ch.Chart.Paste()
                            h = hashlib.md5(f"{file}:{sheet}:wps_shape:{r}:{c}:{getattr(shp,'Name','')}".encode('utf-8')).hexdigest()[:16]
                            fname = f"import_wps_shape_{h}.png"
                            dst = uploads_dir / fname
                            if not dst.exists():
                                ch.Chart.Export(str(dst))
                            ch.Delete()
                            url = f"/static/uploads/{fname}"
                            if 0 <= i < len(df) and i not in embedded_map:
                                embedded_map[i] = url
                        except Exception:
                            continue
                except Exception:
                    pass

                # 再逐单元格处理公式（WPS 也支持）
                for i in range(len(df)):
                    r = header_row + 1 + i
                    try:
                        cell = ws.Cells(r, col_idx)
                        formula_raw = str(getattr(cell, "Formula", "") or getattr(cell, "FormulaLocal", "")).strip()
                        if formula_raw.startswith("'"):
                            formula_raw = formula_raw[1:]
                        f_upper = formula_raw.upper()
                        for pref in ("=_XLFN.", "=XLFN.", "=_xlfn.", "=xlfn."):
                            if f_upper.startswith(pref):
                                f_upper = "=" + f_upper[len(pref):]
                                break
                        if f_upper.startswith("=DISPIMG") or f_upper.startswith("=IMAGE"):
                            # 已有显式图片列或已有映射则跳过导出
                            if image_col and image_col in df.columns and str(df.iloc[i].get(image_col) or "").strip():
                                continue
                            if i in embedded_map:
                                continue
                            cell.CopyPicture(Appearance=1, Format=2)
                            left = cell.Left
                            top = cell.Top
                            width = max(cell.Width, 50)
                            height = max(cell.Height, 50)
                            ch = ws.ChartObjects().Add(left, top, int(width), int(height))
                            ch.Chart.Paste()
                            h = hashlib.md5(f"{file}:{sheet}:wps_cell:{r}:{col_idx}".encode("utf-8")).hexdigest()[:16]
                            fname = f"import_wps_cell_{h}.png"
                            dst = uploads_dir / fname
                            if not dst.exists():
                                ch.Chart.Export(str(dst))
                            ch.Delete()
                            url = f"/static/uploads/{fname}"
                            if i not in embedded_map:
                                embedded_map[i] = url
                    except Exception:
                        continue

                try:
                    wb.Close(False)
                    et.Quit()
                except Exception:
                    pass
                # 若 WPS 成功，跳过 Excel 分支
                break
            except Exception:
                # 尝试下一个 ProgID 或回退到 Excel COM 分支
                try:
                    et.Quit()
                except Exception:
                    pass
                continue

        # Excel 回退（原逻辑）
        try:
            excel = win32com.client.Dispatch("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            wb = excel.Workbooks.Open(str(Path(file).resolve()))
            ws = wb.Worksheets(sheet if isinstance(sheet, str) else wb.Worksheets(int(sheet) + 1).Name)

            # 选择图片列索引：优先显式列名，否则最后一列
            if image_col and image_col in df.columns:
                col_idx = list(df.columns).index(image_col) + 1  # Excel 1-based
            else:
                col_idx = len(df.columns)  # 最后一列

            header_row = 1  # 假设第1行为表头
            # 优先：遍历 Shapes（更准确匹配由 DISPIMG 渲染出的图片形状）
            try:
                count = ws.Shapes.Count
                for sidx in range(1, int(count) + 1):
                    try:
                        shp = ws.Shapes(sidx)
                        tl = getattr(shp, 'TopLeftCell', None)
                        if tl is None:
                            continue
                        r = int(getattr(tl, 'Row', 0))
                        c = int(getattr(tl, 'Column', 0))
                        if r <= header_row or c != col_idx:
                            continue
                        # 导出 shape：复制为图片 -> 粘贴到临时图表 -> 导出为 png
                        shp.CopyPicture(Appearance=1, Format=2)
                        left = getattr(shp, 'Left', 0)
                        top = getattr(shp, 'Top', 0)
                        width = max(int(getattr(shp, 'Width', 50)), 50)
                        height = max(int(getattr(shp, 'Height', 50)), 50)
                        ch = ws.ChartObjects().Add(left, top, width, height)
                        ch.Chart.Paste()
                        h = hashlib.md5(f"{file}:{sheet}:shape:{r}:{c}:{getattr(shp,'Name','')}`".encode('utf-8')).hexdigest()[:16]
                        fname = f"import_shape_{h}.png"
                        dst = uploads_dir / fname
                        ch.Chart.Export(str(dst))
                        ch.Delete()
                        url = f"/static/uploads/{fname}"
                        i = (r - header_row - 1)
                        if 0 <= i < len(df) and i not in embedded_map:
                            embedded_map[i] = url
                    except Exception:
                        continue
            except Exception:
                pass
            for i in range(len(df)):
                r = header_row + 1 + i
                try:
                    cell = ws.Cells(r, col_idx)
                    # 兼容 _XLFN 前缀与单引号
                    formula_raw = str(getattr(cell, "Formula", "") or getattr(cell, "FormulaLocal", "")).strip()
                    if formula_raw.startswith("'"):
                        formula_raw = formula_raw[1:]
                    f_upper = formula_raw.upper()
                    # 去掉 =_XLFN. 或 =XLFN. 前缀
                    for pref in ("=_XLFN.", "=XLFN.", "=_xlfn.", "=xlfn."):
                        if f_upper.startswith(pref):
                            f_upper = "=" + f_upper[len(pref):]
                            break
                    if f_upper.startswith("=DISPIMG") or f_upper.startswith("=IMAGE"):
                        if image_col and image_col in df.columns and str(df.iloc[i].get(image_col) or "").strip():
                            continue
                        if i in embedded_map:
                            continue
                        # 拷贝为图片并通过临时图表导出
                        cell.CopyPicture(Appearance=1, Format=2)
                        left = cell.Left
                        top = cell.Top
                        width = max(cell.Width, 50)
                        height = max(cell.Height, 50)
                        ch = ws.ChartObjects().Add(left, top, width, height)
                        ch.Chart.Paste()
                        h = hashlib.md5(f"{file}:{sheet}:{r}:{col_idx}".encode("utf-8")).hexdigest()[:16]
                        fname = f"import_disp_{h}.png"
                        dst = uploads_dir / fname
                        if not dst.exists():
                            ch.Chart.Export(str(dst))
                        ch.Delete()
                        url = f"/static/uploads/{fname}"
                        if i not in embedded_map:
                            embedded_map[i] = url
                except Exception:
                    continue
        except Exception:
            pass
        finally:
            try:
                wb.Close(False)
                excel.Quit()
            except Exception:
                pass

    # 构建历史 manifest（用于幂等与回滚）
    manifest_path = project_root / "data" / "import_manifest.jsonl"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    ok, fail = 0, 0
    for idx, row in tqdm(df.iterrows(), total=len(df)):
        row = dict(row)
        name = str(row.get("设备名") or row.get("设备名称") or row.get("名称") or "").strip()
        if not name:
            fail += 1
            continue
        tags = extract_tags(row)
        description = make_description(row, name=name)

        # 地址 → 经纬度
        address = str(row.get("联系地址") or row.get("地址") or "").strip()
        loc = geocode_sync(address, ak)

        # 处理图片列：优先显式列名，其次回退到最后一列
        img_val = ""
        if image_col and image_col in row:
            img_val = str(row.get(image_col) or "")
        else:
            last_col_name = list(row.keys())[-1]
            img_val = str(row.get(last_col_name) or "")
        # 在决定保存图片前先做“重复检测”：避免无意义地写入重复图像
        # 使用后端专用接口 /api/rag/check_duplicate，不触发入库/向量生成
        try:
            chk_payload = {
                "name": name,
                "parameters": row.get("参数", ""),
                "test_items": row.get("测试项目", ""),
                "unit_name": row.get("单位名称", "") or row.get("单位", ""),
                "address": address,
            }
            chk = requests.post(f"{api}/api/rag/check_duplicate", json=chk_payload, timeout=15)
            if chk.ok:
                chk_json = chk.json()
                if isinstance(chk_json, dict) and chk_json.get("duplicate") is True:
                    ok += 1
                    with open(manifest_path, "a", encoding="utf-8") as mf:
                        mf.write(json.dumps({
                            "row": int(idx),
                            "row_key": _row_key_from_mapping(row),
                            "name": name,
                            "image_url": "",
                            "address": address,
                            "lat": loc["lat"],
                            "lng": loc["lng"],
                            "tags": tags,
                            "dup": True,
                        }, ensure_ascii=False) + "\n")
                    continue
        except Exception:
            # 查重失败时继续正常流程，不影响入库
            pass

        image_url = save_image(img_val, uploads_dir, base_dir=excel_dir)
        if not image_url:
            # 使用形状/公式提取的图片（行号映射），并做校准校验 
            image_url = embedded_map.get(int(idx), "")
            if image_url:
                # 行锚点核对：如果图片上方/下方 2 行内存在同名单位更匹配，则迁移归属
                key_here = _row_key_from_mapping(row)
                if not key_here:
                    # 无法判断，保留现匹配
                    pass
                else:
                    # 简单扫描邻近 2 行，寻找相同行锚点
                    for off in (-2, -1, 1, 2):
                        cand = int(idx) + off
                        if 0 <= cand < len(df):
                            crow = dict(df.iloc[cand])
                            if _row_key_from_mapping(crow) == key_here and embedded_map.get(cand) == image_url:
                                # 将图片归属迁移到当前行，避免 idx 错配
                                embedded_map[cand] = ""
                                break

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

        # 新增台数字段：更鲁棒的解析（支持“3台”“3.0”“数量（台）”等列/格式）
        quantity = _get_quantity_from_row(row)

        payload = {
            "name": name,
            "description": description,
            "tags": tags,
            "image_url": image_url,
            "address": address,
            "lat": loc["lat"],
            "lng": loc["lng"],
            "metadata": metadata,
            "quantity": quantity,
        }

        try:
            resp = requests.post(f"{api}/api/rag/upsert_full", json=payload, timeout=30)
            if resp.ok:
                ok += 1
                # 记录 manifest（便于回滚与审计）
                with open(manifest_path, "a", encoding="utf-8") as mf:
                    mf.write(json.dumps({
                        "row": int(idx),
                        "row_key": _row_key_from_mapping(row),
                        "name": name,
                        "image_url": image_url,
                        "address": address,
                        "lat": loc["lat"],
                        "lng": loc["lng"],
                        "tags": tags,
                    }, ensure_ascii=False) + "\n")
            else:
                fail += 1
                # 调试输出：状态码与响应体
                print(f"[fail] name={name} status={resp.status_code} body={resp.text[:200]}...")
                with open(fail_log, "a", encoding="utf-8") as flog:
                    flog.write(json.dumps({
                        "name": name,
                        "status": resp.status_code,
                        "response": resp.text,
                        "payload": payload,
                    }, ensure_ascii=False) + "\n")
        except Exception as e:
            fail += 1
            print(f"[error] name={name} exception={repr(e)}")
            with open(fail_log, "a", encoding="utf-8") as flog:
                flog.write(json.dumps({
                    "name": name,
                    "exception": repr(e),
                    "trace": traceback.format_exc(),
                    "payload": payload,
                }, ensure_ascii=False) + "\n")

    return {"ok": ok, "fail": fail}


# def main() -> None:
#     ap = argparse.ArgumentParser()
#     ap.add_argument("--file", required=True, help="Excel 文件路径")
#     ap.add_argument("--api", default="http://localhost:8000", help="后端基地址")
#     ap.add_argument("--ak", required=False, default="", help="百度地图 Geocoding AK（当前未使用）")
#     ap.add_argument("--sheet", default=0, help="sheet 名或索引")
#     args = ap.parse_args()

#     result = run_import(file=args.file, api=args.api, ak=args.ak, sheet=args.sheet)
#     print(f"done. ok={result['ok']}, fail={result['fail']}")

# run_import_example.py
def main() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))

# 拼接到 data 目录
    file_path = os.path.join(base_dir, "..", "data", "device_data.xlsx")

    result = run_import(
        file= file_path,      # Excel 路径
        api="http://localhost:8000",       # 你的后端地址
        sheet=0,                            # sheet 名称或索引
    )
    print(result)  # {'ok': X, 'fail': Y}
if __name__ == "__main__":
    main()

