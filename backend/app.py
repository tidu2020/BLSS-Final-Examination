"""Flask 应用入口。

启动方式：
    python -m backend.app
或：
    python backend/app.py

依赖注入：所有路由模块的 store/kb/orchestrator 在 create_app() 中初始化。
"""

from __future__ import annotations

import os

from flask import Flask, send_from_directory, jsonify, request

from backend.config import config
from backend.models.account import AccountStore
from backend.models.workflow import WorkOrderStore
from backend.knowledge.models import KnowledgeBase
from backend.ai.orchestrator import ReviewOrchestrator
from backend.ai.llm_client import LlmClient

# 路由模块
from backend.auth import routes as auth_routes
from backend.business import routes as business_routes
from backend.legal import routes as legal_routes
from backend.ai import routes as ai_routes
from backend.knowledge import routes as knowledge_routes
from backend.report import routes as report_routes
from backend.archive import routes as archive_routes
from backend.file_review import routes as file_review_routes


def create_app() -> Flask:
    """创建并配置 Flask 应用。"""
    app = Flask(
        __name__,
        static_folder=None,  # 禁用默认 static，手动指定
    )
    app.config.from_object(config)
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

    # 1. 初始化核心依赖
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.WORK_ORDERS_DIR, exist_ok=True)
    os.makedirs(config.ARCHIVE_DIR, exist_ok=True)

    account_store = AccountStore(config.ACCOUNTS_PATH)
    account_store.load()
    work_order_store = WorkOrderStore(config.WORK_ORDERS_DIR)
    knowledge_base = KnowledgeBase(
        data_path=config.KNOWLEDGE_BASE_PATH,
        source_md=config.FAQ_SOURCE,
    )
    knowledge_base.load()

    # 大模型客户端（RAG + LLM 生成增强）
    llm_client = LlmClient(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        model=config.LLM_MODEL,
        timeout=config.LLM_TIMEOUT,
        temperature=config.LLM_TEMPERATURE,
        max_tokens=config.LLM_MAX_TOKENS,
    )
    if llm_client.available:
        print(f"[LLM] 已启用：{config.LLM_MODEL} @ {config.LLM_BASE_URL}")
    else:
        print("[LLM] 未配置或不可用，将使用纯 RAG 模式")

    orchestrator = ReviewOrchestrator(knowledge_base, llm_client=llm_client)

    # 2. 注入到各路由模块
    auth_routes.init_store(account_store)
    business_routes.init_dependencies(account_store, work_order_store,
                                     orchestrator)
    legal_routes.init_dependencies(account_store, work_order_store,
                                   knowledge_base, orchestrator)
    ai_routes.init_dependencies(orchestrator, llm_client)
    knowledge_routes.init_dependencies(knowledge_base, orchestrator)
    report_routes.init_dependencies(work_order_store, knowledge_base)
    archive_routes.init_dependencies(work_order_store)

    # 合同审核模块：独立运行，自管理依赖（不需要外部注入）
    # file_review.routes 自带 LLMClient 与 AppConfig
    # 此处仅创建输出目录，不强制注入
    try:
        from backend.file_review.config import app_config as _fr_cfg
        os.makedirs(_fr_cfg.output_dir, exist_ok=True)
        print(f"[file_review] 输出目录：{_fr_cfg.output_dir}")
        if _fr_cfg.llm.api_key:
            print(f"[file_review] LLM 已就绪：{_fr_cfg.llm.model}")
        else:
            print("[file_review] LLM 未配置，审核功能不可用")
    except Exception as e:
        print(f"[file_review] 初始化警告：{e}")

    # 3. 注册 API 蓝图
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(business_routes.bp)
    app.register_blueprint(legal_routes.bp)
    app.register_blueprint(ai_routes.bp)
    app.register_blueprint(knowledge_routes.bp)
    app.register_blueprint(report_routes.bp)
    app.register_blueprint(archive_routes.bp)
    app.register_blueprint(file_review_routes.bp)

    # 4. 前端静态文件服务
    frontend_dir = config.FRONTEND_DIR

    @app.route("/")
    def index():
        """根路径返回登录页。"""
        return send_from_directory(frontend_dir, "login.html")

    @app.route("/<page>.html")
    def serve_page(page: str):
        """页面路由。"""
        # 安全：禁止路径穿越
        if not all(c.isalnum() or c in "_-" for c in page):
            return jsonify({"error": "非法页面名"}), 400
        return send_from_directory(frontend_dir, f"{page}.html")

    @app.route("/static/<path:filename>")
    def serve_static(filename: str):
        """静态资源（css/js）。"""
        return send_from_directory(
            os.path.join(frontend_dir, "static"), filename)

    # 5. 全局错误处理
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": str(e.description)}), 400

    @app.errorhandler(404)
    def not_found(e):
        # API 返回 JSON，页面返回 index
        if request.path.startswith("/api/"):
            return jsonify({"error": "接口不存在"}), 404
        return jsonify({"error": "资源不存在"}), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "服务器内部错误",
                        "detail": str(e.original_exception)
                        if hasattr(e, "original_exception") else str(e)}), 500

    return app


# 模块级 app 实例（供 flask run 使用）
app = create_app()


if __name__ == "__main__":
    print("=" * 60)
    print("业法协同系统 — 启动中")
    print(f"知识库：{config.KNOWLEDGE_BASE_PATH}")
    print(f"前端目录：{config.FRONTEND_DIR}")
    print(f"访问地址：http://127.0.0.1:{config.PORT}")
    print("=" * 60)
    app.run(host=config.HOST, port=config.PORT,
            debug=config.DEBUG, use_reloader=False)
