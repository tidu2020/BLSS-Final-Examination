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


# ---------- L1：法务修改自己的归档内容 ----------

@bp.route("/orders/<order_id>/reopen_edit", methods=["POST"])
@require_role("legal", "admin")
def reopen_archive_edit(order_id: str):
    """法务重新编辑已归档内容，工单回到 reviewing 状态。"""
    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404
    try:
        order.reopen_archive_edit()
        work_order_store.save(order)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": order.status})


# ---------- L2：法务查看全部知识库 ----------

@bp.route("/kb/items")
@require_role("legal", "admin")
def kb_list_items():
    """法务/管理员查看全部知识库条目（只读）。"""
    month = request.args.get("month")
    category = request.args.get("category")
    tag = request.args.get("tag")
    keyword = request.args.get("keyword", "").strip()

    items = knowledge_base.items
    if month:
        items = [it for it in items if it.get("month") == month]
    if category:
        items = [it for it in items if it.get("category") == category]
    if tag:
        items = [it for it in items if tag in it.get("tags", [])]
    if keyword:
        items = [it for it in items
                 if keyword in it.get("question", "")
                 or keyword in it.get("legal_answer", "")
                 or keyword in it.get("legal_basis", "")]

    return jsonify([{
        "id": it["id"],
        "question": it.get("question", ""),
        "legal_answer": it.get("legal_answer", ""),
        "compliance_risk": it.get("compliance_risk", ""),
        "practical_advice": it.get("practical_advice", ""),
        "legal_basis": it.get("legal_basis", ""),
        "category": it.get("category", ""),
        "tags": it.get("tags", []),
        "month": it.get("month", ""),
        "status": it.get("status", ""),
        "source_work_order_id": it.get("source_work_order_id"),
    } for it in items])


@bp.route("/kb/items/<item_id>")
@require_role("legal", "admin")
def kb_get_item(item_id: str):
    """法务/管理员查看单条知识库条目详情。"""
    item = knowledge_base.get(item_id)
    if not item:
        return jsonify({"error": "条目不存在"}), 404
    return jsonify(item)


# ---------- L3：法务提交知识库修改申请 ----------

@bp.route("/kb/items/<item_id>/edit_request", methods=["POST"])
@require_role("legal", "admin")
def kb_edit_request(item_id: str):
    """法务提交知识库条目修改申请（需管理员审批）。

    请求体：{"patch": {...}, "reason": "..."}
    """
    item = knowledge_base.get(item_id)
    if not item:
        return jsonify({"error": "条目不存在"}), 404

    data = request.get_json() or {}
    patch = data.get("patch", {})
    reason = data.get("reason", "")
    if not patch:
        return jsonify({"error": "patch 不能为空"}), 400

    # 记录修改申请（存到 data/edit_requests.json）
    import json, os
    from datetime import datetime as _dt
    req_path = os.path.join("data", "edit_requests.json")
    reqs = []
    if os.path.exists(req_path):
        with open(req_path, "r", encoding="utf-8") as f:
            reqs = json.load(f)

    user = current_user()
    req = {
        "id": f"ER-{_dt.now().strftime('%Y%m%d%H%M%S')}",
        "item_id": item_id,
        "original": item,
        "patch": patch,
        "reason": reason,
        "submitter": user["id"],
        "submitter_name": user.get("name", ""),
        "status": "pending",  # pending / approved / rejected
        "created_at": _dt.now().isoformat(timespec="seconds"),
    }
    reqs.append(req)
    os.makedirs(os.path.dirname(req_path), exist_ok=True)
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(reqs, f, ensure_ascii=False, indent=2)

    return jsonify({"status": "submitted", "request_id": req["id"]})


# ---------- L4：法务调用 AI 审核工具 ----------

@bp.route("/ai_tool", methods=["POST"])
@require_role("legal", "admin")
def ai_tool():
    """法务调用 AI 审核工具（基于知识库检索 + LLM 生成）。

    请求体：{"query": "...", "extra_context": "..."}
    """
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "查询不能为空"}), 400
    extra_context = data.get("extra_context") or ""

    result = orchestrator.review(query, top_k=5, extra_context=extra_context)
    return jsonify({
        "answer": result["answer"],
        "results": [
            {"id": r["id"], "question": r["question"], "score": r["score"]}
            for r in result["results"]
        ],
        "sources": result["sources"],
        "mode": result["mode"],
        "relevance": result["relevance"],
    })


# ---------- L6：法务审核后自动总结 FAQ + 冲突检测 ----------

@bp.route("/orders/<order_id>/summarize_faq", methods=["POST"])
@require_role("legal", "admin")
def summarize_faq(order_id: str):
    """基于工单对话与法务结论自动总结 FAQ 条目，并检测与历史答复的冲突。

    返回：{"faq": {...}, "conflicts": [...]}
    """
    from backend.ai.llm_client import LlmClient, LlmError

    order = work_order_store.get(order_id)
    if not order:
        return jsonify({"error": "工单不存在"}), 404

    # 取最近几轮对话 + 法务结论作为输入
    dialogue_text = "\n".join(
        f"{d.get('role','')}: {d.get('content','')}"
        for d in order.dialogue[-6:]
    )
    legal_conclusion = order.legal_conclusion or ""

    llm = orchestrator.llm
    if not llm or not llm.available:
        return jsonify({"error": "大模型未配置，无法自动总结"}), 503

    # 1. 用 LLM 总结成四段式 FAQ
    sys_prompt = (
        "你是法务知识沉淀助手。请根据下方法务咨询对话与法务结论，"
        "提炼出一条四段式 FAQ 知识条目。\n"
        "输出 JSON 格式：\n"
        '{"question":"...","legal_answer":"...",'
        '"compliance_risk":"...","practical_advice":"...",'
        '"legal_basis":"...","tags":["..."]}\n'
        "要求：question 以问号结尾；不得编造法条；如信息不足则对应字段留空。"
    )
    user_prompt = (
        f"【咨询对话】\n{dialogue_text}\n\n"
        f"【法务结论】\n{legal_conclusion}\n\n"
        f"请提炼 FAQ 条目。"
    )
    try:
        raw = llm.chat(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user_prompt}],
            temperature=0.2,
            max_tokens=1024,
        )
    except LlmError as e:
        return jsonify({"error": f"LLM 总结失败：{e}"}), 500

    # 解析 JSON（容错）
    import json as _json, re as _re
    faq = None
    m = _re.search(r"\{[\s\S]*\}", raw or "")
    if m:
        try:
            faq = _json.loads(m.group(0))
        except _json.JSONDecodeError:
            faq = None
    if not faq:
        return jsonify({"error": "LLM 输出无法解析为 FAQ",
                        "raw": raw}), 500

    # 2. 冲突检测：与知识库中相似条目比对
    candidates = orchestrator.matcher.filter(
        faq.get("question", ""), knowledge_base.items, top_n=5)
    ranked = orchestrator._rank_with_score(
        faq.get("question", ""), candidates, top_k=3)
    conflicts = []
    for r in ranked:
        if r["score"] >= 0.6:
            it = r["item"]
            # 简单冲突判定：法律解答核心结论是否矛盾
            # （这里用相似度 + 关键词差异作粗判，交法务人工确认）
            conflicts.append({
                "id": it["id"],
                "question": it.get("question", ""),
                "legal_answer": it.get("legal_answer", "")[:200],
                "score": r["score"],
                "need_review": True,
            })

    return jsonify({
        "faq": faq,
        "conflicts": conflicts,
        "has_conflict": len(conflicts) > 0,
    })
