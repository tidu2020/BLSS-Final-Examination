"""冒烟测试：验证核心模块串联。

执行：python -m tests.smoke_test
覆盖：
- FAQ 预处理
- KnowledgeBase 加载
- 双算法检索
- 校验器 6 项规则
- 账户体系
- 工单流程
"""

from __future__ import annotations

import os
import sys

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_preprocessor_and_kb():
    """测试 FAQ 预处理和知识库加载。"""
    from backend.knowledge.models import KnowledgeBase

    kb = KnowledgeBase()
    kb.load()

    assert kb.count() == 67, f"预期 67 条，实际 {kb.count()}"
    assert len(kb.all_months()) >= 10, "月份数量异常"
    assert len(kb.all_categories()) >= 5, "分类数量异常"

    # 测试按 id 查询
    item = kb.get("FAQ-202312-001")
    assert item is not None, "FAQ-202312-001 应存在"
    assert "卡通" in item["question"] or "著作权" in item["legal_answer"], \
        "首条应为卡通形象著作权问题"
    print(f"[OK] 知识库：{kb.count()} 条，{len(kb.all_months())} 月，"
          f"{len(kb.all_categories())} 分类")


def test_review_orchestrator():
    """测试 AI 编排器。"""
    from backend.knowledge.models import KnowledgeBase
    from backend.ai.orchestrator import ReviewOrchestrator

    kb = KnowledgeBase()
    kb.load()
    orch = ReviewOrchestrator(kb)

    # 测试 1：合同第三方付款
    result = orch.review("合同相对方要求第三方付款可以吗", top_k=3)
    assert result["candidates_count"] > 0, "应有候选"
    assert len(result["results"]) == 3, "应返回 3 条"
    top1 = result["results"][0]
    assert "第三方付款" in top1["question"] or "第三方" in top1["question"], \
        f"Top1 应命中第三方付款问题，实际：{top1['question']}"
    print(f"[OK] 编排器-合同：Top1 = {top1['id']} ({top1['question'][:30]})")

    # 测试 2：滑倒案例链
    result = orch.review("商场顾客滑倒责任", top_k=5)
    top_ids = [r["id"] for r in result["results"]]
    has_064 = any("064" in i for i in top_ids) or any("065" in i for i in top_ids)
    assert has_064, f"应命中 Q64-67 滑倒案例，实际 Top5：{top_ids}"
    print(f"[OK] 编排器-滑倒：Top5 = {top_ids}")

    # 测试 3：未命中
    result = orch.review("今天天气怎么样啊", top_k=3)
    # 即使有候选也应该返回（关键词很少但可能有单字命中）
    print(f"[OK] 编排器-未命中：候选 {result['candidates_count']} 条")


def test_validator():
    """测试校验器 6 项规则。"""
    from backend.knowledge.models import KnowledgeBase
    from backend.ai.orchestrator import ReviewOrchestrator
    from backend.legal.validator import FaqValidator

    kb = KnowledgeBase()
    kb.load()
    orch = ReviewOrchestrator(kb)
    validator = FaqValidator(kb, retriever=orch.retriever)

    # 用例 1：完整合法条目（应通过）
    good_item = {
        "id": "TEST-001",
        "question": "测试问题？",
        "legal_answer": "测试法律解答",
        "compliance_risk": "测试合规风险",
        "practical_advice": "测试实操建议",
        "legal_basis": "《测试法》第一条：测试条款。",
        "tags": ["测试"],
    }
    result = validator.validate(good_item)
    assert result["passed"], f"合法条目应通过，错误：{result['errors']}"
    print(f"[OK] 校验器-通过：errors={result['errors']}, "
          f"warnings={result['warnings']}")

    # 用例 2：缺问题（硬错误）
    no_question = dict(good_item)
    no_question["question"] = ""
    result = validator.validate(no_question)
    assert not result["passed"], "缺问题应阻止入库"
    assert any("问题" in e for e in result["errors"]), \
        f"应提示问题为空，实际：{result['errors']}"
    print(f"[OK] 校验器-缺问题：{result['errors']}")

    # 用例 3：缺法条（硬错误）
    no_basis = dict(good_item)
    no_basis["legal_basis"] = ""
    result = validator.validate(no_basis)
    assert not result["passed"], "缺法条应阻止入库"
    print(f"[OK] 校验器-缺法条：{result['errors']}")

    # 用例 4：法条格式不规范（软警告）
    bad_format = dict(good_item)
    bad_format["legal_basis"] = "这里没有法条格式"
    result = validator.validate(bad_format)
    assert result["passed"], "法条格式问题是警告，应通过"
    assert any("法条格式" in w for w in result["warnings"]), \
        f"应警告法条格式，实际：{result['warnings']}"
    print(f"[OK] 校验器-法条格式警告：{result['warnings']}")

    # 用例 5：无标签（软警告）
    no_tags = dict(good_item)
    no_tags["tags"] = []
    result = validator.validate(no_tags)
    assert result["passed"], "无标签是警告，应通过"
    print(f"[OK] 校验器-无标签警告：{result['warnings']}")

    # 用例 6：问题无问号（软警告）
    no_qmark = dict(good_item)
    no_qmark["question"] = "测试问题没有问号"
    result = validator.validate(no_qmark)
    assert result["passed"], "无问号是警告，应通过"
    assert any("问号" in w for w in result["warnings"]), \
        f"应警告无问号，实际：{result['warnings']}"
    print(f"[OK] 校验器-无问号警告：{result['warnings']}")

    # 用例 7：与现有条目重复（软警告）
    duplicate_item = dict(good_item)
    duplicate_item["question"] = kb.items[0]["question"]
    result = validator.validate(duplicate_item)
    assert result["passed"], "重复是警告，应通过"
    print(f"[OK] 校验器-重复警告：{result['warnings'][:1]}")


def test_accounts():
    """测试账户体系。"""
    from backend.models.account import AccountStore, BusinessUser, \
        LegalUser, AdminUser

    store = AccountStore()
    store.load()

    # 默认 3 个账户
    assert len(store.list_all()) >= 3, "应有 3 个默认账户"

    b = store.get("business01")
    assert b is not None and isinstance(b, BusinessUser), "业务账户异常"
    assert b.login("123456"), "密码校验失败"
    assert not b.login("wrong"), "错误密码不应通过"
    assert b.can_submit() and not b.can_review(), "业务权限异常"

    l = store.get("legal01")
    assert isinstance(l, LegalUser), "法务账户异常"
    assert l.can_review() and l.can_confirm(), "法务权限异常"
    assert not l.can_manage_kb(), "法务不应能管理知识库"

    a = store.get("admin01")
    assert isinstance(a, AdminUser), "管理员账户异常"
    assert a.can_manage_kb() and a.can_view_all_orders(), "管理员权限异常"
    print(f"[OK] 账户体系：业务/法务/管理员权限边界正确")


def test_workflow():
    """测试工单流程。"""
    from backend.models.account import AccountStore
    from backend.models.workflow import Session, WorkOrder, WorkOrderStore

    store = AccountStore()
    store.load()
    business_user = store.get("business01")

    # 创建会话
    session = Session(business_user)
    session.add_question("合同第三方付款可以吗？", "AI 回复：参考 Q3...")
    session.add_material("合同.txt", "合同内容...")
    session.set_ai_conclusion("综合结论：需法务审核")

    # 提交法务
    order = session.submit_to_legal()
    assert order.submitter == "business01"
    assert len(order.dialogue) == 2  # 一问一答
    assert len(order.materials) == 1
    assert order.status == "submitted_to_legal"
    print(f"[OK] 工单流程：业务侧打包正确（{len(order.dialogue)} 轮对话，"
          f"{len(order.materials)} 份材料）")

    # 法务审核
    legal_user = store.get("legal01")
    order.start_review(legal_user)
    assert order.status == "reviewing"
    assert order.reviewer == "legal01"
    print(f"[OK] 工单流程：法务开始审核（reviewer={order.reviewer_name}）")

    # 确认入库
    order.set_legal_conclusion("法务结论：可以，但需补充协议")
    order.confirm([{"id": "FAQ-NEW-001", "question": "新问题？"}])
    assert order.status == "confirmed"
    assert len(order.confirmed_faqs) == 1
    print(f"[OK] 工单流程：确认入库（{len(order.confirmed_faqs)} 条 FAQ）")


def test_reader():
    """测试数据读取器。"""
    from backend.models.reader import reader_factory, ContractReader, \
        LetterReader, TopicReader, ProcurementReader, TextReader

    # 工厂分发
    assert isinstance(reader_factory("采购合同.docx"), ContractReader)
    assert isinstance(reader_factory("对外函件.txt"), LetterReader)
    assert isinstance(reader_factory("上会议题.docx"), TopicReader)
    assert isinstance(reader_factory("采购文件.docx"), ProcurementReader)
    assert isinstance(reader_factory("notes.txt"), TextReader)
    print("[OK] 读取器：工厂分发正确")


def main():
    print("=" * 60)
    print("智能法务系统 — 冒烟测试")
    print("=" * 60)

    tests = [
        ("知识库加载", test_preprocessor_and_kb),
        ("AI 编排器", test_review_orchestrator),
        ("校验器 6 项", test_validator),
        ("账户体系", test_accounts),
        ("工单流程", test_workflow),
        ("数据读取器", test_reader),
    ]

    passed = 0
    failed = 0
    for name, test_func in tests:
        print(f"\n--- {name} ---")
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"测试结果：{passed} 通过，{failed} 失败")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
