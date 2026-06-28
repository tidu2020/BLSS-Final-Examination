"""ai 路由：AI 审核引擎对外接口。

接口：
- POST /api/ai/review  AI 审核
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.auth.decorator import require_login
from backend.ai.orchestrator import ReviewOrchestrator

bp = Blueprint("ai", __name__, url_prefix="/api/ai")

# 由 app 注入
orchestrator: ReviewOrchestrator = None  # type: ignore


def init_dependencies(orch: ReviewOrchestrator) -> None:
    global orchestrator
    orchestrator = orch


@bp.route("/review", methods=["POST"])
@require_login()
def review():
    """AI 审核。

    请求体：{"query": "...", "top_k": 5}
    返回：{"answer": "...", "results": [...], "disclaimer": "..."}
    """
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    top_k = int(data.get("top_k", 5))

    if not query:
        return jsonify({"error": "查询不能为空"}), 400

    result = orchestrator.review(query, top_k=top_k)
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
    })
