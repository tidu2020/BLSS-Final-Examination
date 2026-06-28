"""report 路由：报告生成。

接口：
- POST /api/report/monthly_summary  生成月度总结报告
- POST /api/report/risk_scan          扫描近期风险
- POST /api/report/annual_risk       生成年度风险报告
- GET  /api/report/templates          模板列表
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List

from flask import Blueprint, jsonify, request

from backend.auth.decorator import require_role, require_login
from backend.models.workflow import WorkOrderStore
from backend.knowledge.models import KnowledgeBase

bp = Blueprint("report", __name__, url_prefix="/api/report")

# 由 app 注入
work_order_store: WorkOrderStore = None  # type: ignore
knowledge_base: KnowledgeBase = None  # type: ignore


def init_dependencies(orders: WorkOrderStore, kb: KnowledgeBase) -> None:
    global work_order_store, knowledge_base
    work_order_store = orders
    knowledge_base = kb


def _parse_month(s: str) -> str:
    """提取 YYYYMM 形式月份。"""
    if not s:
        return ""
    # 兼容 ISO 时间戳 2024-01-15T... 或 YYYYMM
    s = s[:7].replace("-", "")
    return s if len(s) == 6 else ""


@bp.route("/monthly_summary", methods=["POST"])
@require_role("legal", "admin")
def monthly_summary():
    """生成月度总结报告。

    请求体：{"month": "202507"}
    若省略，自动取最近一个有工单的月份。
    """
    data = request.get_json() or {}
    month = data.get("month")

    all_orders = work_order_store.query()
    if not month:
        # 自动取最近月份
        months = sorted({_parse_month(o.submitted_at) for o in all_orders
                         if _parse_month(o.submitted_at)}, reverse=True)
        if not months:
            return jsonify({"error": "无工单数据"}), 400
        month = months[0]

    # 过滤本月工单
    month_orders = [o for o in all_orders
                    if _parse_month(o.submitted_at) == month]

    # 统计
    status_breakdown = Counter(o.status for o in month_orders)
    submitter_breakdown = Counter(o.submitter for o in month_orders)
    new_faqs = [it for it in knowledge_base.items
                if _parse_month(it.get("created_at", "")) == month
                or it.get("month") == month]

    # 按工单状态聚合简报
    report_lines = [
        f"# {month[:4]}年{month[4:]}月法务工作月度总结",
        "",
        f"## 一、工单总览",
        f"- 本月新增工单：{len(month_orders)} 件",
        f"- 已确认入库：{status_breakdown.get('confirmed', 0)} 件",
        f"- 审核中：{status_breakdown.get('reviewing', 0)} 件",
        f"- 待处理：{status_breakdown.get('submitted_to_legal', 0)} 件",
        f"- 已归档：{status_breakdown.get('archived', 0)} 件",
        "",
        f"## 二、知识沉淀",
        f"- 本月入库 FAQ：{len(new_faqs)} 条",
    ]
    if new_faqs:
        report_lines.append("")
        report_lines.append("### 入库条目")
        for it in new_faqs[:10]:
            report_lines.append(f"- [{it['id']}] {it.get('question', '')}")

    report_lines.extend([
        "",
        f"## 三、提交人分布",
    ])
    for sub, cnt in submitter_breakdown.most_common():
        report_lines.append(f"- {sub}: {cnt} 件")

    report_lines.extend([
        "",
        f"## 四、典型工单",
    ])
    for o in month_orders[:5]:
        report_lines.append(
            f"- [{o.id}] 提交人 {o.submitter_name or o.submitter} "
            f"于 {o.submitted_at[:10]}，状态 {o.status}"
        )

    report_lines.append("")
    report_lines.append("---")
    report_lines.append(f"生成时间：{datetime.now().isoformat(timespec='seconds')}")

    return jsonify({
        "month": month,
        "order_count": len(month_orders),
        "status_breakdown": dict(status_breakdown),
        "new_faq_count": len(new_faqs),
        "report_text": "\n".join(report_lines),
    })


@bp.route("/risk_scan", methods=["POST"])
@require_role("legal", "admin")
def risk_scan():
    """扫描近期新增知识，识别风险关键词。"""
    data = request.get_json() or {}
    months = data.get("months", 1)
    # 取最近 N 个月
    all_months = sorted(knowledge_base.all_months(), reverse=True)
    target_months = set(all_months[:months])

    recent_items = [it for it in knowledge_base.items
                    if it.get("month") in target_months]

    # 风险关键词
    risk_keywords = [
        "风险", "处罚", "违规", "诉讼", "仲裁", "赔偿",
        "无效", "解除", "违约", "索赔", "争议", "瑕疵",
    ]
    risk_items = []
    for it in recent_items:
        text = (it.get("compliance_risk", "") + " "
                + it.get("legal_answer", ""))
        hit = [kw for kw in risk_keywords if kw in text]
        if hit:
            risk_items.append({
                "id": it["id"],
                "question": it.get("question", ""),
                "month": it.get("month", ""),
                "hit_keywords": hit,
                "compliance_risk": it.get("compliance_risk", "")[:120],
            })

    # 关键词统计
    keyword_counter = Counter()
    for ri in risk_items:
        keyword_counter.update(ri["hit_keywords"])

    return jsonify({
        "scanned_months": sorted(target_months, reverse=True),
        "total_recent": len(recent_items),
        "risk_count": len(risk_items),
        "keyword_breakdown": dict(keyword_counter.most_common()),
        "risk_items": risk_items,
    })


@bp.route("/annual_risk", methods=["POST"])
@require_role("legal", "admin")
def annual_risk():
    """生成年度风险报告。

    请求体：{"year": 2025}
    """
    data = request.get_json() or {}
    year = data.get("year", datetime.now().year)

    year_prefix = str(year)
    year_items = [it for it in knowledge_base.items
                  if (it.get("month") or "").startswith(year_prefix)]

    # 按月分布
    month_dist = Counter(it.get("month") for it in year_items)
    # 按分类分布
    cat_dist = Counter(it.get("category", "未分类") for it in year_items)

    # 高频风险关键词
    keyword_counter = Counter()
    risk_keywords = ["风险", "处罚", "违规", "诉讼", "仲裁", "赔偿",
                     "无效", "解除", "违约", "索赔", "争议", "瑕疵"]
    for it in year_items:
        text = it.get("compliance_risk", "") + it.get("legal_answer", "")
        for kw in risk_keywords:
            if kw in text:
                keyword_counter[kw] += 1

    report_lines = [
        f"# {year}年度法律风险报告",
        "",
        "## 一、知识库概况",
        f"- 年度入库：{len(year_items)} 条",
        f"- 涉及月份：{len([m for m in month_dist if m])} 个",
        f"- 涉及分类：{len(cat_dist)} 个",
        "",
        "## 二、月份分布",
    ]
    for m in sorted(month_dist):
        if m:
            report_lines.append(f"- {m[:4]}-{m[4:]}: {month_dist[m]} 条")

    report_lines.extend(["", "## 三、分类分布"])
    for cat, cnt in cat_dist.most_common():
        report_lines.append(f"- {cat}: {cnt} 条")

    report_lines.extend(["", "## 四、风险关键词频次"])
    for kw, cnt in keyword_counter.most_common():
        report_lines.append(f"- {kw}: {cnt} 次")

    report_lines.extend([
        "",
        "## 五、建议关注事项",
        "1. 高频风险关键词对应的事项应纳入年度合规重点",
        "2. 反复出现的同类问题应建立标准化操作指引",
        "3. 新出现的风险类型应制定专项合规方案",
        "",
        "---",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}",
    ])

    return jsonify({
        "year": year,
        "total_items": len(year_items),
        "month_breakdown": dict(month_dist),
        "category_breakdown": dict(cat_dist),
        "keyword_breakdown": dict(keyword_counter),
        "report_text": "\n".join(report_lines),
    })


@bp.route("/templates")
@require_login()
def templates():
    """报告模板列表（占位，后续扩展）。"""
    return jsonify({
        "templates": [
            {
                "id": "monthly_summary",
                "name": "月度法务工作总结",
                "description": "汇总当月工单与知识入库情况",
            },
            {
                "id": "risk_scan",
                "name": "近期风险扫描",
                "description": "扫描最近 N 个月新增知识的风险关键词",
            },
            {
                "id": "annual_risk",
                "name": "年度法律风险报告",
                "description": "汇总年度知识库数据，识别风险趋势",
            },
        ]
    })
