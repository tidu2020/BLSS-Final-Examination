"""应用配置。"""

from __future__ import annotations

import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    """应用配置。"""

    # Flask
    SECRET_KEY = os.environ.get(
        "LEGAL_SECRET_KEY",
        "dev-secret-key-请生产环境替换",
    )
    DEBUG = True
    HOST = "0.0.0.0"
    PORT = 5000

    # 数据路径
    DATA_DIR = os.path.join(BASE_DIR, "data")
    ACCOUNTS_PATH = os.path.join(DATA_DIR, "accounts.json")
    KNOWLEDGE_BASE_PATH = os.path.join(DATA_DIR, "knowledge_base.json")
    FAQ_SOURCE = os.path.join(BASE_DIR, "FAQ_四部分版_最终.md")
    WORK_ORDERS_DIR = os.path.join(DATA_DIR, "work_orders")
    ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
    MERGE_REJECTIONS_PATH = os.path.join(DATA_DIR, "merge_rejections.json")

    # 前端静态文件
    FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

    # 上传
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXTENSIONS = {".txt", ".docx", ".md"}

    # 大模型（OpenAI 兼容协议）
    # 测试用：豆包 Ark；生产环境：换成公司私有部署大模型
    # 生产环境务必用环境变量注入，不要把密钥写进源码
    LLM_API_KEY = os.environ.get(
        "LLM_API_KEY",
        "",  # 请通过环境变量注入，勿将密钥提交到代码仓库
    )
    LLM_BASE_URL = os.environ.get(
        "LLM_BASE_URL",
        "https://ark.cn-beijing.volces.com/api/coding/v3",
    )
    LLM_MODEL = os.environ.get("LLM_MODEL", "ark-code-latest")
    LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "60"))
    # 生成温度：法务场景要求严谨，取低值
    LLM_TEMPERATURE = 0.3
    LLM_MAX_TOKENS = 1024


config = Config()
