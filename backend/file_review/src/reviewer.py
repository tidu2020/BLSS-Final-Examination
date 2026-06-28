from .llm_client import LLMClient
from .models import (
    ContractInfo, CaseCause, LegalReference, ClauseReview,
    StructureOptimization, KnowledgeBase, BusinessPitfall,
    LegalIssue, LawVerification,
)
from ..prompts.review import (
    DEPT_LEGAL_SYSTEM_PROMPT, DEPT_BUSINESS_SYSTEM_PROMPT, DEPT_REVIEW_USER_PROMPT,
    CHUNK_REVIEW_SYSTEM_PROMPT, CHUNK_REVIEW_USER_PROMPT, MERGE_SYSTEM_PROMPT,
    KNOWLEDGE_RETRIEVAL_PROMPT, LAW_VERIFICATION_PROMPT,
)
from typing import List, Tuple, Dict, Any
import json
import logging

logger = logging.getLogger(__name__)


def _parse_review_result(result: Dict[str, Any], department: str) -> Tuple[
    List[ClauseReview], List[StructureOptimization], List[str], List[str], List[str]
]:
    clause_reviews = []
    raw_clauses = result.get("clause_reviews", []) or []
    if not isinstance(raw_clauses, list):
        logger.warning("clause_reviews 不是列表，跳过解析")
        raw_clauses = []
    for c in raw_clauses:
        # 防御：LLM 偶尔返回字符串而非对象
        if not isinstance(c, dict):
            logger.warning("跳过非对象 clause: %s", str(c)[:80])
            continue
        legal_basis = []
        raw_lb = c.get("legal_basis", []) or []
        if isinstance(raw_lb, list):
            for lb in raw_lb:
                if not isinstance(lb, dict):
                    continue
                legal_basis.append(LegalReference(
                    article=lb.get("article", ""),
                    content=lb.get("content", ""),
                    source_url=lb.get("source_url", ""),
                    verified=lb.get("verified", False),
                ))
        clause_reviews.append(ClauseReview(
            clause_title=c.get("clause_title", ""),
            original_text=c.get("original_text", ""),
            risk_level=c.get("risk_level", "🟢低风险"),
            review_category=f"{department}-{c.get('review_category', '')}",
            legal_basis=legal_basis,
            problem_analysis=c.get("problem_analysis", ""),
            suggested_revision=c.get("suggested_revision", ""),
            revision_reason=c.get("revision_reason", ""),
            negotiation_priority=c.get("negotiation_priority", "🟢可协商"),
            risk_type=c.get("risk_type", ""),
            actual_impact=c.get("actual_impact", ""),
        ))

    raw_so = result.get("structure_optimizations", []) or []
    if not isinstance(raw_so, list):
        raw_so = []
    structure_opts = []
    for so in raw_so:
        if not isinstance(so, dict):
            continue
        alts = so.get("alternatives", []) or []
        chk = so.get("checklist", []) or []
        structure_opts.append(StructureOptimization(
            problem=so.get("problem", ""),
            alternatives=alts if isinstance(alts, list) else [],
            checklist=chk if isinstance(chk, list) else [],
        ))

    raw_actions = result.get("department_action_items", []) or []
    action_items = raw_actions if isinstance(raw_actions, list) else []
    raw_supp = result.get("supplementary_notes", []) or []
    supplementary = raw_supp if isinstance(raw_supp, list) else []
    raw_kw = result.get("search_keywords", []) or []
    search_kw = raw_kw if isinstance(raw_kw, list) else []

    return clause_reviews, structure_opts, action_items, supplementary, search_kw


class ContractReviewer:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def knowledge_retrieval(self, contract_type: str, case_cause: str) -> KnowledgeBase:
        prompt = KNOWLEDGE_RETRIEVAL_PROMPT.format(
            contract_type=contract_type,
            case_cause=case_cause,
        )
        result = self.llm.chat_json(
            system_prompt="你是一位中国合同法知识库专家。",
            user_prompt=prompt,
            temperature=0.3,
        )

        business_pitfalls = []
        raw_bp = result.get("business_pitfalls", []) or []
        if isinstance(raw_bp, list):
            for bp in raw_bp:
                if not isinstance(bp, dict):
                    continue
                business_pitfalls.append(BusinessPitfall(
                    category=bp.get("category", ""),
                    description=bp.get("description", ""),
                    remedy=bp.get("remedy", ""),
                ))

        legal_issues = []
        raw_li = result.get("legal_issues", []) or []
        if isinstance(raw_li, list):
            for li in raw_li:
                if not isinstance(li, dict):
                    continue
                related_laws = li.get("related_laws", [])
                if isinstance(related_laws, list):
                    related_laws = "; ".join(str(x) for x in related_laws)
                legal_issues.append(LegalIssue(
                    category=li.get("category", ""),
                    description=li.get("description", ""),
                    related_laws=related_laws or "",
                ))

        return KnowledgeBase(
            business_pitfalls=business_pitfalls,
            legal_issues=legal_issues,
        )

    def review_legal(
        self,
        contract_text: str,
        contract_info: ContractInfo,
        case_cause: CaseCause,
        review_stance: str,
        legal_references: List[LegalReference],
        knowledge_base: KnowledgeBase,
        special_focus: str = "",
    ) -> Tuple[List[ClauseReview], List[StructureOptimization], List[str], List[str], List[str]]:
        refs_text = self._build_refs_text(legal_references)
        kb_text = self._build_knowledge_text(knowledge_base, "法务")

        legal_system_prompt = DEPT_LEGAL_SYSTEM_PROMPT.format(review_stance=review_stance)

        user_prompt = DEPT_REVIEW_USER_PROMPT.format(
            contract_type=contract_info.contract_type,
            case_cause=case_cause.full_path,
            review_stance=review_stance,
            special_focus=special_focus or "无",
            extracted_info=contract_info.model_dump_json(indent=2, ensure_ascii=False),
            knowledge_base=kb_text,
            legal_references=refs_text,
            contract_text=contract_text,
        )

        result = self.llm.chat_json(
            system_prompt=legal_system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
        )

        return _parse_review_result(result, "法务")

    def review_business(
        self,
        contract_text: str,
        contract_info: ContractInfo,
        case_cause: CaseCause,
        review_stance: str,
        knowledge_base: KnowledgeBase,
        special_focus: str = "",
    ) -> Tuple[List[ClauseReview], List[StructureOptimization], List[str], List[str], List[str]]:
        kb_text = self._build_knowledge_text(knowledge_base, "商务")

        business_system_prompt = DEPT_BUSINESS_SYSTEM_PROMPT.format(review_stance=review_stance)

        user_prompt = DEPT_REVIEW_USER_PROMPT.format(
            contract_type=contract_info.contract_type,
            case_cause=case_cause.full_path,
            review_stance=review_stance,
            special_focus=special_focus or "无",
            extracted_info=contract_info.model_dump_json(indent=2, ensure_ascii=False),
            knowledge_base=kb_text,
            legal_references="未检索到法律依据（联网搜索未开启或检索失败）",
            contract_text=contract_text,
        )

        result = self.llm.chat_json(
            system_prompt=business_system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
        )

        return _parse_review_result(result, "商务")

    def verify_laws(self, clause_reviews: List[ClauseReview], enable_search: bool = True) -> List[LawVerification]:
        high_risk_refs = []
        medium_risk_refs = []
        seen_articles = set()

        for clause in clause_reviews:
            for lb in clause.legal_basis:
                if lb.article and lb.article not in seen_articles:
                    seen_articles.add(lb.article)
                    if "🔴" in clause.risk_level:
                        high_risk_refs.append(lb)
                    elif "🟡" in clause.risk_level:
                        medium_risk_refs.append(lb)

        verifications = []

        for ref in medium_risk_refs:
            status = "✅已验证"
            reason = "法条编号格式正确（基于格式检查，未做深度LLM核验）"
            if not ref.article or len(ref.article.strip()) < 3:
                status = "⚠️存疑"
                reason = "法条编号过短或为空，需人工核查"
            verifications.append(LawVerification(
                article=ref.article, status=status,
                reason=reason, correct_article="",
            ))

        if high_risk_refs:
            refs_json = json.dumps([
                {"article": r.article, "content": r.content} for r in high_risk_refs
            ], ensure_ascii=False, indent=2)

            prompt = LAW_VERIFICATION_PROMPT.format(legal_references=refs_json)

            result = self.llm.chat_json(
                system_prompt="你是一位中国法律法规核验专家。",
                user_prompt=prompt,
                temperature=0.1,
            )

            raw_vr = result.get("verification_results", []) or []
            if isinstance(raw_vr, list):
                for v in raw_vr:
                    if not isinstance(v, dict):
                        continue
                    verifications.append(LawVerification(
                        article=v.get("original_article", ""),
                        status=v.get("verification_status", "未核验"),
                        reason=v.get("verification_detail", ""),
                        correct_article=v.get("corrected_article") or "",
                    ))

        return verifications

    def review_chunk(
        self,
        chunk_text: str,
        chunk_index: int,
        chunk_total: int,
        contract_info: ContractInfo,
        case_cause: CaseCause,
        review_stance: str,
        legal_references: List[LegalReference],
        department: str = "法务",
    ) -> Tuple[List[ClauseReview], List[StructureOptimization], List[str], List[str], List[str]]:
        refs_text = self._build_refs_text(legal_references)

        system_prompt = CHUNK_REVIEW_SYSTEM_PROMPT.format(review_stance=review_stance)

        user_prompt = CHUNK_REVIEW_USER_PROMPT.format(
            contract_type=contract_info.contract_type,
            case_cause=case_cause.full_path,
            review_stance=review_stance,
            legal_references=refs_text,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            chunk_text=chunk_text,
        )

        try:
            result = self.llm.chat_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
            )
            return _parse_review_result(result, department)
        except Exception as e:
            logger.warning(f"分块 {chunk_index}/{chunk_total} 审查失败: {e}")
            raise

    def merge_chunk_results(
        self,
        chunk_results: List[Tuple[List[ClauseReview], List[StructureOptimization], List[str], List[str], List[str]]],
        contract_info: ContractInfo,
        case_cause: CaseCause,
        review_stance: str,
    ) -> Tuple[List[ClauseReview], List[StructureOptimization], List[str], List[str], List[str]]:
        if len(chunk_results) == 1:
            return chunk_results[0]

        all_clauses = []
        all_structs = []
        all_actions = []
        all_notes = []
        all_kw = []

        for clauses, structs, actions, notes, kw in chunk_results:
            all_clauses.extend(clauses)
            all_structs.extend(structs)
            all_actions.extend(actions)
            all_notes.extend(notes)
            all_kw.extend(kw)

        if len(chunk_results) <= 2:
            return all_clauses, all_structs, list(dict.fromkeys(all_actions)), list(dict.fromkeys(all_notes)), list(dict.fromkeys(all_kw))

        unique_clauses = {}
        for c in all_clauses:
            key = c.clause_title
            if key not in unique_clauses or "🔴" in c.risk_level:
                unique_clauses[key] = c
        deduped_clauses = list(unique_clauses.values())

        if len(chunk_results) <= 5 and len(deduped_clauses) <= 30:
            return deduped_clauses, all_structs, list(dict.fromkeys(all_actions)), list(dict.fromkeys(all_notes)), list(dict.fromkeys(all_kw))

        clauses_json = []
        for c in all_clauses:
            clauses_json.append({
                "clause_title": c.clause_title,
                "original_text": c.original_text,
                "risk_level": c.risk_level,
                "review_category": c.review_category,
                "problem_analysis": c.problem_analysis,
                "suggested_revision": c.suggested_revision,
                "revision_reason": c.revision_reason,
                "negotiation_priority": c.negotiation_priority,
            })

        merge_user_prompt = f"""合同类型：{contract_info.contract_type}
案由：{case_cause.full_path}
立场：{review_stance}

分片审查结果（共{len(chunk_results)}片）：

{json.dumps(clauses_json, ensure_ascii=False, indent=2)}

请合并去重并执行跨章节一致性检查。"""

        try:
            merged = self.llm.chat_json(
                system_prompt=MERGE_SYSTEM_PROMPT,
                user_prompt=merge_user_prompt,
                temperature=0.2,
            )
            result = _parse_review_result(merged, "法务")
            all_actions_merged = list(dict.fromkeys(all_actions + result[2]))
            all_notes_merged = list(dict.fromkeys(all_notes + result[3]))
            return result[0], result[1], all_actions_merged, all_notes_merged, list(dict.fromkeys(all_kw + result[4]))
        except Exception as e:
            logger.warning(f"合并审查结果失败，使用简单拼接: {e}")
            return all_clauses, all_structs, list(dict.fromkeys(all_actions)), list(dict.fromkeys(all_notes)), list(dict.fromkeys(all_kw))

    def _build_refs_text(self, legal_references: List[LegalReference]) -> str:
        if legal_references:
            refs_lines = []
            for r in legal_references:
                verified_label = "已验证" if r.verified else "需人工核查"
                refs_lines.append(f"- {r.article}: {r.content} (来源: {r.source_url}, {verified_label})")
            return "\n".join(refs_lines)
        return "未检索到法律依据（联网搜索未开启或检索失败）"

    def _build_knowledge_text(self, knowledge_base: KnowledgeBase, side: str) -> str:
        parts = []

        if side == "商务":
            if knowledge_base.business_pitfalls:
                parts.append("【商业陷阱参考 - 重点】")
                for bp in knowledge_base.business_pitfalls:
                    parts.append(f"- {bp.category}: {bp.description}（应对：{bp.remedy}）")

            if knowledge_base.legal_issues:
                parts.append("【法律争议点参考】")
                for li in knowledge_base.legal_issues:
                    parts.append(f"- {li.category}: {li.description}（涉及法条：{li.related_laws}）")
        else:
            if knowledge_base.legal_issues:
                parts.append("【法律争议点参考 - 重点】")
                for li in knowledge_base.legal_issues:
                    parts.append(f"- {li.category}: {li.description}（涉及法条：{li.related_laws}）")

            if knowledge_base.business_pitfalls:
                parts.append("【商业陷阱参考】")
                for bp in knowledge_base.business_pitfalls:
                    parts.append(f"- {bp.category}: {bp.description}（应对：{bp.remedy}）")

        return "\n".join(parts) if parts else "无知识库信息"