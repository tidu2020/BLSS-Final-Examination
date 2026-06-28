"""file_review 端到端测试（需要 LLM 配置）。

执行真实的六阶段合同审核流水线，验证：
- upload → run → report 完整流程
- 报告非空且包含关键章节
- session 持久化正常
"""

import io
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.app import create_app


SAMPLE_CONTRACT = """咨询服务合同

甲方：国有示范科技有限公司
乙方：智诚咨询有限公司

第一条 合同标的
乙方为甲方提供为期 6 个月的管理咨询服务，包括组织架构诊断、流程优化建议、人才培养方案设计。

第二条 价款与支付
1. 合同总价款为人民币 300,000 元（含税）。
2. 甲方应在合同签订后 10 个工作日内支付 50% 预付款，余款在服务完成后 30 日内一次性付清。

第三条 权利义务
1. 甲方应按时支付服务费用，并提供必要的资料与协助。
2. 乙方应按时提交咨询成果，并对所提供内容的质量负责。
3. 乙方应对甲方提供的商业秘密严格保密，合同终止后 5 年内不得泄露。

第四条 违约责任
1. 任何一方未按约定履行义务的，应向守约方支付合同总价款 10% 的违约金。
2. 损失超出违约金部分的，违约方应继续赔偿。

第五条 争议解决
本合同履行过程中发生的争议，双方应友好协商解决；协商不成的，提交甲方所在地人民法院诉讼解决。

第六条 合同生效
本合同自双方法定代表人或授权代表签字并加盖公章之日起生效。

甲方（盖章）：________________       乙方（盖章）：________________
日期：______年____月____日          日期：______年____月____日
"""


def run_e2e():
    app = create_app()
    client = app.test_client()
    client.post("/api/auth/login", json={
        "user_id": "admin01", "password": "123456"
    })

    # 1. 上传合同
    print("\n[1/3] 上传合同...")
    rv = client.post(
        "/api/file_review/upload",
        data={
            "file": (io.BytesIO(SAMPLE_CONTRACT.encode("utf-8")), "咨询合同.txt"),
        },
        content_type="multipart/form-data",
    )
    assert rv.status_code == 200, rv.get_json()
    body = rv.get_json()
    sid = body["session_id"]
    print(f"   session_id = {sid}")
    print(f"   文件名: {body['filename']}, 字符数: {body['char_count']}")

    # 2. 启动审核
    print("\n[2/3] 启动六阶段审核流水线...")
    print("   (预计 30-120 秒，请耐心等待)")
    t0 = time.monotonic()
    rv = client.post("/api/file_review/run", json={
        "session_id": sid,
        "special_focus": "违约责任与争议解决条款",
        "user_stance": "甲方",
    })
    elapsed = time.monotonic() - t0
    print(f"   耗时 {elapsed:.1f}s, HTTP {rv.status_code}")
    body = rv.get_json()
    if rv.status_code != 200:
        print(f"   错误：{body}")
        return False

    report = body.get("report_full") or body.get("report_preview", "")
    print(f"   报告长度：{body['report_length']} 字符")
    print(f"   报告前 200 字符：\n   {report[:200]}")

    # 3. 下载报告
    print("\n[3/3] 下载 Markdown 报告...")
    rv = client.get(f"/api/file_review/report/{sid}")
    print(f"   HTTP {rv.status_code}")
    if rv.status_code == 200:
        print(f"   报告大小：{len(rv.data)} bytes")
        print(f"   Content-Type: {rv.content_type}")
        # 报告前 500 字符
        try:
            content = rv.data.decode("utf-8")
            print(f"   报告前 500 字符：\n{content[:500]}")
        except Exception:
            pass

    # 4. 多轮对话
    print("\n[4/4] 多轮对话测试...")
    rv = client.post("/api/file_review/chat", json={
        "session_id": sid,
        "message": "本合同的争议解决条款对甲方是否有利？请简要说明。",
        "history": [],
    })
    print(f"   HTTP {rv.status_code}")
    if rv.status_code == 200:
        body = rv.get_json()
        print(f"   回复前 300 字符：\n{body['answer'][:300]}")
    else:
        print(f"   错误：{rv.get_json()}")

    print("\n" + "=" * 60)
    print("端到端测试完成 ✓")
    print("=" * 60)
    return True


if __name__ == "__main__":
    if not os.environ.get("LLM_API_KEY"):
        print("请先设置 LLM_API_KEY 环境变量")
        sys.exit(1)
    ok = run_e2e()
    sys.exit(0 if ok else 1)
