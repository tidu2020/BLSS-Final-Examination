"""账户与权限模型。

三角色账户体系：
- BusinessUser（业务）：发起咨询、上传材料、提交工单
- LegalUser（法务）：审核、校验、确认入库（不可直接增删改知识库）
- AdminUser（管理员）：可直接 CRUD 知识库，拥有最高权限

通过继承 Account 抽象基类，体现 OOP 多态与封装。
"""

from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from typing import Optional, Dict, List


class Account(ABC):
    """账户抽象基类。

    封装账户通用属性（id/name/密码哈希）和登录逻辑。
    权限通过 can_xxx() 方法定义，子类按角色覆写。
    """

    def __init__(self, user_id: str, name: str, pwd_hash: str,
                 department: str = ""):
        self.id = user_id
        self.name = name
        self._pwd_hash = pwd_hash
        self.department = department

    # ---------- 抽象方法 ----------

    @abstractmethod
    def role(self) -> str:
        """返回角色标识。"""
        ...

    # ---------- 通用方法 ----------

    def login(self, password: str) -> bool:
        """密码校验。

        使用 SHA-256 哈希存储密码（生产版应换 bcrypt）。
        """
        return self._pwd_hash == self._hash_password(password)

    @staticmethod
    def _hash_password(password: str) -> str:
        """SHA-256 哈希。"""
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict:
        """序列化为字典（不含密码哈希）。"""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role(),
            "department": self.department,
        }

    # ---------- 权限方法（默认 False，子类覆写为 True）----------

    def can_submit(self) -> bool:
        """能否提交咨询/工单。"""
        return False

    def can_review(self) -> bool:
        """能否审核工单。"""
        return False

    def can_confirm(self) -> bool:
        """能否确认入库。"""
        return False

    def can_search_kb(self) -> bool:
        """能否检索知识库。"""
        return False

    def can_manage_kb(self) -> bool:
        """能否直接 CRUD 知识库。"""
        return False

    def can_view_all_orders(self) -> bool:
        """能否查看全部工单（不限自己提交的）。"""
        return False

    def can_manage_accounts(self) -> bool:
        """能否管理账户。"""
        return False

    def can_export_archive(self) -> bool:
        """能否导出归档。"""
        return False


class BusinessUser(Account):
    """业务用户。"""

    def role(self) -> str:
        return "business"

    def can_submit(self) -> bool:
        return True

    def can_search_kb(self) -> bool:
        return True  # 业务可检索（用于咨询场景）


class LegalUser(Account):
    """法务用户。"""

    def role(self) -> str:
        return "legal"

    def can_review(self) -> bool:
        return True

    def can_confirm(self) -> bool:
        return True

    def can_search_kb(self) -> bool:
        return True

    def can_export_archive(self) -> bool:
        return True


class AdminUser(Account):
    """管理员用户。"""

    def role(self) -> str:
        return "admin"

    def can_review(self) -> bool:
        return True

    def can_confirm(self) -> bool:
        return True

    def can_search_kb(self) -> bool:
        return True

    def can_manage_kb(self) -> bool:
        return True

    def can_view_all_orders(self) -> bool:
        return True

    def can_manage_accounts(self) -> bool:
        return True

    def can_export_archive(self) -> bool:
        return True


# ---------- 工厂 ----------

# 角色 -> 类的映射
ROLE_CLASSES: Dict[str, type] = {
    "business": BusinessUser,
    "legal": LegalUser,
    "admin": AdminUser,
}


def create_account(role: str, user_id: str, name: str,
                   password: str, department: str = "") -> Account:
    """工厂方法：按角色创建账户。

    Args:
        role: 角色标识（business/legal/admin）
        user_id: 账户 ID
        name: 显示名
        password: 明文密码（内部会哈希）
        department: 部门

    Returns:
        对应角色的 Account 子类实例

    Raises:
        ValueError: 未知角色
    """
    cls = ROLE_CLASSES.get(role)
    if not cls:
        raise ValueError(f"未知角色：{role}")
    pwd_hash = Account._hash_password(password)
    return cls(user_id, name, pwd_hash, department)


def account_from_dict(data: Dict) -> Account:
    """从字典反序列化为 Account 实例。"""
    cls = ROLE_CLASSES.get(data.get("role"))
    if not cls:
        raise ValueError(f"未知角色：{data.get('role')}")
    return cls(
        user_id=data["id"],
        name=data["name"],
        pwd_hash=data["pwd_hash"],
        department=data.get("department", ""),
    )


# ---------- 账户存储 ----------

class AccountStore:
    """账户存储：JSON 持久化。

    职责：
    - 加载/保存账户列表
    - 按 id 查询账户
    - 增删改账户（管理员权限）
    - 初始化默认账户（首次运行）
    """

    DEFAULT_ACCOUNTS = [
        ("business01", "业务员小王", "business", "123456", "业务一部"),
        ("legal01", "法务老李", "legal", "123456", "法务部"),
        ("admin01", "管理员", "admin", "123456", "信息中心"),
    ]

    def __init__(self, data_path: str = "data/accounts.json"):
        self.data_path = data_path
        self.accounts: Dict[str, Account] = {}

    def load(self) -> "AccountStore":
        """加载账户。若文件不存在则初始化默认账户。"""
        if os.path.exists(self.data_path):
            with open(self.data_path, "r", encoding="utf-8") as f:
                data_list = json.load(f)
            for data in data_list:
                acct = account_from_dict(data)
                self.accounts[acct.id] = acct
        else:
            self._init_defaults()
        return self

    def save(self) -> None:
        """持久化。"""
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        data_list = [
            {
                "id": a.id,
                "name": a.name,
                "role": a.role(),
                "department": a.department,
                "pwd_hash": a._pwd_hash,
            }
            for a in self.accounts.values()
        ]
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(data_list, f, ensure_ascii=False, indent=2)

    def _init_defaults(self) -> None:
        """初始化默认账户。"""
        for uid, name, role, pwd, dept in self.DEFAULT_ACCOUNTS:
            acct = create_account(role, uid, name, pwd, dept)
            self.accounts[uid] = acct
        self.save()

    def get(self, user_id: str) -> Optional[Account]:
        """按 id 查询账户。"""
        return self.accounts.get(user_id)

    def add(self, role: str, user_id: str, name: str,
            password: str, department: str = "") -> Account:
        """新增账户。"""
        if user_id in self.accounts:
            raise ValueError(f"账户已存在：{user_id}")
        acct = create_account(role, user_id, name, password, department)
        self.accounts[user_id] = acct
        self.save()
        return acct

    def delete(self, user_id: str) -> bool:
        """删除账户。"""
        if user_id in self.accounts:
            del self.accounts[user_id]
            self.save()
            return True
        return False

    def list_all(self) -> List[Account]:
        """返回所有账户。"""
        return list(self.accounts.values())
