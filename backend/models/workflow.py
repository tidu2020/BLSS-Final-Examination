"""业务流程模型：Session 和 WorkOrder。

Session：业务侧的咨询会话（多轮对话 + 材料 + AI 结论）
WorkOrder：业务提交给法务的工单（含完整留痕字段）

两者通过 submit_to_legal() 衔接：Session 打包成 WorkOrder。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import List, Dict, Optional

from backend.models.account import Account


class Session:
    """业务咨询会话。

    封装一次完整的业务咨询过程：
    - 多轮对话（question/answer 列表）
    - 上传的材料（filename/content）
    - AI 生成的最终结论
    - 会话状态（consulting/submitted/closed）

    会话可在业务侧持续对话，材料可分批上传；当业务选择"提交法务"时，
    由 submit_to_legal() 打包为 WorkOrder。
    """

    def __init__(self, user: Account):
        self.id: str = uuid.uuid4().hex[:12]
        self.user: Account = user
        self.dialogue: List[Dict] = []   # [{role, content, timestamp}]
        self.materials: List[Dict] = []   # [{filename, content, uploaded_at}]
        self.ai_conclusion: str = ""
        self.status: str = "consulting"   # consulting/submitted/closed
        self.created_at: str = datetime.now().isoformat(timespec="seconds")

    # ---------- 对话 ----------

    def add_dialogue(self, role: str, content: str) -> None:
        """追加一轮对话。

        Args:
            role: user（业务提问）/ assistant（AI 回复）
            content: 对话内容
        """
        self.dialogue.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })

    def add_question(self, question: str, answer: str) -> None:
        """业务提问 + AI 回复（一对）。"""
        self.add_dialogue("user", question)
        self.add_dialogue("assistant", answer)

    # ---------- 材料 ----------

    def add_material(self, filename: str, content: str) -> None:
        """上传一份材料。"""
        self.materials.append({
            "filename": filename,
            "content": content,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        })

    # ---------- 结论 ----------

    def set_ai_conclusion(self, conclusion: str) -> None:
        """设置 AI 综合结论（可由编排器填充）。"""
        self.ai_conclusion = conclusion

    # ---------- 状态流转 ----------

    def submit_to_legal(self) -> "WorkOrder":
        """打包为工单提交法务。

        会话状态变为 submitted，并返回新建的 WorkOrder。
        """
        if self.status != "consulting":
            raise RuntimeError(f"会话状态 {self.status} 不可提交")
        self.status = "submitted"
        return WorkOrder(self)

    def close(self) -> None:
        """关闭会话（业务选择"仅参考"）。"""
        self.status = "closed"

    # ---------- 序列化 ----------

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "user_id": self.user.id,
            "user_name": self.user.name,
            "dialogue": self.dialogue,
            "materials": self.materials,
            "ai_conclusion": self.ai_conclusion,
            "status": self.status,
            "created_at": self.created_at,
        }


class WorkOrder:
    """法务工单。

    由 Session.submit_to_legal() 创建，包含：
    - 业务侧数据：材料、对话、AI 结论
    - 留痕字段：提交人/提交时间/AI 审核时间
    - 法务侧数据：审阅人/审阅时间/法务结论/确认时间/入库条目
    - 状态：submitted_to_legal / reviewing / confirmed / archived
    """

    def __init__(self, session: Session):
        # 工单编号：WO-{年月}-{序号}
        self.id: str = ""
        # 业务侧数据
        self.materials: List[Dict] = list(session.materials)
        self.dialogue: List[Dict] = list(session.dialogue)
        self.ai_conclusion: str = session.ai_conclusion
        # 留痕字段（MVP 即填充）
        self.submitter: str = session.user.id
        self.submitter_name: str = session.user.name
        self.submitted_at: str = datetime.now().isoformat(timespec="seconds")
        self.ai_reviewed_at: str = self.submitted_at  # AI 结论在提交前已生成
        # 法务侧数据
        self.reviewer: str = ""
        self.reviewer_name: str = ""
        self.reviewed_at: str = ""
        self.legal_conclusion: str = ""
        self.confirmed_at: str = ""
        self.confirmed_faqs: List[Dict] = []
        # 状态
        self.status: str = "submitted_to_legal"
        # 关联会话
        self.session_id: str = session.id

    # ---------- 状态流转 ----------

    def start_review(self, reviewer: Account) -> None:
        """法务开始审核。"""
        if self.status != "submitted_to_legal":
            raise RuntimeError(f"工单状态 {self.status} 不可开始审核")
        self.reviewer = reviewer.id
        self.reviewer_name = reviewer.name
        self.reviewed_at = datetime.now().isoformat(timespec="seconds")
        self.status = "reviewing"

    def set_legal_conclusion(self, conclusion: str) -> None:
        """设置法务结论。"""
        self.legal_conclusion = conclusion

    def confirm(self, faqs: List[Dict]) -> None:
        """法务确认入库。

        Args:
            faqs: 拟入库的知识条目列表
        """
        self.confirmed_faqs = faqs
        self.confirmed_at = datetime.now().isoformat(timespec="seconds")
        self.status = "confirmed"

    def archive(self) -> None:
        """归档完成。"""
        self.status = "archived"

    # ---------- 序列化 ----------

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            # 业务侧
            "materials": self.materials,
            "dialogue": self.dialogue,
            "ai_conclusion": self.ai_conclusion,
            # 留痕
            "submitter": self.submitter,
            "submitter_name": self.submitter_name,
            "submitted_at": self.submitted_at,
            "ai_reviewed_at": self.ai_reviewed_at,
            # 法务侧
            "reviewer": self.reviewer,
            "reviewer_name": self.reviewer_name,
            "reviewed_at": self.reviewed_at,
            "legal_conclusion": self.legal_conclusion,
            "confirmed_at": self.confirmed_at,
            "confirmed_faqs": self.confirmed_faqs,
            # 状态
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "WorkOrder":
        """从字典反序列化。"""
        # 创建一个临时 Session 替代品（实际不使用）
        order = cls.__new__(cls)
        order.id = data.get("id", "")
        order.session_id = data.get("session_id", "")
        order.materials = data.get("materials", [])
        order.dialogue = data.get("dialogue", [])
        order.ai_conclusion = data.get("ai_conclusion", "")
        order.submitter = data.get("submitter", "")
        order.submitter_name = data.get("submitter_name", "")
        order.submitted_at = data.get("submitted_at", "")
        order.ai_reviewed_at = data.get("ai_reviewed_at", "")
        order.reviewer = data.get("reviewer", "")
        order.reviewer_name = data.get("reviewer_name", "")
        order.reviewed_at = data.get("reviewed_at", "")
        order.legal_conclusion = data.get("legal_conclusion", "")
        order.confirmed_at = data.get("confirmed_at", "")
        order.confirmed_faqs = data.get("confirmed_faqs", [])
        order.status = data.get("status", "submitted_to_legal")
        return order


class WorkOrderStore:
    """工单存储：JSON 文件持久化（每工单一文件）。

    存储路径：data/work_orders/{工单id}.json
    工单 id 由 store 在保存时分配。
    """

    def __init__(self, data_dir: str = "data/work_orders"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def _next_id(self) -> str:
        """生成下一个工单 id：WO-{年月}-{序号}。"""
        month = datetime.now().strftime("%Y%m")
        existing = self.list_all()
        seq = sum(
            1 for o in existing
            if o.id.startswith(f"WO-{month}-")
        ) + 1
        return f"WO-{month}-{seq:03d}"

    def save(self, order: WorkOrder) -> WorkOrder:
        """保存工单。若工单无 id 则分配。"""
        if not order.id:
            order.id = self._next_id()
        path = os.path.join(self.data_dir, f"{order.id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(order.to_dict(), f, ensure_ascii=False, indent=2)
        return order

    def get(self, order_id: str) -> Optional[WorkOrder]:
        """按 id 查询工单。"""
        path = os.path.join(self.data_dir, f"{order_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WorkOrder.from_dict(data)

    def list_all(self) -> List[WorkOrder]:
        """列出所有工单。"""
        orders = []
        if not os.path.exists(self.data_dir):
            return orders
        for fname in os.listdir(self.data_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.data_dir, fname)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    orders.append(WorkOrder.from_dict(data))
                except (json.JSONDecodeError, KeyError):
                    continue
        # 按提交时间降序
        orders.sort(key=lambda o: o.submitted_at, reverse=True)
        return orders

    def query(self, submitter: Optional[str] = None,
              status: Optional[str] = None,
              reviewer: Optional[str] = None) -> List[WorkOrder]:
        """条件查询工单。"""
        orders = self.list_all()
        if submitter:
            orders = [o for o in orders if o.submitter == submitter]
        if status:
            orders = [o for o in orders if o.status == status]
        if reviewer:
            orders = [o for o in orders if o.reviewer == reviewer]
        return orders
