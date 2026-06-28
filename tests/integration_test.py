"""端到端集成测试：覆盖完整业务流程。

执行：python -m tests.integration_test
前提：后端运行在 http://127.0.0.1:5000

流程：
1. 业务登录 → 咨询 → 上传材料 → 提交工单
2. 法务登录 → 查看工单 → 开始审核 → 填写结论
3. 法务校验拟入库条目 → 确认入库
4. 法务确认工单 → 归档 → 下载归档包
"""

from __future__ import annotations

import io
import sys
import urllib.request
import urllib.parse
import json
import http.cookiejar


BASE = "http://127.0.0.1:5000"


class HttpClient:
    """带 cookie 的简易 HTTP 客户端。"""

    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies))

    def _req(self, method, path, body=None, is_form=False):
        url = BASE + path
        data = None
        headers = {}
        if body is not None:
            if is_form:
                data = body
            else:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers)
        try:
            resp = self.opener.open(req, timeout=15)
            return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"raw": raw}

    def get(self, path):
        return self._req("GET", path)

    def delete(self, path):
        return self._req("DELETE", path)

    def post_json(self, path, body=None):
        return self._req("POST", path, body=body)

    def post_form(self, path, fields):
        # multipart 简化：用 form-urlencoded 不行，需 multipart
        # 这里手动构造 multipart
        boundary = "----testboundary123"
        parts = []
        for key, (filename, content) in fields.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(
                f'Content-Disposition: form-data; name="{key}"; '
                f'filename="{filename}"\r\n'.encode())
            parts.append(b"Content-Type: text/plain\r\n\r\n")
            parts.append(content.encode("utf-8") if isinstance(content, str)
                        else content)
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        req = urllib.request.Request(
            BASE + path, data=data, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            resp = self.opener.open(req, timeout=15)
            return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"raw": raw}


def assert_ok(status, label):
    assert status == 200, f"{label} 失败：HTTP {status}"
    print(f"  [OK] {label}")


def main():
    print("=" * 60)
    print("端到端集成测试")
    print("=" * 60)

    client = HttpClient()

    # ========== 1. 业务流程 ==========
    print("\n--- 1. 业务登录 + 咨询 + 上传 + 提交 ---")
    status, res = client.post_json("/api/auth/login",
                                    {"user_id": "business01", "password": "123456"})
    assert_ok(status, "业务登录")
    assert res["role"] == "business"
    print(f"       当前用户：{res['name']}（{res['role']}）")

    # 咨询
    status, res = client.post_json("/api/business/consult",
                                    {"question": "合同相对方要求第三方付款可以吗？"})
    assert_ok(status, "AI 咨询")
    assert res["answer"], "回复不应为空"
    assert len(res["results"]) > 0, "应有参考结果"
    print(f"       回复长度：{len(res['answer'])} 字，"
          f"参考 {len(res['results'])} 条")
    print(f"       Top1：{res['results'][0]['id']} "
          f"({res['results'][0]['question'][:30]}...) "
          f"分数 {res['results'][0]['score']:.3f}")

    # 上传材料
    status, res = client.post_form("/api/business/upload", {
        "file": ("测试合同.txt", "这是一份测试合同的内容。\n甲方：XX公司\n乙方：YY公司"),
    })
    assert_ok(status, "上传材料")
    print(f"       已上传 {res['filename']}（{res['size']} 字）")

    # 提交工单
    status, res = client.post_json("/api/business/submit_order",
                                    {"note": "请法务重点关注付款条款"})
    assert_ok(status, "提交工单")
    order_id = res["order_id"]
    print(f"       工单号：{order_id}")

    # 查看我的工单
    status, res = client.get("/api/business/my_orders")
    assert_ok(status, "查询我的工单")
    assert any(o["id"] == order_id for o in res), "工单应在我的列表中"
    print(f"       我的工单共 {len(res)} 件")

    # ========== 2. 法务流程 ==========
    print("\n--- 2. 法务登录 + 审核工单 ---")
    client2 = HttpClient()
    status, res = client2.post_json("/api/auth/login",
                                     {"user_id": "legal01", "password": "123456"})
    assert_ok(status, "法务登录")
    print(f"       当前用户：{res['name']}（{res['role']}）")

    # 查看工单列表
    status, res = client2.get("/api/legal/orders")
    assert_ok(status, "查询工单列表")
    assert any(o["id"] == order_id for o in res), "工单应在法务列表中"
    print(f"       工单列表共 {len(res)} 件")

    # 查看工单详情
    status, res = client2.get(f"/api/legal/orders/{order_id}")
    assert_ok(status, "查询工单详情")
    assert res["materials"], "应有材料"
    assert res["dialogue"], "应有对话"
    print(f"       材料 {len(res['materials'])} 份，"
          f"对话 {len(res['dialogue'])} 轮")

    # 开始审核
    status, res = client2.post_json(f"/api/legal/orders/{order_id}/review",
                                     {"action": "start"})
    assert_ok(status, "开始审核")
    assert res["status"] == "reviewing"
    print(f"       状态：{res['status']}，审核人：{res['reviewer_name']}")

    # 保存法务结论
    status, res = client2.post_json(f"/api/legal/orders/{order_id}/review",
                                     {"action": "save",
                                      "conclusion": "经审核，第三方付款需谨慎，建议补充书面协议并核实第三方资质。"})
    assert_ok(status, "保存法务结论")
    print(f"       法务结论已保存")

    # ========== 3. 校验 + 入库 ==========
    print("\n--- 3. 校验拟入库条目 + 确认入库 ---")
    test_item = {
        "question": "合同第三方付款需要注意什么？",
        "legal_answer": "第三方付款需谨慎，建议核实第三方资质并签订补充协议。",
        "compliance_risk": "若第三方资质不明，可能存在资金挪用风险。",
        "practical_advice": "要求相对方提供第三方书面确认函，并保留付款凭证。",
        "legal_basis": "《中华人民共和国民法典》第五百一十四条规定。",
        "tags": ["合同", "付款"],
        "month": "202507",
    }

    # 校验
    status, res = client2.post_json("/api/legal/validate", test_item)
    assert_ok(status, "校验条目")
    print(f"       passed={res['passed']}, errors={res['errors']}, "
          f"warnings={res['warnings']}")
    assert res["passed"], "应通过校验"

    # 确认入库（遇 409 二次确认时自动 force）
    status, res = client2.post_json("/api/legal/confirm",
                                     {"order_id": order_id, "item": test_item})
    if status == 409 and res.get("need_force"):
        print(f"       二次确认：{res['warnings']}")
        status, res = client2.post_json("/api/legal/confirm", {
            "order_id": order_id, "item": test_item, "force": True,
        })
    assert_ok(status, "确认入库")
    faq_id = res["faq_id"]
    print(f"       已入库，FAQ ID：{faq_id}")

    # 验证知识库新增成功
    status, res = client2.get(f"/api/kb/items/{faq_id}")
    assert_ok(status, "查询新入库条目")
    assert res["source_work_order_id"] == order_id
    print(f"       关联工单：{res['source_work_order_id']}")

    # 测试软警告场景：缺标签
    bad_item = dict(test_item)
    bad_item["question"] = "测试缺标签的条目"
    bad_item["tags"] = []
    status, res = client2.post_json("/api/legal/validate", bad_item)
    assert_ok(status, "校验缺标签")
    assert res["passed"], "缺标签是软警告，应 passed=True"
    assert any("标签" in w for w in res["warnings"])
    print(f"       软警告：{res['warnings']}")

    # 测试硬错误场景：缺问题
    err_item = dict(test_item)
    err_item["question"] = ""
    status, res = client2.post_json("/api/legal/validate", err_item)
    assert_ok(status, "校验缺问题")
    assert not res["passed"], "缺问题应 passed=False"
    print(f"       硬错误：{res['errors']}")

    # 确认工单完成
    status, res = client2.post_json(f"/api/legal/orders/{order_id}/confirm",
                                     {"faqs": [{"id": faq_id,
                                                "question": test_item["question"]}]})
    assert_ok(status, "确认工单完成")
    assert res["status"] == "confirmed"
    print(f"       工单状态：{res['status']}，"
          f"入库 {res['confirmed_count']} 条")

    # ========== 4. 归档 ==========
    print("\n--- 4. 归档 + 下载归档包 ---")
    status, res = client2.post_json(f"/api/legal/orders/{order_id}/archive")
    assert_ok(status, "归档工单")
    assert res["status"] == "archived"
    print(f"       工单状态：{res['status']}")

    # 查看归档列表
    status, res = client2.get("/api/archive/orders")
    assert_ok(status, "查询归档列表")
    assert any(o["id"] == order_id for o in res), "归档列表应有此工单"
    print(f"       归档库共 {len(res)} 件")

    # 下载归档包（验证返回 Markdown）
    url = f"{BASE}/api/archive/orders/{order_id}/package"
    req = urllib.request.Request(url)
    resp = client2.opener.open(req, timeout=15)
    content = resp.read().decode("utf-8")
    assert order_id in content, "归档包应含工单号"
    assert "业务咨询对话" in content, "归档包应含对话"
    assert "法务结论" in content, "归档包应含法务结论"
    print(f"       归档包长度：{len(content)} 字，含工单号+对话+结论")

    # ========== 5. 管理员流程 ==========
    print("\n--- 5. 管理员登录 + 知识库管理 ---")
    client3 = HttpClient()
    status, res = client3.post_json("/api/auth/login",
                                     {"user_id": "admin01", "password": "123456"})
    assert_ok(status, "管理员登录")
    print(f"       当前用户：{res['name']}（{res['role']}）")

    # 统计
    status, res = client3.get("/api/kb/stats")
    assert_ok(status, "查询统计")
    print(f"       知识库共 {res['total']} 条，"
          f"{len(res['months'])} 月，{len(res['categories'])} 分类")

    # 账户列表
    status, res = client3.get("/api/auth/accounts")
    assert_ok(status, "查询账户列表")
    print(f"       账户共 {len(res)} 个")

    # 权限校验：业务不能访问法务工单列表
    status, res = client.post_json("/api/auth/login",
                                    {"user_id": "business01", "password": "123456"})
    assert_ok(status, "业务重新登录")
    status, _ = client.get("/api/legal/orders")
    assert status == 403, f"业务访问法务列表应 403，实际 {status}"
    print(f"  [OK] 业务访问法务接口被拒（403）")

    # 权限校验：法务不能管理知识库
    status, _ = client2.delete(f"/api/kb/items/FAQ-202312-001")
    assert status == 403, f"法务删除知识应 403，实际 {status}"
    print(f"  [OK] 法务管理知识库被拒（403）")

    print(f"\n{'=' * 60}")
    print("端到端测试全部通过 ✓")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"\n[ERROR] 无法连接后端：{e}")
        print("请先启动后端：python -m backend.app")
        sys.exit(2)
