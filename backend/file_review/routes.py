"""合同审核模块路由（独立蓝图）。

接口：
- POST /api/file_review/upload      上传合同文件，解析文本
- POST /api/file_review/run        执行六阶段审核流水线
- GET  /api/file_review/report/<sid>  下载 Markdown 报告
- POST /api/file_review/chat        针对已审合同进行多轮问答

设计要点：
- 完全独立于 ai 模块（自带 LLMClient）
- session 用 JSON 文件持久化（backend/data/file_review_sessions/）
- 不依赖 backend.ai.orchestrator，可单独运行
"""

from __future__ import annotations

import json
import logging
import os
import uuid
import threading
from typing import Dict, Optional

from flask import Blueprint, jsonify, request, send_file, current_app

from backend.auth.decorator import require_login, require_role
from backend.file_review.config import app_config, AppConfig
from backend.file_review.src.pipeline import ContractReviewPipeline
from backend.file_review.src.llm_client import LLMClient, ContextLengthExceededError

logger = logging.getLogger(__name__)

bp = Blueprint("file_review", __name__, url_prefix="/api/file_review")

# session 持久化目录
SESSIONS_DIR = os.path.join(app_config.output_dir, "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

# 内存中的会话缓存（同一进程内有效）
_sessions: Dict[str, dict] = {}
_lock = threading.Lock()


def _load_session(sid: str) -> Optional[dict]:
    """加载会话（先内存，再磁盘）。"""
    with _lock:
        if sid in _sessions:
            return _sessions[sid]
    path = os.path.join(SESSIONS_DIR, f"{sid}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _lock:
            _sessions[sid] = data
        return data
    except Exception as e:
        logger.warning("加载会话 %s 失败：%s", sid, e)
        return None


def _save_session(sid: str, data: dict) -> None:
    """保存会话到内存 + 磁盘。"""
    with _lock:
        _sessions[sid] = data
    path = os.path.join(SESSIONS_DIR, f"{sid}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("保存会话 %s 失败：%s", sid, e)


# ---------- 工具函数 ----------

def _extract_text_from_upload(file_storage) -> str:
    """从上传文件中提取文本（支持 txt/md/docx）。"""
    filename = file_storage.filename or ""
    ext = os.path.splitext(filename)[1].lower()

    if ext in (".txt", ".md"):
        raw = file_storage.read()
        for enc in ("utf-8", "gbk", "gb2312", "utf-16"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    if ext == ".docx":
        try:
            from docx import Document
        except ImportError as e:
            raise ValueError("读取 .docx 需要 python-docx 库，请安装") from e
        file_storage.stream.seek(0)
        doc = Document(file_storage)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    raise ValueError(f"不支持的文件类型：{ext}（仅支持 .txt/.md/.docx）")


# ---------- 路由 ----------

@bp.route("/upload", methods=["POST"])
@require_role("business", "legal", "admin")
def upload_contract():
    """上传合同文件，解析文本并创建会话。

    返回：{"session_id": "...", "filename": "...", "char_count": 1234, "preview": "..."}
    """
    if "file" not in request.files:
        return jsonify({"error": "未上传文件"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "文件名为空"}), 400

    try:
        text = _extract_text_from_upload(f)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("文件解析失败")
        return jsonify({"error": f"文件解析失败：{e}"}), 500

    if not text.strip():
        return jsonify({"error": "文件内容为空"}), 400

    sid = uuid.uuid4().hex[:12]
    _save_session(sid, {
        "session_id": sid,
        "filename": f.filename,
        "contract_text": text,
        "report_md": "",
        "status": "uploaded",
        "history": [],  # 多轮对话历史
    })

    return jsonify({
        "session_id": sid,
        "filename": f.filename,
        "char_count": len(text),
        "preview": text[:200].replace("\n", " "),
    })


@bp.route("/run", methods=["POST"])
@require_role("business", "legal", "admin")
def run_review():
    """执行六阶段合同审核流水线。

    请求体：{
        "session_id": "...",         # 上传后获得的会话 ID
        "special_focus": "...",       # 特别关注点（可选）
        "user_stance": "甲方/乙方",   # 审核立场（可选，默认 auto）
        "contract_context": "..."    # 合同背景（可选）
    }
    """
    data = request.get_json() or {}
    sid = data.get("session_id")
    if not sid:
        return jsonify({"error": "缺少 session_id"}), 400

    sess = _load_session(sid)
    if not sess:
        return jsonify({"error": "会话不存在或已过期"}), 404
    if not sess.get("contract_text"):
        return jsonify({"error": "会话缺少合同文本"}), 400

    config = AppConfig()
    if not config.llm.api_key:
        return jsonify({"error": "LLM 未配置（缺少 LLM_API_KEY）"}), 503

    pipeline = ContractReviewPipeline(
        config=config,
        step_callback=lambda pct, text: logger.info("[审核进度 %d%%] %s", pct, text),
    )

    try:
        report_md = pipeline.run(
            sess["contract_text"],
            special_focus=data.get("special_focus", ""),
            user_stance=data.get("user_stance", "auto"),
            contract_context=data.get("contract_context", ""),
        )
    except ContextLengthExceededError as e:
        logger.warning("合同超长：%s", e)
        return jsonify({"error": f"合同过长，超出上下文窗口：{e}"}), 413
    except Exception as e:
        logger.exception("合同审核失败")
        return jsonify({"error": f"合同审核失败：{e}"}), 500

    sess["report_md"] = report_md
    sess["status"] = "reviewed"
    _save_session(sid, sess)

    # 截取报告前 4000 字符返回（避免响应过大）
    preview = report_md[:4000]
    return jsonify({
        "session_id": sid,
        "status": "reviewed",
        "report_preview": preview,
        "report_length": len(report_md),
        "report_full": report_md if len(report_md) <= 20000 else "",
    })


@bp.route("/report/<sid>", methods=["GET"])
@require_login()
def download_report(sid: str):
    """下载 Markdown 报告文件。"""
    sess = _load_session(sid)
    if not sess:
        return jsonify({"error": "会话不存在"}), 404
    if not sess.get("report_md"):
        return jsonify({"error": "尚未生成报告，请先执行审核"}), 400

    # 写临时文件并返回
    out_path = os.path.join(SESSIONS_DIR, f"{sid}_report.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(sess["report_md"])

    base_name = os.path.splitext(sess.get("filename", "contract"))[0]
    download_name = f"{base_name}_审核报告.md"
    return send_file(
        out_path,
        as_attachment=True,
        download_name=download_name,
        mimetype="text/markdown; charset=utf-8",
    )


@bp.route("/chat", methods=["POST"])
@require_role("business", "legal", "admin")
def chat_about_contract():
    """针对已审合同的多轮问答。

    请求体：{
        "session_id": "...",
        "message": "...",          # 本轮问题
        "history": [...]           # 历史对话（可选）
    }
    """
    data = request.get_json() or {}
    sid = data.get("session_id")
    message = (data.get("message") or "").strip()
    history = data.get("history") or []

    if not sid:
        return jsonify({"error": "缺少 session_id"}), 400
    if not message:
        return jsonify({"error": "message 不能为空"}), 400

    sess = _load_session(sid)
    if not sess:
        return jsonify({"error": "会话不存在"}), 404
    if not sess.get("contract_text"):
        return jsonify({"error": "会话缺少合同文本"}), 400

    config = AppConfig()
    if not config.llm.api_key:
        return jsonify({"error": "LLM 未配置"}), 503

    # 系统提示：基于合同全文 + 已有审核报告（若有）
    system_prompt = (
        "你是国有企业合同审核助手。用户已上传合同并完成审核，"
        "现在针对合同细节进行多轮问答。\n\n"
        "审核范围：法律效力、商业可行性、违约责任、合规风险。\n"
        "回答要求：基于已上传合同具体条款作答，给出法律依据与可执行修改建议。"
    )

    user_parts = []
    user_parts.append(f"【合同全文】\n{sess['contract_text'][:6000]}")
    if sess.get("report_md"):
        user_parts.append(f"【已有审核报告】\n{sess['report_md'][:4000]}")
    user_parts.append(f"【本轮问题】\n{message}")

    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-10:]:
        role = h.get("role", "user")
        content = h.get("content", "")
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": "\n\n".join(user_parts)})

    try:
        llm = LLMClient(config.llm)
        answer = llm.chat(
            "\n".join(m["content"] for m in messages if m["role"] == "system"),
            "\n\n".join(m["content"] for m in messages if m["role"] == "user"),
            temperature=0.2,
            max_tokens=2048,
        )
    except Exception as e:
        logger.warning("合同多轮对话失败：%s", e)
        return jsonify({"error": f"对话失败：{e}"}), 500

    # 保存到历史
    sess["history"].append({"role": "user", "content": message})
    sess["history"].append({"role": "assistant", "content": answer})
    _save_session(sid, sess)

    return jsonify({
        "answer": answer,
        "session_id": sid,
    })


@bp.route("/sessions", methods=["GET"])
@require_login()
def list_sessions():
    """列出当前用户的会话（简化版）。"""
    sessions_meta = []
    for fname in os.listdir(SESSIONS_DIR):
        if not fname.endswith(".json"):
            continue
        sid = fname[:-5]
        sess = _load_session(sid)
        if not sess:
            continue
        sessions_meta.append({
            "session_id": sid,
            "filename": sess.get("filename", ""),
            "status": sess.get("status", ""),
            "char_count": len(sess.get("contract_text", "")),
        })
    # 按会话 ID 倒序
    sessions_meta.sort(key=lambda x: x["session_id"], reverse=True)
    return jsonify({"sessions": sessions_meta})
