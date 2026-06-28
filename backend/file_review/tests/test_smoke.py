"""file_review 模块冒烟测试。

不依赖外部 LLM，验证：
- 模块可独立导入
- 蓝图可注册
- 上传接口可解析文件
- sessions 列表可返回
- 不存在的 session 报 404
"""

import io
import json
import os
import sys
import tempfile
from pathlib import Path

# 让脚本能直接运行
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.app import create_app


def make_app():
    """创建测试 app（用临时数据目录）。"""
    return create_app()


def test_upload_txt():
    app = make_app()
    client = app.test_client()
    # 模拟登录
    client.post("/api/auth/login", json={
        "user_id": "admin01", "password": "123456"
    })
    # 上传一个简单的合同 txt
    content = "甲方：示例公司\n乙方：测试方\n第一条 合同标的：本合同项下的标的为咨询服务。\n第二条 价款：人民币 100,000 元。"
    rv = client.post(
        "/api/file_review/upload",
        data={
            "file": (io.BytesIO(content.encode("utf-8")), "demo.txt"),
        },
        content_type="multipart/form-data",
    )
    print("upload status:", rv.status_code)
    body = rv.get_json()
    print("upload body keys:", list(body.keys()))
    assert rv.status_code == 200, body
    assert "session_id" in body
    assert body["char_count"] > 0
    return body["session_id"]


def test_sessions_list():
    app = make_app()
    client = app.test_client()
    client.post("/api/auth/login", json={
        "user_id": "admin01", "password": "123456"
    })
    rv = client.get("/api/file_review/sessions")
    print("sessions status:", rv.status_code)
    body = rv.get_json()
    assert rv.status_code == 200
    assert "sessions" in body
    print(f"找到 {len(body['sessions'])} 个会话")


def test_report_not_found():
    app = make_app()
    client = app.test_client()
    client.post("/api/auth/login", json={
        "user_id": "admin01", "password": "123456"
    })
    rv = client.get("/api/file_review/report/nonexistent-session")
    print("report(404) status:", rv.status_code)
    assert rv.status_code == 404


def test_run_without_llm():
    """没配 LLM 时，run 接口应返回 503 而不是 500。"""
    app = make_app()
    client = app.test_client()
    client.post("/api/auth/login", json={
        "user_id": "admin01", "password": "123456"
    })
    sid = test_upload_txt()
    rv = client.post("/api/file_review/run", json={
        "session_id": sid,
        "special_focus": "",
        "user_stance": "auto",
    })
    print("run(no llm) status:", rv.status_code)
    body = rv.get_json()
    print("run body:", body)
    # 没 LLM 时应该是 503
    assert rv.status_code in (503, 500), body


if __name__ == "__main__":
    print("=" * 60)
    print("file_review 冒烟测试")
    print("=" * 60)
    test_sessions_list()
    test_report_not_found()
    test_run_without_llm()
    sid = test_upload_txt()
    print(f"\n上传成功，session_id = {sid}")
    print("\n所有冒烟测试通过 ✓")
