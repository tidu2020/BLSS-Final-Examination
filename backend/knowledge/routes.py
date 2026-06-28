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


# ---------- A4：相似知识合并提示 ----------

@bp.route("/items/<item_id>/similar", methods=["GET"])
@require_role("admin")
def similar_items(item_id: str):
    """查找与指定条目相似的知识（相似度 >= 0.5），提示可合并。

    返回：{"similar": [...]}
    """
    item = knowledge_base.get(item_id)
    if not item:
        return jsonify({"error": "条目不存在"}), 404

    candidates = orchestrator.matcher.filter(
        item.get("question", ""), knowledge_base.items, top_n=20)
    # 排除自身
    candidates = [c for c in candidates if c.get("id") != item_id]
    ranked = orchestrator._rank_with_score(
        item.get("question", ""), candidates, top_k=10)

    similar = [
        {
            "id": r["item"]["id"],
            "question": r["item"].get("question", ""),
            "score": r["score"],
            "category": r["item"].get("category", ""),
            "tags": r["item"].get("tags", []),
        }
        for r in ranked if r["score"] >= 0.5
    ]
    return jsonify({
        "item_id": item_id,
        "similar": similar,
        "can_merge": len(similar) > 0,
    })


@bp.route("/items/merge", methods=["POST"])
@require_role("admin")
def merge_items():
    """合并两条相似知识（需法务确认后生效）。

    请求体：{
        "primary_id": "...",     # 保留的主条目
        "secondary_id": "...",   # 被合并的条目（删除）
        "merged_patch": {...}    # 合并后的字段（可选）
    }

    流程：合并字段 -> 标记 secondary 为待法务确认 -> 生成合并申请
    """
    data = request.get_json() or {}
    primary_id = data.get("primary_id")
    secondary_id = data.get("secondary_id")
    merged_patch = data.get("merged_patch", {})

    if not primary_id or not secondary_id:
        return jsonify({"error": "primary_id 和 secondary_id 必填"}), 400
    if primary_id == secondary_id:
        return jsonify({"error": "不能与自身合并"}), 400

    primary = knowledge_base.get(primary_id)
    secondary = knowledge_base.get(secondary_id)
    if not primary or not secondary:
        return jsonify({"error": "条目不存在"}), 404

    # 应用合并补丁到主条目
    if merged_patch:
        primary.update(merged_patch)

    # 生成合并确认申请（法务确认后真正删除 secondary）
    import json, os
    from datetime import datetime as _dt
    req_path = os.path.join("data", "merge_requests.json")
    reqs = []
    if os.path.exists(req_path):
        with open(req_path, "r", encoding="utf-8") as f:
            reqs = json.load(f)

    req = {
        "id": f"MR-{_dt.now().strftime('%Y%m%d%H%M%S')}",
        "primary_id": primary_id,
        "secondary_id": secondary_id,
        "primary_after": primary,
        "secondary_before": secondary,
        "status": "pending",  # pending（待法务确认）/ confirmed / cancelled
        "created_at": _dt.now().isoformat(timespec="seconds"),
    }
    reqs.append(req)
    os.makedirs(os.path.dirname(req_path), exist_ok=True)
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(reqs, f, ensure_ascii=False, indent=2)

    return jsonify({
        "status": "merge_requested",
        "request_id": req["id"],
        "message": "合并申请已生成，待法务确认后生效",
    })


# ---------- L3：管理员审批法务的修改申请 ----------

@bp.route("/edit_requests", methods=["GET"])
@require_role("admin")
def list_edit_requests():
    """列出待审批的修改申请。"""
    import os, json
    req_path = os.path.join("data", "edit_requests.json")
    if not os.path.exists(req_path):
        return jsonify([])
    with open(req_path, "r", encoding="utf-8") as f:
        reqs = json.load(f)
    status = request.args.get("status")
    if status:
        reqs = [r for r in reqs if r.get("status") == status]
    return jsonify(reqs)


@bp.route("/edit_requests/<req_id>/approve", methods=["POST"])
@require_role("admin")
def approve_edit_request(req_id: str):
    """管理员批准修改申请，应用到知识库。"""
    import os, json
    from datetime import datetime as _dt
    req_path = os.path.join("data", "edit_requests.json")
    if not os.path.exists(req_path):
        return jsonify({"error": "无修改申请"}), 404

    with open(req_path, "r", encoding="utf-8") as f:
        reqs = json.load(f)

    target = None
    for r in reqs:
        if r["id"] == req_id:
            target = r
            break
    if not target:
        return jsonify({"error": "申请不存在"}), 404
    if target["status"] != "pending":
        return jsonify({"error": f"申请状态 {target['status']} 不可审批"}), 400

    # 应用 patch
    item_id = target["item_id"]
    patch = target["patch"]
    updated = knowledge_base.update(item_id, patch)
    if updated:
        knowledge_base.save()
        orchestrator.refresh()

    target["status"] = "approved"
    target["decided_at"] = _dt.now().isoformat(timespec="seconds")
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(reqs, f, ensure_ascii=False, indent=2)

    return jsonify({"status": "approved", "item_id": item_id, "updated": updated})


@bp.route("/edit_requests/<req_id>/reject", methods=["POST"])
@require_role("admin")
def reject_edit_request(req_id: str):
    """管理员驳回修改申请。"""
    import os, json
    from datetime import datetime as _dt
    req_path = os.path.join("data", "edit_requests.json")
    if not os.path.exists(req_path):
        return jsonify({"error": "无修改申请"}), 404

    with open(req_path, "r", encoding="utf-8") as f:
        reqs = json.load(f)

    for r in reqs:
        if r["id"] == req_id:
            if r["status"] != "pending":
                return jsonify({"error": f"申请状态 {r['status']} 不可驳回"}), 400
            r["status"] = "rejected"
            r["decided_at"] = _dt.now().isoformat(timespec="seconds")
            with open(req_path, "w", encoding="utf-8") as f:
                json.dump(reqs, f, ensure_ascii=False, indent=2)
            return jsonify({"status": "rejected", "request_id": req_id})

    return jsonify({"error": "申请不存在"}), 404
