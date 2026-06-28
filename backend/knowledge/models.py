"""知识库数据模型：KnowledgeBase 类。

封装知识库的加载、查询、增删改、持久化。
支持从 JSON 加载，也支持首次从 FAQ Markdown 预处理后构建。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Dict, Optional

from backend.knowledge.preprocessor import DataPreprocessor


class KnowledgeBase:
    """知识库：FAQ 条目的内存存储 + JSON 持久化。

    职责：
    - 加载：从 JSON 文件加载，或调用 preprocessor 解析 Markdown
    - 查询：按 id / 标签 / 月份 / 案例链查询
    - 检索：委托给 AI 引擎的检索算法（本类不实现检索算法）
    - 增删改：法务确认入库 / 管理员管理
    - 持久化：保存到 JSON
    """

    def __init__(self, data_path: str = "data/knowledge_base.json",
                 source_md: str = "FAQ_四部分版_最终.md"):
        self.data_path = data_path
        self.source_md = source_md
        self.items: List[Dict] = []

    # ---------- 加载 ----------

    def load(self) -> "KnowledgeBase":
        """加载知识库。

        优先从 JSON 加载；若 JSON 不存在，则从 Markdown 解析并保存。
        """
        if os.path.exists(self.data_path):
            with open(self.data_path, "r", encoding="utf-8") as f:
                self.items = json.load(f)
        else:
            # 首次运行：从 Markdown 解析
            os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
            preprocessor = DataPreprocessor(source_file=self.source_md)
            self.items = preprocessor.parse_and_save(self.data_path)
        return self

    def save(self) -> None:
        """持久化到 JSON。"""
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)

    # ---------- 查询 ----------

    def get(self, item_id: str) -> Optional[Dict]:
        """按 id 查询条目。"""
        for it in self.items:
            if it.get("id") == item_id:
                return it
        return None

    def list_by_month(self, month: str) -> List[Dict]:
        """按月份查询。"""
        return [it for it in self.items if it.get("month") == month]

    def list_by_category(self, category: str) -> List[Dict]:
        """按分类查询。"""
        return [it for it in self.items if it.get("category") == category]

    def list_by_case(self, case_id: str) -> List[Dict]:
        """按案例链查询。"""
        return [it for it in self.items if it.get("case_id") == case_id]

    def list_by_tag(self, tag: str) -> List[Dict]:
        """按标签查询。"""
        return [it for it in self.items if tag in it.get("tags", [])]

    def all_months(self) -> List[str]:
        """返回所有月份（去重升序）。"""
        return sorted(set(it.get("month", "") for it in self.items))

    def all_categories(self) -> List[str]:
        """返回所有分类（去重升序）。"""
        return sorted(set(it.get("category", "") for it in self.items))

    def status_breakdown(self) -> Dict[str, int]:
        """按 status 字段统计分布。"""
        counts: Dict[str, int] = {}
        for it in self.items:
            s = it.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return counts

    # ---------- 增删改 ----------

    def add(self, item: Dict) -> Dict:
        """新增条目。

        自动补全 id、created_at、status（如未提供）。
        """
        if not item.get("id"):
            item["id"] = self._next_id(item.get("month"))
        item.setdefault("status", "confirmed")
        item.setdefault("created_by", "legal")
        item.setdefault("created_at",
                        datetime.now().isoformat(timespec="seconds"))
        item.setdefault("source", "manual")
        item.setdefault("source_work_order_id", None)
        item.setdefault("case_id", None)
        item.setdefault("category", "其他")
        item.setdefault("tags", [])
        self.items.append(item)
        return item

    def update(self, item_id: str, patch: Dict) -> Optional[Dict]:
        """部分更新条目。

        Args:
            item_id: 要更新的条目 id
            patch: 要更新的字段（仅覆盖提供的字段）

        Returns:
            更新后的条目；若 id 不存在返回 None
        """
        for it in self.items:
            if it.get("id") == item_id:
                for k, v in patch.items():
                    if k != "id":  # id 不可改
                        it[k] = v
                return it
        return None

    def delete(self, item_id: str) -> bool:
        """删除条目。返回是否删除成功。"""
        for i, it in enumerate(self.items):
            if it.get("id") == item_id:
                self.items.pop(i)
                return True
        return False

    # ---------- 统计 ----------

    def count(self) -> int:
        """条目总数。"""
        return len(self.items)

    def stats(self) -> Dict:
        """知识库统计摘要。"""
        return {
            "total": len(self.items),
            "months": len(self.all_months()),
            "categories": len(self.all_categories()),
            "confirmed": sum(
                1 for it in self.items
                if it.get("status") == "confirmed"
            ),
            "draft": sum(
                1 for it in self.items
                if it.get("status") == "draft"
            ),
        }

    # ---------- 内部方法 ----------

    def _next_id(self, month: Optional[str]) -> str:
        """生成下一个 FAQ id。"""
        month = month or datetime.now().strftime("%Y%m")
        # 找该月份下最大序号
        max_seq = 0
        for it in self.items:
            if it.get("month") == month and it.get("id", "").startswith(
                    f"FAQ-{month}-"):
                try:
                    seq = int(it["id"].rsplit("-", 1)[-1])
                    if seq > max_seq:
                        max_seq = seq
                except ValueError:
                    pass
        return f"FAQ-{month}-{max_seq + 1:03d}"
