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

import json
import os
from datetime import datetime
from flask import Blueprint, jsonify, request

from backend.config import config
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


# ---------- 合并拒绝记录存储 ----------
# 结构：[{"a_id": "...", "b_id": "...", "rejected_at": "...", "reason": "..."}]
# 同一对 (a,b) 只保留一条记录，按 id 字典序作为 key

def _load_rejections() -> list:
    path = config.MERGE_REJECTIONS_PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_rejections(data: list) -> None:
    path = config.MERGE_REJECTIONS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _rejection_key(a_id: str, b_id: str) -> str:
    """生成稳定的拒绝记录 key（与顺序无关）。"""
    return "|".join(sorted([a_id, b_id]))


def _is_rejected(a_id: str, b_id: str) -> bool:
    key = _rejection_key(a_id, b_id)
    return any(r.get("key") == key for r in _load_rejections())


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


@bp.route("/similar_pairs", methods=["GET"])
@require_role("admin")
def similar_pairs():
    """扫描全部知识库，识别所有相似对（相似度 >= 0.6）。

    查询参数：
    - scope: "all"=全量扫描（含已拒绝，默认）；"active"=排除已拒绝的
    返回：{"pairs": [{"a": {...}, "b": {...}, "score": 0.xx}, ...], "count": N}
    """
    scope = request.args.get("scope", "all")
    exclude_rejected = (scope == "active")
    rejections = _load_rejections()
    rejected_keys = {r.get("key") for r in rejections}

    items = knowledge_base.items
    pairs = []
    seen = set()
    for i, it_a in enumerate(items):
        q_a = it_a.get("question", "")
        if not q_a:
            continue
        # 仅与后续条目比对，避免重复
        for it_b in items[i + 1:]:
            q_b = it_b.get("question", "")
            if not q_b:
                continue
            ranked = orchestrator._rank_with_score(q_a, [it_b], top_k=1)
            if ranked and ranked[0]["score"] >= 0.6:
                key = tuple(sorted([it_a["id"], it_b["id"]]))
                if key in seen:
                    continue
                # 排除已拒绝
                if exclude_rejected and "|".join(key) in rejected_keys:
                    continue
                seen.add(key)
                pairs.append({
                    "a": {
                        "id": it_a["id"],
                        "question": q_a,
                        "legal_answer": it_a.get("legal_answer", "")[:120],
                        "category": it_a.get("category", ""),
                        "tags": it_a.get("tags", []),
                    },
                    "b": {
                        "id": it_b["id"],
                        "question": q_b,
                        "legal_answer": it_b.get("legal_answer", "")[:120],
                        "category": it_b.get("category", ""),
                        "tags": it_b.get("tags", []),
                    },
                    "score": ranked[0]["score"],
                })
    # 按相似度降序
    pairs.sort(key=lambda p: p["score"], reverse=True)
    return jsonify({"pairs": pairs, "count": len(pairs)})


@bp.route("/merge_rejections", methods=["GET"])
@require_role("admin")
def list_merge_rejections():
    """列出已被拒绝的合并对。"""
    rejections = _load_rejections()
    # 关联当前条目信息（条目可能已删除）
    items_map = {it["id"]: it for it in knowledge_base.items}
    result = []
    for r in rejections:
        a = items_map.get(r.get("a_id"), {})
        b = items_map.get(r.get("b_id"), {})
        result.append({
            "key": r.get("key"),
            "a_id": r.get("a_id"),
            "b_id": r.get("b_id"),
            "a_question": a.get("question", "（已删除）"),
            "b_question": b.get("question", "（已删除）"),
            "rejected_at": r.get("rejected_at", ""),
            "reason": r.get("reason", ""),
        })
    result.sort(key=lambda x: x["rejected_at"], reverse=True)
    return jsonify({"rejections": result, "count": len(result)})


@bp.route("/merge_rejections", methods=["POST"])
@require_role("admin")
def reject_merge():
    """拒绝一对合并建议。

    请求体：{"a_id": "...", "b_id": "...", "reason": "..."}
    后续 similar_pairs?scope=active 扫描将排除该对。
    """
    data = request.get_json() or {}
    a_id = data.get("a_id", "").strip()
    b_id = data.get("b_id", "").strip()
    reason = (data.get("reason") or "").strip()
    if not a_id or not b_id:
        return jsonify({"error": "缺少 a_id 或 b_id"}), 400
    if a_id == b_id:
        return jsonify({"error": "a_id 与 b_id 不能相同"}), 400

    key = _rejection_key(a_id, b_id)
    rejections = _load_rejections()
    # 幂等：已存在则更新 reason
    existing = next((r for r in rejections if r.get("key") == key), None)
    if existing:
        existing["rejected_at"] = datetime.now().isoformat(timespec="seconds")
        existing["reason"] = reason
    else:
        rejections.append({
            "key": key,
            "a_id": a_id,
            "b_id": b_id,
            "rejected_at": datetime.now().isoformat(timespec="seconds"),
            "reason": reason,
        })
    _save_rejections(rejections)
    return jsonify({"ok": True, "key": key})


@bp.route("/merge_rejections/<key>", methods=["DELETE"])
@require_role("admin")
def remove_merge_rejection(key: str):
    """移除一条拒绝记录，使该对在后续扫描中重新出现。"""
    rejections = _load_rejections()
    new_list = [r for r in rejections if r.get("key") != key]
    if len(new_list) == len(rejections):
        return jsonify({"error": "拒绝记录不存在"}), 404
    _save_rejections(new_list)
    return jsonify({"ok": True})


@bp.route("/merge_draft", methods=["POST"])
@require_role("admin")
def merge_draft():
    """生成合并建议稿（LLM 辅助合并两条相似知识）。

    请求体：{"primary_id": "...", "secondary_id": "..."}

    流程：取两条知识完整内容 → LLM 合并生成建议稿 → 返回给管理员修改确认。
    """
    data = request.get_json() or {}
    primary_id = data.get("primary_id")
    secondary_id = data.get("secondary_id")
    if not primary_id or not secondary_id:
        return jsonify({"error": "primary_id 和 secondary_id 必填"}), 400

    primary = knowledge_base.get(primary_id)
    secondary = knowledge_base.get(secondary_id)
    if not primary or not secondary:
        return jsonify({"error": "条目不存在"}), 404

    from backend.ai.llm_client import LlmClient, LlmError
    llm = orchestrator.llm
    if not llm or not llm.available:
        # LLM 不可用，退化为字段拼接建议稿
        draft = {
            "question": primary.get("question", "") or secondary.get("question", ""),
            "legal_answer": (primary.get("legal_answer", "") + "\n\n" + secondary.get("legal_answer", "")).strip(),
            "compliance_risk": (primary.get("compliance_risk", "") + "\n\n" + secondary.get("compliance_risk", "")).strip(),
            "practical_advice": (primary.get("practical_advice", "") + "\n\n" + secondary.get("practical_advice", "")).strip(),
            "legal_basis": (primary.get("legal_basis", "") + "\n\n" + secondary.get("legal_basis", "")).strip(),
            "category": primary.get("category", "") or secondary.get("category", ""),
            "tags": list(set((primary.get("tags", []) or []) + (secondary.get("tags", []) or []))),
        }
        return jsonify({"draft": draft, "primary": primary, "secondary": secondary, "llm_used": False})

    # LLM 合并
    import json as _json
    sys_prompt = (
        "你是知识库合并助手。请把两条相似的法务知识条目合并为一条，"
        "保留双方核心信息，去除重复，输出 JSON 格式：\n"
        '{"question":"...","legal_answer":"...","compliance_risk":"...",'
        '"practical_advice":"...","legal_basis":"...","category":"...","tags":["..."]}\n'
        "要求：question 以问号结尾；合并后保留两条的法律依据；不得编造。"
    )
    user_prompt = (
        f"【条目A - {primary_id}】\n{_json.dumps(primary, ensure_ascii=False, indent=2)}\n\n"
        f"【条目B - {secondary_id}】\n{_json.dumps(secondary, ensure_ascii=False, indent=2)}\n\n"
        f"请合并为一条知识条目。"
    )
    try:
        raw = llm.chat(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user_prompt}],
            temperature=0.2,
            max_tokens=1024,
        )
    except LlmError as e:
        return jsonify({"error": f"LLM 合并失败：{e}"}), 500

    import re as _re
    draft = None
    m = _re.search(r"\{[\s\S]*\}", raw or "")
    if m:
        try:
            draft = _json.loads(m.group(0))
        except _json.JSONDecodeError:
            draft = None
    if not draft:
        return jsonify({"error": "LLM 输出无法解析", "raw": raw}), 500

    return jsonify({"draft": draft, "primary": primary, "secondary": secondary, "llm_used": True})


@bp.route("/items/merge", methods=["POST"])
@require_role("admin")
def merge_items():
    """合并两条相似知识（需法务确认后生效）。

    请求体：{
        "primary_id": "...",     # 保留的主条目
        "secondary_id": "...",   # 被合并的条目（删除）
        "merged_patch": {...}    # 合并后的字段（可选，来自 merge_draft）
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

    # 不立即应用补丁，存入申请记录，待法务确认后再生效
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
        "primary_before": primary,
        "secondary_before": secondary,
        "merged_patch": merged_patch,  # 法务确认后应用到 primary
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


# ---------- A5：归档入库申请审批 ----------

@bp.route("/kb_submit_requests", methods=["GET"])
@require_role("admin")
def list_kb_submit_requests():
    """列出待审批的归档入库申请。"""
    import os, json
    req_path = os.path.join("data", "kb_submit_requests.json")
    if not os.path.exists(req_path):
        return jsonify([])
    with open(req_path, "r", encoding="utf-8") as f:
        reqs = json.load(f)
    status = request.args.get("status")
    if status:
        reqs = [r for r in reqs if r.get("status") == status]
    return jsonify(reqs)


@bp.route("/kb_submit_requests/<req_id>/approve", methods=["POST"])
@require_role("admin")
def approve_kb_submit(req_id: str):
    """管理员批准归档入库申请，条目进入知识库。"""
    import os, json
    from datetime import datetime as _dt
    req_path = os.path.join("data", "kb_submit_requests.json")
    if not os.path.exists(req_path):
        return jsonify({"error": "无入库申请"}), 404

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

    # 入库
    faq = target["faq"]
    faq["source_work_order_id"] = target.get("order_id")
    faq["status"] = "confirmed"
    added = knowledge_base.add(faq)
    knowledge_base.save()
    orchestrator.refresh()

    target["status"] = "approved"
    target["decided_at"] = _dt.now().isoformat(timespec="seconds")
    target["added_id"] = added["id"]
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(reqs, f, ensure_ascii=False, indent=2)

    return jsonify({"status": "approved", "faq_id": added["id"]})


@bp.route("/kb_submit_requests/<req_id>/reject", methods=["POST"])
@require_role("admin")
def reject_kb_submit(req_id: str):
    """管理员驳回归档入库申请。"""
    import os, json
    from datetime import datetime as _dt
    req_path = os.path.join("data", "kb_submit_requests.json")
    if not os.path.exists(req_path):
        return jsonify({"error": "无入库申请"}), 404

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
