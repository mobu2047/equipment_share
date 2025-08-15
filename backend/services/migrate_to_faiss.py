"""
数据迁移脚本：从旧JSON存储迁移到FAISS

使用方法：
1. 确保已安装faiss-cpu
2. 运行此脚本迁移现有数据
3. 迁移完成后，应用将自动使用FAISS版本
"""

import json
from pathlib import Path
from typing import List, Dict, Any

from backend.core.logger import logger
from backend.services.rag_store_faiss import RagStoreFaiss


def migrate_to_faiss() -> bool:
    """迁移现有JSON数据到FAISS存储。"""
    try:
        # 检查是否存在旧数据
        project_root = Path(__file__).resolve().parents[2]
        old_store_file = project_root / "data" / "rag_store.json"
        
        if not old_store_file.exists():
            logger.info("migrate.no_old_data", extra={"event": "migrate_skip"})
            return True
            
        # 读取旧数据
        with open(old_store_file, 'r', encoding='utf-8') as f:
            old_data = json.load(f)
            
        items = old_data.get("items", [])
        if not items:
            logger.info("migrate.empty_old_data", extra={"event": "migrate_skip"})
            return True
            
        logger.info("migrate.start", extra={"event": "migrate_start", "count": len(items)})
        
        # 创建FAISS存储并迁移数据
        store = RagStoreFaiss()
        
        # 检查是否有向量数据
        has_vectors = any(item.get("vector") for item in items)
        
        if has_vectors:
            # 直接迁移（包含向量）
            for item in items:
                try:
                    store.upsert(
                        name=item.get("name", ""),
                        description=item.get("description", ""),
                        tags=item.get("tags") or [],
                        image_url=item.get("image_url", ""),
                        vector=item.get("vector") or []
                    )
                except Exception as e:
                    logger.warning("migrate.item_failed", extra={"event": "migrate_item_error", "name": item.get("name"), "error": str(e)})
                    continue
        else:
            # 没有向量数据，需要重新生成
            logger.info("migrate.no_vectors", extra={"event": "migrate_no_vectors"})
            # 这里可以调用embedding服务重新生成向量
            # 暂时跳过，等应用启动后通过reindex接口处理
            
        # 备份旧文件
        backup_file = old_store_file.with_suffix('.json.backup')
        old_store_file.rename(backup_file)
        
        logger.info("migrate.success", extra={"event": "migrate_success", "count": len(items), "backup": str(backup_file)})
        return True
        
    except Exception as e:
        logger.error("migrate.failed", extra={"event": "migrate_error", "error": str(e)})
        return False


if __name__ == "__main__":
    """直接运行迁移脚本。"""
    success = migrate_to_faiss()
    if success:
        print("✅ 数据迁移成功！")
    else:
        print("❌ 数据迁移失败，请检查日志。")
