"""knowledge 路由：知识库管理。

接口：
- GET  /api/kb/search                搜索知识库
- GET  /api/kb/items                 知识列表（分页/过滤）
- GET  /api/kb/items/<id>             知识详情
- POST /api/kb/items                 新增条目（管理员，需校验）
- PUT  /api/kb/items/<id>             修改条目（管理员）
- DELETE /api/kb/items/<id>           删除条目（管理员）
- GET  /api/kb/stats                 统计信息（月份/分类/标签分布）
- POST /api/kb/rebuild                重建索引（管理员）
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.auth.decorator import require_login, require_role
from backend.knowledge.models import KnowledgeBase
from backend.ai.orchestrator import ReviewOrchestrator
from backend.legal.validator import FaqValidator

bp = Blueprint("knowledge", __name__, url_prefix="/api/kb")

# 由 app 注入
knowledge_base: KnowledgeBase = None  # type: ignore
orchestrator: ReviewOrchestrator = None  # type: ignore


def init_dependencies(kb: KnowledgeBase, orch: ReviewOrchestrator) -> None:
    global knowledge_base, orchestrator
    knowledge_base = kb
    orchestrator = orch


@bp.route("/search")
@require_login()
def search():
    """搜索知识库。

    查询参数：q=关键词&top_k=5
    """
    q = (request.args.get("q") or "").strip()
    top_k = int(request.args.get("top_k", 5))
    if not q:
        return jsonify({"error": "查询 q 不能为空"}), 400

    result = orchestrator.review(q, top_k=top_k, with_disclaimer=False)
    return jsonify({
        "results": [
            {
                "id": r["id"],
                "question": r["question"],
                "score": r["score"],
                "legal_answer": r["item"].get("legal_answer", ""),
                "legal_basis": r["item"].get("legal_basis", ""),
            }
            for r in result["results"]
        ],
        "candidates_count": result["candidates_count"],
    })


@bp.route("/items")
@require_login()
def list_items():
    """知识列表（支持过滤）。

    查询参数：
    - month: 6 位月份
    - category: 分类
    - tag: 标签
    - q: 关键词模糊搜索（question/legal_answer）
    - limit/offset: 分页
    """
    month = request.args.get("month")
    category = request.args.get("category")
    tag = request.args.get("tag")
    q = request.args.get("q")
    limit = int(request.args.get("limit", 20))
    offset = int(request.args.get("offset", 0))

    items = knowledge_base.items
    if month:
        items = [it for it in items if it.get("month") == month]
    if category:
        items = [it for it in items if it.get("category") == category]
    if tag:
        items = [it for it in items if tag in (it.get("tags") or [])]
    if q:
        ql = q.lower()
        items = [
            it for it in items
            if ql in it.get("question", "").lower()
            or ql in it.get("legal_answer", "").lower()
        ]

    total = len(items)
    page = items[offset: offset + limit]
    return jsonify({
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@bp.route("/items/<item_id>")
@require_login()
def get_item(item_id: str):
    """知识详情。"""
    item = knowledge_base.get(item_id)
    if not item:
        return jsonify({"error": "条目不存在"}), 404
    return jsonify(item)


@bp.route("/items", methods=["POST"])
@require_role("admin")
def add_item():
    """新增条目（管理员直接增）。

    请求体：单条 FAQ 条目
    流程：校验 -> 有硬错误拒绝 -> 软警告返回 409 二次确认 -> 入库
    """
    item = request.get_json() or {}
    validator = FaqValidator(knowledge_base, retriever=orchestrator.retriever)
    result = validator.validate(item)

    if not result["passed"]:
        return jsonify({
            "error": "校验未通过",
            "errors": result["errors"],
            "warnings": result["warnings"],
        }), 400

    force = bool(request.args.get("force", "false") == "true")
    if result["warnings"] and not force:
        return jsonify({
            "error": "存在警告，需二次确认",
            "warnings": result["warnings"],
            "similar_items": result["similar_items"],
            "need_force": True,
        }), 409

    added = knowledge_base.add(item)
    knowledge_base.save()
    orchestrator.refresh()
    return jsonify(added), 201


@bp.route("/items/<item_id>", methods=["PUT"])
@require_role("admin")
def update_item(item_id: str):
    """修改条目（管理员）。"""
    item = knowledge_base.get(item_id)
    if not item:
        return jsonify({"error": "条目不存在"}), 404

    data = request.get_json() or {}
    item.update(data)
    knowledge_base.save()
    orchestrator.refresh()
    return jsonify(item)


@bp.route("/items/<item_id>", methods=["DELETE"])
@require_role("admin")
def delete_item(item_id: str):
    """删除条目（管理员）。"""
    if knowledge_base.delete(item_id):
        knowledge_base.save()
        orchestrator.refresh()
        return jsonify({"status": "deleted"})
    return jsonify({"error": "条目不存在"}), 404


@bp.route("/stats")
@require_login()
def stats():
    """统计信息。"""
    return jsonify({
        "total": knowledge_base.count(),
        "months": knowledge_base.all_months(),
        "categories": knowledge_base.all_categories(),
        "status_breakdown": knowledge_base.status_breakdown(),
    })


@bp.route("/rebuild", methods=["POST"])
@require_role("admin")
def rebuild():
    """重建索引（管理员）。"""
    orchestrator.refresh()
    return jsonify({"status": "ok", "items_count": knowledge_base.count()})
