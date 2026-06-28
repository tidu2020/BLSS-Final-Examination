"""AI 审核编排器：ReviewOrchestrator。

编排 KeywordMatcher（粗筛）+ SimilarityRetriever（精排）双算法，
拼装四段式回复 + 免责声明，作为业务咨询的 AI 引擎入口。

支持 RAG + LLM 模式：双算法检索 Top-K 后，将检索结果作为上下文
调用大模型生成自然语言回复；LLM 不可用时回退到规则拼装。
"""

from __future__ import annotations

import logging
from typing import List, Dict, Optional

from backend.ai.analyzer import KeywordMatcher, SimilarityRetriever
from backend.ai.llm_client import LlmClient, LlmError
from backend.knowledge.models import KnowledgeBase

logger = logging.getLogger(__name__)


class ReviewOrchestrator:
    """AI 审核编排器。

    封装双算法的编排流程：
    1. KeywordMatcher 粗筛 Top-20 候选
    2. SimilarityRetriever 精排 Top-5
    3. 拼装四段式回复 + 免责声明

    通过依赖注入 KnowledgeBase，可在知识库更新后调用 refresh() 重建索引。
    """

    DISCLAIMER = (
        "\n\n⚠️ 本意见由 AI 基于知识库生成，仅供参考。"
        "未提交法务处理，不得作为正式决策依据。"
        "如需正式法律意见，请提交法务处理。"
    )

    # 每段内容在回复中的截断长度（避免单条过长）
    FIELD_TRUNCATE = {
        "legal_answer": 300,
        "compliance_risk": 200,
        "practical_advice": 250,
        "legal_basis": 200,
    }

    def __init__(self, knowledge_base: KnowledgeBase,
                 llm_client: Optional[LlmClient] = None):
        self.kb = knowledge_base
        self.matcher = KeywordMatcher()
        self.retriever = SimilarityRetriever()
        self.llm = llm_client
        self.refresh()

    def refresh(self) -> None:
        """知识库变更后重建检索索引。"""
        self.retriever.fit(self.kb.items)

    # ---------- 公开接口 ----------

    def review(self, user_input: str,
               top_k: int = 5,
               with_disclaimer: bool = True) -> Dict:
        """审核业务咨询，返回结构化结果。

        Args:
            user_input: 用户的查询文本
            top_k: 返回前 K 条参考
            with_disclaimer: 是否附带免责声明

        Returns:
            {
                "query": str,                # 原查询
                "candidates_count": int,     # 粗筛候选数
                "results": List[Dict],       # 精排 Top-K（含相似度分）
                "answer": str,               # 四段式回复文本
                "disclaimer": str,            # 免责声明
                "mode": str,                 # "rag+llm" 或 "rag"
            }
        """
        # 1. 粗筛
        candidates = self.matcher.filter(user_input, self.kb.items, top_n=20)

        if not candidates:
            answer = "未检索到相关知识。"
            return {
                "query": user_input,
                "candidates_count": 0,
                "results": [],
                "answer": answer + (self.DISCLAIMER if with_disclaimer else ""),
                "disclaimer": self.DISCLAIMER if with_disclaimer else "",
                "mode": "rag",
            }

        # 2. 精排（含相似度分）
        ranked_with_score = self._rank_with_score(user_input, candidates,
                                                   top_k=top_k)
        items = [r["item"] for r in ranked_with_score]

        # 3. 生成回复：优先 LLM，失败回退规则拼装
        answer, mode = self._generate(user_input, items)

        if with_disclaimer:
            answer += self.DISCLAIMER

        return {
            "query": user_input,
            "candidates_count": len(candidates),
            "results": [
                {
                    "id": r["item"]["id"],
                    "question": r["item"]["question"],
                    "score": r["score"],
                    "item": r["item"],
                }
                for r in ranked_with_score
            ],
            "answer": answer,
            "disclaimer": self.DISCLAIMER if with_disclaimer else "",
            "mode": mode,
        }

    def review_text(self, user_input: str, top_k: int = 5) -> str:
        """简化版：仅返回回复文本。"""
        return self.review(user_input, top_k=top_k)["answer"]

    # ---------- 内部方法 ----------

    def _generate(self, query: str, items: List[Dict]) -> tuple:
        """生成回复文本，返回 (answer, mode)。

        优先用 LLM 基于检索结果生成；LLM 不可用或失败时回退规则拼装。
        """
        # 规则拼装作为兜底（始终先算好，保证可用）
        fallback = self._compose(items)

        if not self.llm or not self.llm.available:
            return fallback, "rag"

        try:
            answer = self._generate_with_llm(query, items)
            if answer and answer.strip():
                return answer.strip(), "rag+llm"
            logger.warning("LLM 返回空内容，回退规则拼装")
            return fallback, "rag"
        except LlmError as e:
            logger.warning("LLM 调用失败，回退规则拼装：%s", e)
            return fallback, "rag"

    def _generate_with_llm(self, query: str, items: List[Dict]) -> str:
        """用大模型基于检索到的知识生成四段式回复。

        将 Top-K 检索结果作为上下文，约束模型只依据知识库作答。
        """
        # 拼接知识库上下文
        context_parts = []
        for i, it in enumerate(items, 1):
            context_parts.append(
                f"【案例{i}】\n"
                f"问题：{it.get('question', '')}\n"
                f"法律解答：{it.get('legal_answer', '')}\n"
                f"合规风险：{it.get('compliance_risk', '')}\n"
                f"实操建议：{it.get('practical_advice', '')}\n"
                f"相关法条：{it.get('legal_basis', '')}"
            )
        context = "\n\n".join(context_parts)

        system_prompt = (
            "你是国有企业法务合规助手。请严格依据下方提供的知识库案例回答业务咨询，"
            "不得编造知识库外的法律结论。\n"
            "回答要求：\n"
            "1. 结构清晰，分四段：法律解答、合规风险、实操建议、相关法条；\n"
            "2. 语言专业但通俗易懂，适合业务人员理解；\n"
            "3. 若知识库案例不能完全覆盖用户问题，明确指出需进一步咨询法务；\n"
            "4. 不要输出与问题无关的内容。"
        )
        user_prompt = (
            f"知识库参考案例：\n{context}\n\n"
            f"业务咨询：{query}\n\n"
            f"请基于上述案例给出四段式法务意见。"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.llm.chat(
            messages,
            temperature=self.llm.temperature,
            max_tokens=self.llm.max_tokens,
        )

    def _rank_with_score(self, query: str,
                         candidates: List[Dict],
                         top_k: int = 5) -> List[Dict]:
        """精排并附带相似度分数。"""
        q_vec = self.retriever._vectorize(query)
        q_norm = self.retriever._norm(q_vec)

        scored = []
        for cand in candidates:
            idx = self.retriever._find_item_index(cand)
            if idx >= 0:
                doc_vec = self.retriever.doc_vectors[idx]
            else:
                doc_vec = self.retriever._vectorize(
                    self.retriever._doc_text(cand))
            score = self.retriever._cosine(q_vec, doc_vec, q_norm)
            scored.append({"score": score, "item": cand})

        scored.sort(key=lambda x: -x["score"])
        return scored[:top_k]

    def _compose(self, items: List[Dict]) -> str:
        """将 Top-K 条目拼装为四段式回复文本。"""
        if not items:
            return "未检索到相关知识。"

        parts = []
        for i, it in enumerate(items, 1):
            section = [
                f"【参考{i}】{it.get('question', '')}",
                f"  法律解答：{self._truncate(it.get('legal_answer', ''), 'legal_answer')}",
                f"  合规风险：{self._truncate(it.get('compliance_risk', ''), 'compliance_risk')}",
                f"  实操建议：{self._truncate(it.get('practical_advice', ''), 'practical_advice')}",
                f"  依据法条：{self._truncate(it.get('legal_basis', ''), 'legal_basis')}",
            ]
            parts.append("\n".join(section))

        return "\n\n".join(parts)

    def _truncate(self, text: str, field_name: str) -> str:
        """按字段配置截断文本，超出加省略号。"""
        if not text:
            return ""
        limit = self.FIELD_TRUNCATE.get(field_name, 200)
        text = text.replace("\n", " ").strip()
        if len(text) > limit:
            return text[:limit] + "..."
        return text
