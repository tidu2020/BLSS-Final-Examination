"""archive 路由：归档导出。

接口：
- GET  /api/archive/orders              已归档工单列表
- GET  /api/archive/orders/<id>/package  下载归档包（Markdown）
- POST /api/archive/orders/<id>/archive  归档工单（调用 WorkOrder.archive）
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, Response

from backend.auth.decorator import require_role
from backend.models.workflow import WorkOrderStore

bp = Blueprint("archive", __name__, url_prefix="/api/archive")

# 由 app 注入
work_order_store: WorkOrderStore = None  # type: ignore


def init_dependencies(orders: WorkOrderStore) -> None:
    global work_order_store
    work_order_store = orders


def _build_markdown(order) -> str:
    """把工单打包成 Markdown 归档文件。"""
    lines = [
        f"# 法务工单归档 {order.id}",
        "",
        "## 一、基本信息",
        f"- 工单号：{order.id}",
        f"- 提交人：{order.submitter_name or order.submitter}"
        f"（{order.submitter}）",
        f"- 提交时间：{order.submitted_at}",
        f"- AI 审核时间：{order.ai_reviewed_at}",
        f"- 法务审核人：{order.reviewer_name or order.reviewer}"
        f"（{order.reviewer}）" if order.reviewer else "",
        f"- 法务审核时间：{order.reviewed_at}" if order.reviewed_at else "",
        f"- 确认时间：{order.confirmed_at}" if order.confirmed_at else "",
        f"- 状态：{order.status}",
        "",
        "## 二、业务咨询对话",
        "",
    ]
    for d in order.dialogue:
        role = d.get("role", "")
        content = d.get("content", "")
        speaker = {"user": "业务", "ai": "AI", "legal": "法务"}.get(
            role, role)
        lines.append(f"**{speaker}**：{content}")
        lines.append("")

    lines.append("## 三、提交材料")
    if order.materials:
        for m in order.materials:
            lines.append(f"### {m.get('filename', '')}")
            lines.append("```")
            content = m.get("content", "")
            # 截断超长材料
            if len(content) > 2000:
                content = content[:2000] + "\n...（截断）"
            lines.append(content)
            lines.append("```")
            lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 四、AI 综合结论")
    lines.append(order.ai_conclusion or "（无）")
    lines.append("")

    lines.append("## 五、法务结论")
    lines.append(order.legal_conclusion or "（无）")
    lines.append("")

    lines.append("## 六、入库 FAQ")
    if order.confirmed_faqs:
        for f in order.confirmed_faqs:
            lines.append(f"- [{f.get('id', '')}] {f.get('question', '')}")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("---")
    lines.append(f"归档时间：{datetime.now().isoformat(timespec='seconds')}")

    return "\n".join(lines)


@bp.route("/orders")
@require_role("legal", "admin")
def list_archived():
    """已归档工单列表。"""
    orders = work_order_store.query(status="archived")
    return jsonify([
        {
            "id": o.id,
            "submitter": o.submitter,
            "submitter_name": o.submitter_name,
            "submitted_at": o.submitted_at,
            "confirmed_at": o.confirmed_at,
            "reviewer_name": o.reviewer_name,
        }
        for o in orders
    ])


@bp.route("/orders/<order_id>/package")
@require_role("legal", "admin")
def download_package(order_id: str):
    """下载归档包（Markdown 格式）。"""
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404

    md = _build_markdown(order)
    filename = f"archive_{order_id}.md"

    return Response(
        md,
        mimetype="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/orders/<order_id>/archive", methods=["POST"])
@require_role("legal", "admin")
def archive_order(order_id: str):
    """归档工单。"""
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404
    if order.status != "confirmed":
        return jsonify({"error": "仅已确认工单可归档"}), 400
    order.archive()
    work_order_store.save(order)
    return jsonify({"status": order.status})
