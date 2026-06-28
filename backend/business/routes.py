"""business 路由：业务门户。

接口：
- POST /api/business/consult      AI 咨询
- POST /api/business/upload       上传材料
- POST /api/business/submit_order 提交法务工单
- GET  /api/business/my_orders     我的工单
- GET  /api/business/orders/<id>   工单详情
- POST /api/business/sessions/<id>/close  关闭会话
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request, session

from backend.auth.decorator import require_role, current_user
from backend.config import config
from backend.models.account import AccountStore
from backend.models.reader import reader_factory
from backend.models.workflow import Session, WorkOrder, WorkOrderStore
from backend.ai.orchestrator import ReviewOrchestrator

bp = Blueprint("business", __name__, url_prefix="/api/business")

# 由 app 注入
account_store: AccountStore = None  # type: ignore
work_order_store: WorkOrderStore = None  # type: ignore
orchestrator: ReviewOrchestrator = None  # type: ignore

# 会话内存缓存（生产版应换 Redis）
_sessions: dict = {}


def init_dependencies(store: AccountStore, orders: WorkOrderStore,
                     orch: ReviewOrchestrator) -> None:
    """由 app 注入依赖。"""
    global account_store, work_order_store, orchestrator
    account_store = store
    work_order_store = orders
    orchestrator = orch


def _get_or_create_session() -> Session:
    """获取当前用户的活跃会话，没有则新建。"""
    user_info = current_user()
    user = account_store.get(user_info["id"])
    sess_id = session.get("active_session_id")
    if sess_id and sess_id in _sessions:
        return _sessions[sess_id]
    # 新建会话
    sess = Session(user)
    _sessions[sess.id] = sess
    session["active_session_id"] = sess.id
    return sess


@bp.route("/consult", methods=["POST"])
@require_role("business")
def consult():
    """AI 咨询（三级兜底 + 支持上传材料作为上下文）。

    请求体：{"question": "...", "with_materials": true}
    返回：{"answer": "...", "results": [...], "sources": [...],
           "mode": "...", "relevance": "...", "disclaimer": "..."}
    """
    data = request.get_json() or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400

    # 若启用材料合并，将已上传材料作为额外上下文
    extra_context = ""
    if data.get("with_materials"):
        sess = _get_or_create_session()
        if sess.materials:
            mat_parts = []
            for m in sess.materials:
                mat_parts.append(f"[{m['filename']}]\n{m['content'][:1500]}")
            extra_context = "\n\n".join(mat_parts)

    # 调用 AI 引擎
    result = orchestrator.review(question, top_k=5, extra_context=extra_context)
    # 记录到会话
    sess = _get_or_create_session()
    sess.add_question(question, result["answer"])

    return jsonify({
        "answer": result["answer"],
        "results": [
            {
                "id": r["id"],
                "question": r["question"],
                "score": r["score"],
            }
            for r in result["results"]
        ],
        "sources": result["sources"],
        "mode": result["mode"],
        "relevance": result["relevance"],
        "disclaimer": result["disclaimer"],
        "session_id": sess.id,
    })


@bp.route("/upload", methods=["POST"])
@require_role("business")
def upload():
    """上传材料文件。

    表单字段：file（multipart）
    返回：{"filename": "...", "size": N, "session_id": "..."}
    """
    if "file" not in request.files:
        return jsonify({"error": "未上传文件"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "文件名为空"}), 400

    # 读取文件内容
    reader = reader_factory(file.filename)
    # Flask FileStorage 有 filename 属性，可直接读取
    content = reader.read(file)

    # 记录到会话
    sess = _get_or_create_session()
    sess.add_material(file.filename, content)

    return jsonify({
        "filename": file.filename,
        "size": len(content),
        "session_id": sess.id,
    })


@bp.route("/session", methods=["GET"])
@require_role("business")
def get_session():
    """获取当前会话内容（对话 + 材料列表）。"""
    sess = _get_or_create_session()
    return jsonify(sess.to_dict())


@bp.route("/sessions/<session_id>/close", methods=["POST"])
@require_role("business")
def close_session(session_id: str):
    """关闭会话（业务选择"仅参考"）。"""
    sess = _sessions.get(session_id)
    if not sess:
        return jsonify({"error": "会话不存在"}), 404
    sess.close()
    session.pop("active_session_id", None)
    return jsonify({"status": "closed"})


@bp.route("/submit_order", methods=["POST"])
@require_role("business")
def submit_order():
    """打包工单提交法务。

    请求体：可选 {"note": "..."}
    返回：{"order_id": "..."}
    """
    sess = _get_or_create_session()
    if sess.status != "consulting":
        return jsonify({"error": f"会话状态 {sess.status} 不可提交"}), 400
    if not sess.dialogue:
        return jsonify({"error": "会话无对话内容，无法提交"}), 400

    # 附带业务备注
    data = request.get_json() or {}
    note = data.get("note", "")
    if note:
        sess.add_dialogue("user", f"[业务备注] {note}")

    # 提交
    order = sess.submit_to_legal()
    work_order_store.save(order)
    # 清除活跃会话
    session.pop("active_session_id", None)

    return jsonify({
        "order_id": order.id,
        "status": order.status,
        "submitted_at": order.submitted_at,
    })


@bp.route("/my_orders")
@require_role("business")
def my_orders():
    """我提交的工单列表。"""
    user_id = current_user()["id"]
    orders = work_order_store.query(submitter=user_id)
    return jsonify([o.to_dict() for o in orders])


@bp.route("/orders/<order_id>")
@require_role("business", "legal", "admin")
def get_order(order_id: str):
    """工单详情。

    业务只能看自己的；法务和管理员可看全部。
    业务查看时自动标记已读（法务据此判断是否还能撤回已确认审核）。
    """
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404

    user = current_user()
    if user["role"] == "business" and order.submitter != user["id"]:
        return jsonify({"error": "无权查看他人工单"}), 403

    # 业务查看时标记已读（用于法务撤回判断）
    if user["role"] == "business" and order.status in ("confirmed", "archived"):
        if not getattr(order, "business_read_at", ""):
            order.mark_business_read()
            work_order_store.save(order)

    return jsonify(order.to_dict())


@bp.route("/orders/<order_id>/withdraw", methods=["POST"])
@require_role("business")
def withdraw_order(order_id: str):
    """业务撤回工单（仅法务开始审核前可撤回）。

    返回：{"status": "withdrawn"}
    """
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404

    user = current_user()
    if order.submitter != user["id"]:
        return jsonify({"error": "只能撤回自己的工单"}), 403

    try:
        order.withdraw()
        work_order_store.save(order)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"status": "withdrawn", "order_id": order_id})


@bp.route("/orders/<order_id>/confirm", methods=["POST"])
@require_role("business")
def business_confirm_order(order_id: str):
    """业务确认法务审核结果（归档前置条件）。

    仅 confirmed 状态可确认；确认后法务才可归档。
    """
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404
    user = current_user()
    if order.submitter != user["id"]:
        return jsonify({"error": "只能确认自己的工单"}), 403
    try:
        order.business_confirm()
        work_order_store.save(order)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "confirmed", "business_confirmed_at": order.business_confirmed_at})


@bp.route("/orders/<order_id>/ask_more", methods=["POST"])
@require_role("business")
def business_ask_more(order_id: str):
    """业务提出新问题，工单回到 reviewing 状态。

    请求体：{"question": "..."}
    """
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404
    user = current_user()
    if order.submitter != user["id"]:
        return jsonify({"error": "只能操作自己的工单"}), 403
    data = request.get_json() or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400
    try:
        order.business_ask_more(question)
        work_order_store.save(order)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "reviewing", "order_id": order_id})
