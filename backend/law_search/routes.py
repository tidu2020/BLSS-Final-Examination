"""
法律搜索 API 路由

接口：
- POST /api/law/search  法条搜索（自然语言）
- POST /api/law/verify  法条验证（精确查询）
"""

from flask import Blueprint, jsonify, request
from backend.law_search import LawVerifier, search_laws

bp = Blueprint("law_search", __name__, url_prefix="/api/law")

_verifier = LawVerifier()


@bp.route("/search", methods=["POST"])
def search():
    """
    法条搜索（自然语言输入）

    请求体：
    {
        "query": "民法典关于合同违约金怎么规定",
        "num_results": 5
    }

    返回：
    {
        "success": true,
        "query": "...",
        "results": [...]
    }
    """
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "error": "请输入搜索关键词"}), 400

    num_results = min(max(int(data.get("num_results", 5)), 1), 20)

    try:
        results = _verifier.query(query, num_results=num_results)
        return jsonify({
            "success": True,
            "query": query,
            "count": len(results),
            "results": [r.to_dict() for r in results],
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"搜索失败: {str(e)}"}), 500


@bp.route("/verify", methods=["POST"])
def verify():
    """
    法条精确验证

    请求体：
    {
        "law_name": "中华人民共和国民法典",
        "article_num": "第143条"
    }

    返回：
    {
        "success": true,
        "result": {...}
    }
    """
    data = request.get_json(silent=True) or {}
    law_name = (data.get("law_name") or "").strip()
    article_num = (data.get("article_num") or "").strip()

    if not law_name:
        return jsonify({"success": False, "error": "请输入法律名称"}), 400
    if not article_num:
        return jsonify({"success": False, "error": "请输入条文序号"}), 400

    try:
        result = _verifier.verify(law_name, article_num)
        if result:
            return jsonify({
                "success": True,
                "result": result.to_dict(),
            })
        return jsonify({
            "success": False,
            "error": f"未找到《{law_name}》{article_num}",
        }), 404
    except Exception as e:
        return jsonify({"success": False, "error": f"查询失败: {str(e)}"}), 500


@bp.route("/lookup", methods=["POST"])
def lookup():
    """
    法律名称查询

    请求体：
    {
        "keyword": "劳动合同法",
        "only_valid": true
    }

    返回：
    {
        "success": true,
        "results": [...]
    }
    """
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"success": False, "error": "请输入搜索关键词"}), 400

    only_valid = data.get("only_valid", True)
    num_results = min(max(int(data.get("num_results", 10)), 1), 20)

    try:
        results = search_laws(keyword, only_valid=only_valid)
        return jsonify({
            "success": True,
            "keyword": keyword,
            "count": len(results),
            "results": [r.to_dict() for r in results[:num_results]],
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"查询失败: {str(e)}"}), 500