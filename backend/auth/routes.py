"""auth 路由：登录/登出/当前用户。

接口：
- POST /api/auth/login    登录
- POST /api/auth/logout   登出
- GET  /api/auth/me       当前用户信息
- GET  /api/auth/accounts 账户列表（仅管理员）
- POST /api/auth/accounts 新增账户（仅管理员）
- DELETE /api/auth/accounts/<id> 删除账户（仅管理员）
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from backend.auth.decorator import require_role, current_user
from backend.models.account import AccountStore, account_from_dict, \
    create_account, ROLE_CLASSES

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# 全局账户存储（由 app 初始化时注入）
account_store: AccountStore = None  # type: ignore


def init_store(store: AccountStore) -> None:
    """由 app 注入账户存储。"""
    global account_store
    account_store = store


@bp.route("/login", methods=["POST"])
def login():
    """登录。

    请求体：{"user_id": "...", "password": "..."}
    成功：{"role": "...", "name": "...", "id": "..."}
    失败：401
    """
    data = request.get_json() or {}
    user_id = (data.get("user_id") or "").strip()
    password = data.get("password") or ""

    if not user_id or not password:
        return jsonify({"error": "账号或密码不能为空"}), 400

    user = account_store.get(user_id)
    if not user or not user.login(password):
        return jsonify({"error": "账号或密码错误"}), 401

    session["user"] = {
        "id": user.id,
        "name": user.name,
        "role": user.role(),
        "department": user.department,
    }
    return jsonify({
        "id": user.id,
        "name": user.name,
        "role": user.role(),
        "department": user.department,
    })


@bp.route("/logout", methods=["POST"])
def logout():
    """登出。"""
    session.pop("user", None)
    return jsonify({"status": "ok"})


@bp.route("/me")
def me():
    """当前登录用户信息。"""
    user = current_user()
    if not user:
        return jsonify({"error": "未登录"}), 401
    return jsonify(user)


@bp.route("/accounts")
@require_role("admin")
def list_accounts():
    """账户列表（仅管理员）。"""
    accounts = [
        {
            "id": a.id,
            "name": a.name,
            "role": a.role(),
            "department": a.department,
        }
        for a in account_store.list_all()
    ]
    return jsonify(accounts)


@bp.route("/accounts", methods=["POST"])
@require_role("admin")
def create_account_route():
    """新增账户（仅管理员）。

    请求体：{"role": "business|legal|admin",
             "user_id": "...", "name": "...",
             "password": "...", "department": "..."}
    """
    data = request.get_json() or {}
    role = (data.get("role") or "").strip()
    user_id = (data.get("user_id") or "").strip()
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    department = (data.get("department") or "").strip()

    if role not in ROLE_CLASSES:
        return jsonify({"error": f"无效角色：{role}"}), 400
    if not user_id or not name or not password:
        return jsonify({"error": "user_id/name/password 不能为空"}), 400

    try:
        acct = account_store.add(role, user_id, name, password, department)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

    return jsonify({
        "id": acct.id,
        "name": acct.name,
        "role": acct.role(),
        "department": acct.department,
    }), 201


@bp.route("/accounts/<user_id>", methods=["DELETE"])
@require_role("admin")
def delete_account(user_id: str):
    """删除账户（仅管理员）。"""
    if user_id == current_user()["id"]:
        return jsonify({"error": "不能删除自己"}), 400
    if account_store.delete(user_id):
        return jsonify({"status": "ok"})
    return jsonify({"error": "账户不存在"}), 404
