"""法务归档前校验器：FaqValidator。

法务在确认入库前，系统自动对拟入库条目执行 6 项校验：
- 硬错误（errors）：阻止入库
  1. 问题完整性：question 字段非空（有问题才有答案）
  2. 四段式完整性：四字段均非空
- 软警告（warnings）：可强制入库
  3. 法条格式：建议含《》和"第X条"
  4. 标签完整性：至少 1 个 tag
  5. 重复校验：与现有条目相似度 > 0.8
  6. 问题格式：question 以问号结尾
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from backend.ai.analyzer import SimilarityRetriever
from backend.knowledge.models import KnowledgeBase


# 四段式字段名
REQUIRED_FIELDS = [
    "legal_answer",       # 法律解答
    "compliance_risk",    # 合规风险
    "practical_advice",   # 实操建议
    "legal_basis",        # 相关法条
]

# 法条格式正则：建议包含《法名》和"第X条"
LEGAL_BASIS_RE = re.compile(r"《.+?》.*?第.+?条")

# 相似度阈值（>此值则警告重复）
SIMILARITY_THRESHOLD = 0.8


class FaqValidator:
    """法务归档前校验器。

    法务不能直接增删改知识库，只能在归档前审核环节对拟入库条目做修改，
    经本校验器校验后确认入库。

    硬错误（errors）阻止入库；软警告（warnings）允许强制入库。
    """

    def __init__(self, knowledge_base: KnowledgeBase,
                 retriever: Optional[SimilarityRetriever] = None):
        self.kb = knowledge_base
        # 复用传入的 retriever（已 fit），避免重复构建向量
        self._retriever = retriever

    @property
    def retriever(self) -> SimilarityRetriever:
        """延迟初始化 SimilarityRetriever。"""
        if self._retriever is None:
            self._retriever = SimilarityRetriever().fit(self.kb.items)
        return self._retriever

    # ---------- 公开接口 ----------

    def validate(self, item: Dict) -> Dict:
        """对拟入库条目执行 6 项校验。

        Args:
            item: 拟入库的知识条目

        Returns:
            {
                "passed": bool,         # 无硬错误则 True
                "errors": List[str],    # 硬错误，阻止入库
                "warnings": List[str],  # 软警告，可强制
                "similar_items": List  # 相似条目（重复校验命中时）
            }
        """
        errors: List[str] = []
        warnings: List[str] = []
        similar_items: List[Dict] = []

        # 1. 问题完整性（硬）——有问题才有答案
        if not self._is_nonempty(item.get("question")):
            errors.append("问题 question 为空，必须先有问题才能作答")

        # 2. 四段式完整性（硬）
        for field in REQUIRED_FIELDS:
            if not self._is_nonempty(item.get(field)):
                errors.append(f"字段 {field} 为空")

        # 3. 法条格式（软）
        if self._is_nonempty(item.get("legal_basis")):
            if not LEGAL_BASIS_RE.search(item["legal_basis"]):
                warnings.append(
                    '法条格式不规范，建议含《法名》和"第X条"格式'
                )

        # 4. 标签完整性（软）
        tags = item.get("tags") or []
        if not tags:
            warnings.append("未设置标签，建议至少 1 个")

        # 5. 重复校验（软）
        if self._is_nonempty(item.get("question")):
            similar = self._check_duplicate(item["question"])
            if similar:
                similar_items = similar
                ids = ", ".join(s["id"] for s in similar)
                warnings.append(f"与已有条目 {ids} 高度相似")

        # 6. 问题格式（软）
        if self._is_nonempty(item.get("question")):
            q = item["question"].rstrip()
            if not q.endswith(("？", "?")):
                warnings.append('问题未以问号结尾，建议以"？"结尾')

        return {
            "passed": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "similar_items": similar_items,
        }

    def validate_or_raise(self, item: Dict) -> None:
        """校验并在有硬错误时抛出异常。

        用于路由层 confirm 接口：调用此方法可直接拦截非法入库。
        """
        result = self.validate(item)
        if not result["passed"]:
            raise ValidationError(result["errors"])
        # 警告不抛异常，由前端二次确认

    # ---------- 内部方法 ----------

    @staticmethod
    def _is_nonempty(value) -> bool:
        """判断值是否非空（字符串去空白后非空）。"""
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict)):
            return len(value) > 0
        return True

    def _check_duplicate(self, question: str) -> List[Dict]:
        """检查知识库中是否有高度相似的条目。

        使用 SimilarityRetriever 检索，对 Top-5 结果逐个计算相似度。
        """
        if not self.kb.items:
            return []

        # 用 retriever 检索 Top-5
        results = self.retriever.search(question, top_k=5)
        duplicates = []

        for item in results:
            # 计算问题文本的相似度
            sim = self.retriever.similarity(question, item["question"])
            if sim > SIMILARITY_THRESHOLD:
                duplicates.append({
                    "id": item["id"],
                    "question": item["question"],
                    "similarity": round(sim, 3),
                })

        return duplicates


class ValidationError(Exception):
    """校验失败异常。"""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("；".join(errors))
