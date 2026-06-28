"""权限装饰器：@require_role。

在路由层强制角色权限。
未登录返回 401；权限不足返回 403。
"""

from __future__ import annotations

import functools
from typing import Callable, Tuple

from flask import jsonify, session


def current_user() -> dict:
    """获取当前登录用户（session 中）。"""
    return session.get("user")


def require_role(*roles: str) -> Callable:
    """路由权限装饰器。

    Args:
        *roles: 允许访问的角色（business/legal/admin）

    用法：
        @bp.route("/admin")
        @require_role("admin")
        def admin_only(): ...
    """

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return jsonify({"error": "未登录"}), 401
            if user.get("role") not in roles:
                return jsonify({
                    "error": "权限不足",
                    "required": list(roles),
                    "actual": user.get("role"),
                }), 403
            return f(*args, **kwargs)

        return wrapper

    return decorator


def require_login() -> Callable:
    """仅要求登录（不限角色）。"""
    return require_role("business", "legal", "admin")
