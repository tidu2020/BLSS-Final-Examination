"""legal 路由：法务工作台。

接口：
- GET  /api/legal/orders              工单列表
- GET  /api/legal/orders/<id>          工单详情
- POST /api/legal/orders/<id>/review   开始审核/保存法务结论
- POST /api/legal/validate            归档前校验
- POST /api/legal/confirm             确认入库（需先通过校验）
- POST /api/legal/orders/<id>/archive 归档
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.auth.decorator import require_role, current_user
from backend.models.account import AccountStore
from backend.models.workflow import WorkOrderStore
from backend.knowledge.models import KnowledgeBase
from backend.ai.orchestrator import ReviewOrchestrator
from backend.legal.validator import FaqValidator, ValidationError

bp = Blueprint("legal", __name__, url_prefix="/api/legal")

# 由 app 注入
account_store: AccountStore = None  # type: ignore
work_order_store: WorkOrderStore = None  # type: ignore
knowledge_base: KnowledgeBase = None  # type: ignore
orchestrator: ReviewOrchestrator = None  # type: ignore


def init_dependencies(store: AccountStore, orders: WorkOrderStore,
                     kb: KnowledgeBase, orch: ReviewOrchestrator) -> None:
    """由 app 注入依赖。"""
    global account_store, work_order_store, knowledge_base, orchestrator
    account_store = store
    work_order_store = orders
    knowledge_base = kb
    orchestrator = orch


@bp.route("/orders")
@require_role("legal", "admin")
def list_orders():
    """工单列表。

    查询参数：
    - status: 按状态过滤
    - submitter: 按提交人过滤
    """
    status = request.args.get("status")
    submitter = request.args.get("submitter")
    orders = work_order_store.query(submitter=submitter, status=status)
    # 精简列表（不含完整对话内容）
    return jsonify([
        {
            "id": o.id,
            "submitter": o.submitter,
            "submitter_name": o.submitter_name,
            "submitted_at": o.submitted_at,
            "status": o.status,
            "reviewer": o.reviewer,
            "reviewer_name": o.reviewer_name,
            "reviewed_at": o.reviewed_at,
            "materials_count": len(o.materials),
            "dialogue_count": len(o.dialogue),
        }
        for o in orders
    ])


@bp.route("/orders/<order_id>")
@require_role("legal", "admin")
def get_order(order_id: str):
    """工单详情（含材料+对话+AI结论+法务结论）。"""
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404
    return jsonify(order.to_dict())


@bp.route("/orders/<order_id>/review", methods=["POST"])
@require_role("legal", "admin")
def review(order_id: str):
    """开始审核 / 保存法务结论。

    请求体：{"conclusion": "...", "action": "start|save"}
    - action=start：标记开始审核
    - action=save：保存法务结论
    """
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404

    data = request.get_json() or {}
    action = data.get("action", "save")
    user = current_user()
    acct = account_store.get(user["id"])

    if action == "start":
        try:
            order.start_review(acct)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 400
    elif action == "save":
        if order.status == "submitted_to_legal":
            # 还未开始审核，自动开始
            try:
                order.start_review(acct)
            except RuntimeError as e:
                return jsonify({"error": str(e)}), 400
        order.set_legal_conclusion(data.get("conclusion", ""))
    else:
        return jsonify({"error": f"未知 action：{action}"}), 400

    work_order_store.save(order)
    return jsonify({
        "status": order.status,
        "reviewer": order.reviewer,
        "reviewer_name": order.reviewer_name,
        "reviewed_at": order.reviewed_at,
    })


@bp.route("/validate", methods=["POST"])
@require_role("legal", "admin")
def validate_entry():
    """归档前校验拟入库条目。

    请求体：单条 FAQ 条目
    返回：{"passed": bool, "errors": [...], "warnings": [...],
           "similar_items": [...]}
    """
    item = request.get_json() or {}
    validator = FaqValidator(knowledge_base, retriever=orchestrator.retriever)
    result = validator.validate(item)
    return jsonify(result)


@bp.route("/confirm", methods=["POST"])
@require_role("legal", "admin")
def confirm_to_kb():
    """确认入库。

    请求体：{
        "order_id": "...",
        "item": {... FAQ 条目 ...},
        "force": false  # 是否强制入库（忽略 warnings）
    }

    流程：
    1. 校验条目
    2. 有硬错误 -> 400 拒绝
    3. 有软警告且未 force -> 409 返回警告，需二次确认
    4. 通过 -> 入库 + 关联工单
    """
    data = request.get_json() or {}
    order_id = data.get("order_id")
    item = data.get("item", {})
    force = bool(data.get("force", False))

    validator = FaqValidator(knowledge_base, retriever=orchestrator.retriever)
    result = validator.validate(item)

    # 硬错误 -> 拒绝
    if not result["passed"]:
        return jsonify({
            "error": "校验未通过（硬错误）",
            "errors": result["errors"],
            "warnings": result["warnings"],
        }), 400

    # 软警告 + 未强制 -> 二次确认
    if result["warnings"] and not force:
        return jsonify({
            "error": "存在警告，需二次确认",
            "warnings": result["warnings"],
            "similar_items": result["similar_items"],
            "need_force": True,
        }), 409

    # 入库
    item["source_work_order_id"] = order_id
    item["status"] = "confirmed"
    added = knowledge_base.add(item)
    knowledge_base.save()
    # 刷新检索索引
    orchestrator.refresh()

    # 关联工单
    if order_id:
        order = work_order_store.get(order_id)
        if order:
            faqs = list(order.confirmed_faqs)
            faqs.append({
                "id": added["id"],
                "question": added.get("question", ""),
            })
            order.confirmed_faqs = faqs
            order.confirmed_at = order.confirmed_at or None  # 保留已设时间
            work_order_store.save(order)

    return jsonify({
        "status": "confirmed",
        "faq_id": added["id"],
        "warnings": result["warnings"],
    })


@bp.route("/orders/<order_id>/confirm", methods=["POST"])
@require_role("legal", "admin")
def confirm_order(order_id: str):
    """标记工单为已确认（法务完成审核）。"""
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404

    data = request.get_json() or {}
    faqs = data.get("faqs", [])
    if not order.legal_conclusion and not data.get("conclusion"):
        return jsonify({"error": "请先填写法务结论"}), 400

    if data.get("conclusion"):
        order.set_legal_conclusion(data["conclusion"])
    order.confirm(faqs)
    work_order_store.save(order)
    return jsonify({
        "status": order.status,
        "confirmed_at": order.confirmed_at,
        "confirmed_count": len(order.confirmed_faqs),
    })


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
