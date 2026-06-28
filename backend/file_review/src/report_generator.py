from .models import ReviewReport, ClauseReview, KnowledgeBase, LawVerification
from datetime import datetime
from typing import List


def _risk_sort_key(c: ClauseReview) -> int:
    if "🔴" in c.risk_level:
        return 0
    if "🟡" in c.risk_level:
        return 1
    return 2


class ReportGenerator:
    def generate_markdown(self, report: ReviewReport) -> str:
        lines: List[str] = []

        lines.append("# 合同审核报告")
        lines.append("")

        lines.append(f"**生成时间**：{report.generated_at}　|　**合同类型**：{report.contract_type}　|　**审核立场**：{report.review_stance}")
        lines.append(f"**匹配案由**：{report.case_cause.full_path}")
        lines.append("")

        if report.overall_status == "partial":
            lines.append("> ⚠️ 部分审核未完成。")
            lines.append("")
        elif report.overall_status == "failed":
            lines.append("> ❌ 审核失败，请重试。")
            return "\n".join(lines)

        self._chapter1_contract_summary(lines, report)
        self._chapter2_knowledge_summary(lines, report)
        self._chapter3_business_review(lines, report)
        self._chapter4_legal_deep_review(lines, report)
        self._chapter5_risk_summary(lines, report)
        self._chapter6_law_verifications(lines, report)
        self._chapter7_action_items(lines, report)

        lines.append("---")
        lines.append("*本报告由AI生成，不构成正式法律意见。如有疑问请咨询专业律师。*")

        return "\n".join(lines)

    def save_report(self, report: ReviewReport, output_dir: str = "output") -> str:
        import os
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"合同审核报告_{timestamp}.md"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.generate_markdown(report))
        return filepath

    # ==================== 一、合同概要 ====================

    def _chapter1_contract_summary(self, lines: List[str], report: ReviewReport) -> None:
        info = report.contract_info
        lines.append("## 一、合同概要")
        lines.append("")
        lines.append("| 字段 | 内容 |")
        lines.append("|------|------|")
        lines.append(f"| 合同名称 | {info.contract_name} |")
        lines.append(f"| 合同类型 | {info.contract_type} |")
        lines.append(f"| 甲方 | {info.party_a.name} |")
        lines.append(f"| 乙方 | {info.party_b.name} |")
        lines.append(f"| 交易标的 | {info.subject_matter} |")
        lines.append(f"| 价款/对价 | {info.price} |")
        lines.append(f"| 支付方式 | {info.payment_method} |")
        lines.append("")
        lines.append(f"**案由匹配结果**：{report.case_cause.full_path}")
        lines.append(f"**审核立场**：{report.review_stance}")
        lines.append("")

    # ==================== 二、知识检索摘要 ====================

    def _chapter2_knowledge_summary(self, lines: List[str], report: ReviewReport) -> None:
        lines.append("## 二、知识检索摘要")
        lines.append("")

        kb = report.knowledge_base
        if kb is None:
            lines.append("知识检索未执行。")
            lines.append("")
            return

        pitfalls = kb.business_pitfalls
        if not pitfalls:
            lines.append("**商业陷阱**：未发现常见商业陷阱。")
        else:
            lines.append("### 常见商业陷阱")
            lines.append("")
            for idx, bp in enumerate(pitfalls[:5], 1):
                lines.append(f"{idx}. **{bp.category}**：{bp.description}")
            lines.append("")

        legal_issues = kb.legal_issues
        if not legal_issues:
            lines.append("**法律争议点**：未发现常见法律争议点。")
        else:
            lines.append("### 常见法律争议点")
            lines.append("")
            for idx, li in enumerate(legal_issues[:5], 1):
                laws_text = f"（涉及法条：{li.related_laws}）" if li.related_laws else ""
                lines.append(f"{idx}. **{li.category}**：{li.description}{laws_text}")
            lines.append("")

    # ==================== 三、商业审核 ====================

    def _chapter3_business_review(self, lines: List[str], report: ReviewReport) -> None:
        lines.append("## 三、商业审核")
        lines.append("")

        biz = report.business_review
        if biz.status == "failed":
            lines.append("商业审核失败。")
            lines.append("")
            return

        clause_reviews = biz.clause_reviews
        if not clause_reviews:
            lines.append("未发现商业风险条款。")
            lines.append("")
            return

        risk_clauses = [
            c for c in clause_reviews
            if "业务致命伤" not in c.risk_level and "可接受" not in c.risk_level
        ]

        if not risk_clauses:
            lines.append("未发现需关注的商业风险条款。")
            lines.append("")
            return

        for idx, c in enumerate(risk_clauses, 1):
            lines.append(f"**{idx}. {c.clause_title}　|　{c.risk_level}**")
            lines.append("")

            if c.problem_analysis:
                lines.append(f"- **问题分析**：{c.problem_analysis}")

            if c.negotiation_priority:
                lines.append(f"- **谈判策略**：{c.negotiation_priority}")

            if c.suggested_revision:
                lines.append(f"- **修改建议**：{c.suggested_revision}")

            if c.revision_reason:
                lines.append(f"- **修改理由**：{c.revision_reason}")

            lines.append("")

    # ==================== 四、法律深度审核 ====================

    def _chapter4_legal_deep_review(self, lines: List[str], report: ReviewReport) -> None:
        lines.append("## 四、法律深度审核")
        lines.append("")

        legal = report.legal_review
        if legal.status == "failed":
            lines.append("法律审核失败。")
            lines.append("")
            return

        clause_reviews = legal.clause_reviews
        if not clause_reviews:
            lines.append("法律审核结果为空。")
            lines.append("")
            return

        sorted_clauses = sorted(clause_reviews, key=_risk_sort_key)

        for idx, c in enumerate(sorted_clauses, 1):
            lines.append(f"**{idx}. {c.clause_title}　|　{c.risk_level}**")
            lines.append("")

            if c.risk_type:
                lines.append(f"- **风险定性**：{c.risk_type}")

            if c.legal_basis:
                lines.append(f"- **法律依据**：")
                for lb in c.legal_basis:
                    status_text = "✅已验证" if lb.verified else "⚠️需核查"
                    lines.append(f"  - {lb.article}：{lb.content}（{status_text}）")

            if c.actual_impact:
                lines.append(f"- **实际影响**：{c.actual_impact}")

            if c.suggested_revision:
                lines.append(f"- **修改方案**：{c.suggested_revision}")
                if c.revision_reason:
                    lines.append(f"  - 修改理由：{c.revision_reason}")

            if c.problem_analysis:
                lines.append(f"- **问题分析**：{c.problem_analysis}")

            if c.original_text:
                lines.append(f"- **原始文本摘要**：{c.original_text}")

            lines.append("")

    # ==================== 五、风险汇总 ====================

    def _chapter5_risk_summary(self, lines: List[str], report: ReviewReport) -> None:
        lines.append("## 五、风险汇总")
        lines.append("")

        legal_clauses = report.legal_review.clause_reviews

        high = [c for c in legal_clauses if "🔴" in c.risk_level]
        mid = [c for c in legal_clauses if "🟡" in c.risk_level]

        if high:
            lines.append("### 🔴 高风险条款（必须修改）")
            lines.append("")
            for c in high:
                lines.append(f"- **{c.clause_title}**：{c.problem_analysis or '详见法律深度审核'}")
            lines.append("")

        if mid:
            lines.append("### 🟡 中风险条款（建议修改）")
            lines.append("")
            for c in mid:
                lines.append(f"- **{c.clause_title}**：{c.problem_analysis or '详见法律深度审核'}")
            lines.append("")

        if not high and not mid:
            lines.append("未发现高或中风险条款。")
            lines.append("")

    # ==================== 六、法条核验结果 ====================

    def _chapter6_law_verifications(self, lines: List[str], report: ReviewReport) -> None:
        lines.append("## 六、法条核验结果")
        lines.append("")

        verifications = report.law_verifications

        if not verifications:
            legal_clauses = report.legal_review.clause_reviews
            has_legal_basis = any(
                c.legal_basis for c in legal_clauses
            ) if legal_clauses else False

            if not has_legal_basis:
                lines.append("未引用法条。")
            else:
                lines.append("本次审核无需核验法条。")
            lines.append("")
            return

        lines.append("| 法条编号 | 核验状态 | 说明 |")
        lines.append("|----------|----------|------|")
        for v in verifications:
            status_display = v.status or "未核验"
            reason = v.reason
            if v.correct_article:
                reason += f"（正确法条：{v.correct_article}）"
            lines.append(f"| {v.article} | {status_display} | {reason} |")
        lines.append("")

    # ==================== 七、行动清单 ====================

    def _chapter7_action_items(self, lines: List[str], report: ReviewReport) -> None:
        lines.append("## 七、行动清单")
        lines.append("")

        actions = report.action_items.legal_actions + report.action_items.business_actions
        actions = list(dict.fromkeys(actions))

        if actions:
            for a in actions:
                lines.append(f"- [ ] {a}")
            lines.append("")
        else:
            lines.append("暂无需要执行的操作项。")
            lines.append("")

        notes = report.supplementary_notes
        if notes:
            notes = list(dict.fromkeys(notes))
            lines.append("### 补充提示")
            lines.append("")
            for n in notes:
                lines.append(f"- {n}")
            lines.append("")