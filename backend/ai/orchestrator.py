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

    # 相关度阈值：决定走哪一级回复策略
    # high：知识库 + LLM 优化（标注原文）
    # mid：参考知识库 + LLM（提示参考）
    # low：纯 LLM 回答（提示未用知识库）
    RELEVANCE_HIGH = 0.45   # >= 0.45 视为高相关度
    RELEVANCE_LOW = 0.15    # < 0.15 视为低相关度

    def __init__(self, knowledge_base: KnowledgeBase,
                 llm_client: Optional[LlmClient] = None):
        self.kb = knowledge_base
        self.matcher = KeywordMatcher()
        self.retriever = SimilarityRetriever()
        self.llm = llm_client
        # 用户反馈记录：用于自动优化检索
        # 格式：{faq_id: {"relevant": int, "irrelevant": int}}
        self.feedback: Dict[str, Dict[str, int]] = {}
        self.refresh()

    def refresh(self) -> None:
        """知识库变更后重建检索索引。"""
        self.retriever.fit(self.kb.items)

    # ---------- 公开接口 ----------

    def review(self, user_input: str,
               top_k: int = 5,
               with_disclaimer: bool = True,
               extra_context: str = "") -> Dict:
        """审核业务咨询，返回结构化结果。

        支持三级相关度兜底策略：
        - 高相关度（score >= RELEVANCE_HIGH）：知识库 + LLM 优化，标注原文
        - 中相关度（RELEVANCE_LOW <= score < RELEVANCE_HIGH）：参考知识库 + LLM
        - 低相关度（score < RELEVANCE_LOW）：纯 LLM 回答，提示未用知识库

        Args:
            user_input: 用户的查询文本
            top_k: 返回前 K 条参考
            with_disclaimer: 是否附带免责声明
            extra_context: 额外上下文（如上传材料文本），参与 LLM 生成

        Returns:
            {
                "query": str,
                "candidates_count": int,
                "results": List[Dict],      # 精排 Top-K（含相似度分、来源标记）
                "answer": str,
                "disclaimer": str,
                "mode": str,               # "rag+llm" / "rag" / "llm-only" / "llm-fallback"
                "relevance": str,          # "high" / "mid" / "low" / "none"
                "sources": List[Dict],     # 引用的知识库条目（供前端点开查看）
            }
        """
        # 1. 粗筛
        candidates = self.matcher.filter(user_input, self.kb.items, top_n=20)

        if not candidates:
            # 无候选：纯 LLM 兜底（若可用）
            answer, mode = self._generate_llm_only(user_input, extra_context)
            relevance = "none"
            if with_disclaimer:
                answer += self.DISCLAIMER
            return {
                "query": user_input,
                "candidates_count": 0,
                "results": [],
                "answer": answer,
                "disclaimer": self.DISCLAIMER if with_disclaimer else "",
                "mode": mode,
                "relevance": relevance,
                "sources": [],
            }

        # 2. 精排（含相似度分）
        ranked_with_score = self._rank_with_score(user_input, candidates,
                                                   top_k=top_k)
        items = [r["item"] for r in ranked_with_score]

        # 3. 判定相关度等级
        top_score = ranked_with_score[0]["score"] if ranked_with_score else 0.0
        if top_score >= self.RELEVANCE_HIGH:
            relevance = "high"
        elif top_score >= self.RELEVANCE_LOW:
            relevance = "mid"
        else:
            relevance = "low"

        # 4. 按相关度生成回复
        answer, mode = self._generate(user_input, items, relevance, extra_context)

        if with_disclaimer:
            answer += self.DISCLAIMER

        # 构造引用来源（供前端点开查看）
        sources = [
            {
                "id": r["item"]["id"],
                "question": r["item"]["question"],
                "legal_answer": r["item"].get("legal_answer", ""),
                "compliance_risk": r["item"].get("compliance_risk", ""),
                "practical_advice": r["item"].get("practical_advice", ""),
                "legal_basis": r["item"].get("legal_basis", ""),
                "score": r["score"],
                "cited": relevance in ("high", "mid"),
            }
            for r in ranked_with_score
        ]

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
            "relevance": relevance,
            "sources": sources,
        }

    def review_text(self, user_input: str, top_k: int = 5) -> str:
        """简化版：仅返回回复文本。"""
        return self.review(user_input, top_k=top_k)["answer"]

    # ---------- 内部方法 ----------

    def _generate(self, query: str, items: List[Dict],
                  relevance: str = "high",
                  extra_context: str = "") -> tuple:
        """按相关度等级生成回复，返回 (answer, mode)。

        - high：知识库 + LLM 优化（标注原文）
        - mid：参考知识库 + LLM（提示参考）
        - low：纯 LLM 回答（提示未用知识库）
        """
        # 规则拼装作为兜底（始终先算好，保证可用）
        fallback = self._compose(items)

        # 低相关度：纯 LLM 回答
        if relevance == "low":
            return self._generate_llm_only(query, extra_context, fallback)

        if not self.llm or not self.llm.available:
            # LLM 不可用，回退规则拼装
            return fallback, "rag"

        try:
            answer = self._generate_with_llm(query, items, relevance, extra_context)
            if answer and answer.strip():
                return answer.strip(), "rag+llm"
            logger.warning("LLM 返回空内容，回退规则拼装")
            return fallback, "rag"
        except LlmError as e:
            logger.warning("LLM 调用失败，回退规则拼装：%s", e)
            return fallback, "rag"

    def _generate_llm_only(self, query: str, extra_context: str = "",
                          fallback: str = "") -> tuple:
        """纯 LLM 回答（不依赖知识库）。

        若 LLM 不可用，回退 fallback 或默认提示。
        """
        if not self.llm or not self.llm.available:
            return fallback or "未检索到相关知识，且大模型未配置。", "rag"

        system_prompt = (
            "你是国有企业法务合规助手。当前问题在知识库中未找到高相关度案例，"
            "请你基于通用法律知识作答，并明确提示本答复未引用知识库案例，"
            "建议后续提交法务获取正式意见。\n"
            "回答要求：\n"
            "1. 结构清晰，分四段：法律解答、合规风险、实操建议、相关法条；\n"
            "2. 语言专业但通俗易懂；\n"
            "3. 不要编造具体法条编号，如不确定请说明；\n"
            "4. 开头标注【本答复未引用知识库案例】。"
        )
        user_prompt = f"业务咨询：{query}"
        if extra_context:
            user_prompt += f"\n\n附加上下文（用户上传材料）：\n{extra_context[:2000]}"

        try:
            answer = self.llm.chat(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user_prompt}],
                temperature=self.llm.temperature,
                max_tokens=self.llm.max_tokens,
            )
            if answer and answer.strip():
                return answer.strip(), "llm-only"
            return fallback or "未检索到相关知识。", "rag"
        except LlmError as e:
            logger.warning("纯 LLM 回答失败，回退：%s", e)
            return fallback or "未检索到相关知识。", "rag"

    def _generate_with_llm(self, query: str, items: List[Dict],
                            relevance: str = "high",
                            extra_context: str = "") -> str:
        """用大模型基于检索到的知识生成回复。

        高相关度：严格依据知识库，标注原文来源。
        中相关度：参考知识库 + 通用知识，提示参考。
        """
        # 拼接知识库上下文
        context_parts = []
        for i, it in enumerate(items, 1):
            context_parts.append(
                f"【案例{i}】（id={it.get('id', '')}）\n"
                f"问题：{it.get('question', '')}\n"
                f"法律解答：{it.get('legal_answer', '')}\n"
                f"合规风险：{it.get('compliance_risk', '')}\n"
                f"实操建议：{it.get('practical_advice', '')}\n"
                f"相关法条：{it.get('legal_basis', '')}"
            )
        context = "\n\n".join(context_parts)

        if relevance == "high":
            system_prompt = (
                "你是国有企业法务合规助手。请严格依据下方提供的知识库案例回答业务咨询，"
                "不得编造知识库外的法律结论。\n"
                "回答要求：\n"
                "1. 结构清晰，分四段：法律解答、合规风险、实操建议、相关法条；\n"
                "2. 引用知识库案例时，标注【参考案例N】（N 为案例编号）；\n"
                "3. 语言专业但通俗易懂，适合业务人员理解；\n"
                "4. 若知识库案例不能完全覆盖用户问题，明确指出需进一步咨询法务；\n"
                "5. 在回复末尾列出「引用的知识库案例」清单，含案例 id 与问题摘要。"
            )
        else:  # mid
            system_prompt = (
                "你是国有企业法务合规助手。下方知识库案例与用户问题相关度中等，"
                "请参考这些案例并结合通用法律知识作答。\n"
                "回答要求：\n"
                "1. 结构清晰，分四段：法律解答、合规风险、实操建议、相关法条；\n"
                "2. 引用知识库案例时标注【参考案例N】；\n"
                "3. 明确区分哪些内容来自知识库、哪些来自通用知识；\n"
                "4. 提示用户本问题与知识库相关度中等，建议提交法务确认；\n"
                "5. 不要编造具体法条编号。"
            )

        user_prompt = f"知识库参考案例：\n{context}\n\n业务咨询：{query}"
        if extra_context:
            user_prompt += (
                f"\n\n附加上下文（用户上传材料）：\n{extra_context[:2000]}"
            )
        user_prompt += "\n\n请基于上述信息给出四段式法务意见。"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.llm.chat(
            messages,
            temperature=self.llm.temperature,
            max_tokens=self.llm.max_tokens,
        )

    # ---------- 用户反馈 ----------

    def record_feedback(self, faq_id: str, relevant: bool) -> None:
        """记录用户对某条知识库引用的反馈（相关/不相关）。

        系统可基于这类标注自动优化检索结果（调整打分）。
        """
        if faq_id not in self.feedback:
            self.feedback[faq_id] = {"relevant": 0, "irrelevant": 0}
        key = "relevant" if relevant else "irrelevant"
        self.feedback[faq_id][key] += 1
        logger.info("反馈记录 faq_id=%s relevant=%s", faq_id, relevant)

    def feedback_stats(self) -> Dict:
        """返回反馈统计。"""
        return self.feedback

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
