"""ai 路由：AI 审核引擎对外接口。

接口：
- POST /api/ai/review          AI 审核（三级兜底）
- POST /api/ai/feedback         用户对引用的反馈
- GET  /api/ai/sources/<id>     查看知识库引用详情

合同审核已迁移至 backend.file_review 模块（独立工作）。
"""

from __future__ import annotations

import logging
from typing import List, Dict

from flask import Blueprint, jsonify, request

from backend.auth.decorator import require_login, require_role
from backend.ai.orchestrator import ReviewOrchestrator
from backend.ai.llm_client import LlmClient, LlmError

logger = logging.getLogger(__name__)

bp = Blueprint("ai", __name__, url_prefix="/api/ai")

# 由 app 注入
orchestrator: ReviewOrchestrator = None  # type: ignore
llm_client: LlmClient = None  # type: ignore


def init_dependencies(orch: ReviewOrchestrator,
                     llm: LlmClient = None) -> None:
    global orchestrator, llm_client
    orchestrator = orch
    llm_client = llm


@bp.route("/review", methods=["POST"])
@require_login()
def review():
    """AI 审核（三级兜底）。

    请求体：{"query": "...", "top_k": 5, "extra_context": "..."}
    返回：{
        "answer": "...",
        "results": [...],
        "candidates_count": int,
        "disclaimer": "...",
        "mode": "rag+llm" / "rag" / "llm-only",
        "relevance": "high" / "mid" / "low" / "none",
        "sources": [...]   # 引用的知识库条目（含完整字段供点开查看）
    }
    """
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    top_k = int(data.get("top_k", 5))
    extra_context = data.get("extra_context") or ""

    if not query:
        return jsonify({"error": "查询不能为空"}), 400

    result = orchestrator.review(query, top_k=top_k,
                                 extra_context=extra_context)
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
        "candidates_count": result["candidates_count"],
        "disclaimer": result["disclaimer"],
        "mode": result["mode"],
        "relevance": result["relevance"],
        "sources": result["sources"],
    })


@bp.route("/feedback", methods=["POST"])
@require_login()
def feedback():
    """用户对知识库引用的反馈标注（是否相关）。

    请求体：{"faq_id": "...", "relevant": true/false}
    """
    data = request.get_json() or {}
    faq_id = (data.get("faq_id") or "").strip()
    if not faq_id:
        return jsonify({"error": "faq_id 不能为空"}), 400
    relevant = bool(data.get("relevant", True))

    orchestrator.record_feedback(faq_id, relevant)
    return jsonify({"status": "ok", "faq_id": faq_id, "relevant": relevant})


@bp.route("/sources/<faq_id>", methods=["GET"])
@require_login()
def get_source(faq_id: str):
    """查看某条知识库引用的完整内容。"""
    item = orchestrator.kb.get(faq_id)
    if not item:
        return jsonify({"error": "知识库条目不存在"}), 404
    return jsonify(item)
