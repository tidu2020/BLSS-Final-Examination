# -*- coding: utf-8 -*-
from .llm_client import LLMClient, estimate_tokens, ContextLengthExceededError
from .extractor import ContractExtractor
from .case_matcher import CaseCauseMatcher
from .legal_searcher import LegalSearcher
from .reviewer import ContractReviewer
from .report_generator import ReportGenerator
from .models import (
    ReviewReport, DepartmentReviewResult, ActionItems,
    ClauseReview, StructureOptimization, KnowledgeBase,
    LawVerification, BusinessPitfall, LegalIssue,
)
from ..config import AppConfig
from typing import Optional, List, Tuple, Callable
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import time
import logging

logger = logging.getLogger(__name__)


def split_contract(text: str, max_chars: int = 12000) -> List[str]:
    clause_pattern = re.compile(r'(第[一二三四五六七八九十百千\d]+条[^.。\n]*)')
    parts = clause_pattern.split(text)

    chunks = []
    current = ""
    header = ""

    for i, part in enumerate(parts):
        if clause_pattern.match(part):
            if current and len(current) > max_chars * 0.8:
                chunks.append(current.strip())
                current = header
            current += part
        else:
            if not header and not any(clause_pattern.match(p) for p in parts[:i]):
                header = part
                current = part
            else:
                current += part

    if current.strip():
        chunks.append(current.strip())

    if not chunks:
        paragraphs = text.split("\n\n")
        current = ""
        for para in paragraphs:
            if len(current) + len(para) > max_chars and current:
                chunks.append(current.strip())
                current = para
            else:
                current += "\n\n" + para if current else para
        if current.strip():
            chunks.append(current.strip())

    if not chunks:
        chunks = [text]

    return chunks


class ContractReviewPipeline:
    def __init__(self, config: Optional[AppConfig] = None, step_callback: Optional[Callable] = None):
        self.config = config or AppConfig()
        self.llm = LLMClient(self.config.llm)
        self.extractor = ContractExtractor(self.llm)
        self.case_matcher = CaseCauseMatcher(self.llm, self.config.data_dir)
        self.searcher = LegalSearcher(self.llm, self.config.search)
        self.reviewer = ContractReviewer(self.llm)
        self.report_gen = ReportGenerator()
        self.step_callback = step_callback
        self._stage_timings = {}

    def _timed_emit(self, pct: int, text: str, stage_start: float = None):
        if stage_start and self.step_callback:
            elapsed = time.monotonic() - stage_start
            self._stage_timings[text] = elapsed
        if self.step_callback:
            self.step_callback(pct, text)

    def _should_chunk(self, contract_text: str, system_prompt: str) -> bool:
        estimated = estimate_tokens(contract_text) + estimate_tokens(system_prompt)
        limit = int(self.config.llm.context_limit * self.config.llm.token_safety_ratio)
        threshold = int(limit * 0.75)
        return estimated > threshold

    def _chunked_review(self, contract_text, contract_info, case_cause,
                        review_stance, legal_references, department="法务"):
        chunks = split_contract(contract_text, self.config.llm.max_chunk_chars)
        chunk_total = len(chunks)

        all_results = []
        failed_chunks = []

        for idx, chunk in enumerate(chunks, 1):
            try:
                result = self.reviewer.review_chunk(
                    chunk_text=chunk, chunk_index=idx, chunk_total=chunk_total,
                    contract_info=contract_info, case_cause=case_cause,
                    review_stance=review_stance, legal_references=legal_references,
                    department=department,
                )
                all_results.append(result)
            except ContextLengthExceededError:
                logger.warning(f"chunk {idx}/{chunk_total} too large, splitting further")
                sub_chunks = split_contract(chunk, max(int(self.config.llm.max_chunk_chars * 0.6), 3000))
                for sub_idx, sub_chunk in enumerate(sub_chunks):
                    try:
                        sub_result = self.reviewer.review_chunk(
                            chunk_text=sub_chunk, chunk_index=f"{idx}.{sub_idx+1}",
                            chunk_total=chunk_total, contract_info=contract_info,
                            case_cause=case_cause, review_stance=review_stance,
                            legal_references=legal_references, department=department,
                        )
                        all_results.append(sub_result)
                    except Exception:
                        failed_chunks.append(idx)
            except Exception as e:
                failed_chunks.append(idx)

        if not all_results:
            return [], [], [], [f"{department} chunk review all failed"], []

        merged = self.reviewer.merge_chunk_results(
            all_results, contract_info, case_cause, review_stance
        )
        return merged

    def _emit(self, pct, text):
        if self.step_callback:
            self.step_callback(pct, text)

    def _build_knowledge_base(self, contract_type, case_cause):
        knowledge_base = KnowledgeBase()
        search_kw = []
        search_ok = False

        def _fetch_knowledge():
            try:
                return self.reviewer.knowledge_retrieval(
                    contract_type, case_cause.full_path
                )
            except Exception as e:
                logger.warning(f"knowledge retrieval failed: {str(e)[:200]}")
                return KnowledgeBase()

        def _fetch_search():
            try:
                review_focus = ["违约金上限", "合同解除条件", "违约责任", "格式条款", "争议解决"]
                return self.searcher.search(
                    contract_type, case_cause.full_path, review_focus,
                )
            except Exception as e:
                logger.warning(f"external legal search failed: {str(e)[:200]}")
                return [], [], False

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_kb = executor.submit(_fetch_knowledge)
            future_search = executor.submit(_fetch_search)

            for future in as_completed([future_kb, future_search]):
                if future == future_kb:
                    try:
                        knowledge_base = future.result()
                    except Exception as e:
                        logger.warning(f"knowledge retrieval future failed: {str(e)[:200]}")
                else:
                    try:
                        legal_refs, search_kw, search_ok = future.result()
                        if knowledge_base:
                            knowledge_base.external_refs = legal_refs
                    except Exception as e:
                        logger.warning(f"search future failed: {str(e)[:200]}")

        return knowledge_base, search_kw, search_ok

    def _safe_business_review(self, contract_text, contract_info, case_cause,
                              review_stance, knowledge_base, focus_text):
        try:
            result = self.reviewer.review_business(
                contract_text, contract_info, case_cause,
                review_stance, knowledge_base, focus_text,
            )
            return True, *result
        except Exception as e:
            logger.warning(f"business review failed: {str(e)[:200]}")
            return False, [], [], [], [f"business review failed: {str(e)[:200]}"], []

    def _safe_legal_review(self, contract_text, contract_info, case_cause,
                           review_stance, legal_refs, knowledge_base, focus_text):
        from ..prompts.review import DEPT_LEGAL_SYSTEM_PROMPT

        try:
            use_chunking = self._should_chunk(
                contract_text, DEPT_LEGAL_SYSTEM_PROMPT.format(review_stance=review_stance)
            )
            if use_chunking:
                result = self._chunked_review(
                    contract_text, contract_info, case_cause,
                    review_stance, legal_refs, "法务",
                )
            else:
                result = self.reviewer.review_legal(
                    contract_text, contract_info, case_cause,
                    review_stance, legal_refs, knowledge_base, focus_text,
                )
            return True, *result
        except Exception as e:
            logger.warning(f"legal review failed: {str(e)[:200]}")
            return False, [], [], [], [f"legal review failed: {str(e)[:200]}"], []

    def _run_law_verification(self, legal_clause_reviews):
        if not legal_clause_reviews:
            return []

        try:
            return self.reviewer.verify_laws(legal_clause_reviews, enable_search=True)
        except Exception as e:
            logger.warning(f"law verification failed: {str(e)[:200]}")
            return []

    def run(self, contract_text, special_focus="", user_stance="", contract_context=""):
        all_search_kw = []

        focus_text = special_focus or ""
        if contract_context:
            focus_text = f"{special_focus}\n合同背景：{contract_context}" if special_focus else f"合同背景：{contract_context}"

        self._emit(5, "阶段1/6: 正在识别合同类型与基本信息...")

        contract_info = self.extractor.extract(contract_text)

        if user_stance and user_stance != "auto":
            review_stance = user_stance
        else:
            review_stance = contract_info.inferred_party or "无法推断"

        case_cause = self.case_matcher.match(
            contract_info.contract_type, contract_info.type_keywords,
        )

        self._emit(10, "阶段1/6: 合同信息提取完成")

        self._emit(10, "阶段2/6: 正在检索合同类型相关知识...")

        knowledge_base, search_kw, search_ok = self._build_knowledge_base(
            contract_info.contract_type, case_cause,
        )
        all_search_kw.extend(search_kw)
        knowledge_failed = not knowledge_base

        self._emit(20, "阶段2/6: 知识检索完成")

        self._emit(20, "阶段3/6: 正在进行商业条款审核...")

        business_completed, b_clauses, b_struct, b_actions, b_notes, b_kw = \
            self._safe_business_review(
                contract_text, contract_info, case_cause,
                review_stance, knowledge_base, focus_text,
            )
        business_review_result = DepartmentReviewResult(
            department="商务部", review_time=datetime.now().isoformat(),
            status="completed" if business_completed else "failed",
            clause_reviews=b_clauses, structure_optimizations=b_struct,
            action_items=b_actions, supplementary_notes=b_notes,
        )
        all_search_kw.extend(b_kw)

        self._emit(35, "阶段3/6: 商业审核完成")

        self._emit(35, "阶段4/6: 正在进行法律深度审核...")

        legal_focus_text = focus_text
        if business_completed and b_clauses:
            risks = [
                f"{c.clause_title}[{c.risk_level}]"
                for c in b_clauses
                if c.risk_level in ("🔴高风险", "🟡中风险")
            ]
            if risks:
                legal_focus_text += f"\n商业审核发现的待关注风险点：{'; '.join(risks)}"

        legal_refs = knowledge_base.external_refs if knowledge_base else []

        legal_completed, l_clauses, l_struct, l_actions, l_notes, l_kw = \
            self._safe_legal_review(
                contract_text, contract_info, case_cause,
                review_stance, legal_refs, knowledge_base, legal_focus_text,
            )
        legal_review_result = DepartmentReviewResult(
            department="法务部", review_time=datetime.now().isoformat(),
            status="completed" if legal_completed else "failed",
            clause_reviews=l_clauses, structure_optimizations=l_struct,
            action_items=l_actions, supplementary_notes=l_notes,
        )
        all_search_kw.extend(l_kw)

        self._emit(75, "阶段4/6: 法律深度审核完成")

        self._emit(75, "阶段5/6: 正在进行法条真实性核验...")

        law_verifications = []
        if legal_completed and l_clauses:
            law_verifications = self._run_law_verification(l_clauses)

        self._emit(90, "阶段5/6: 法条核验完成")

        self._emit(90, "阶段6/6: 正在生成综合审核报告...")

        if legal_completed and business_completed and not knowledge_failed:
            overall_status = "completed"
        elif legal_completed or business_completed:
            overall_status = "partial"
        else:
            overall_status = "failed"

        report = ReviewReport(
            search_enabled=search_ok,
            contract_type=contract_info.contract_type,
            case_cause=case_cause,
            review_stance=review_stance,
            contract_info=contract_info,
            legal_review=legal_review_result,
            business_review=business_review_result,
            overall_status=overall_status,
            action_items=ActionItems(
                legal_actions=legal_review_result.action_items,
                business_actions=business_review_result.action_items,
            ),
            supplementary_notes=legal_review_result.supplementary_notes + business_review_result.supplementary_notes,
            search_keywords=list(set(all_search_kw)),
            knowledge_base=knowledge_base if knowledge_base else None,
            law_verifications=law_verifications,
        )

        self._emit(100, "审核完成")
        return self.report_gen.generate_markdown(report)
