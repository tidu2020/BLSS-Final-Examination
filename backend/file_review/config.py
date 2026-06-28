"""合同审核模块配置。

桥接到 backend.config 的统一配置，同时为 Contract-Review-System 提供
dataclass 风格的 AppConfig / LLMConfig / SearchConfig。

设计要点：
- file_review 模块对外只依赖 backend.config.Config（环境变量来源）
- AppConfig 在创建时读取 backend.config，运行时一致
- 模块可独立运行：直接 import 此 config 即可使用
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# 模块自身目录（用于解析 data/prompts/output 相对路径）
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_central_config():
    """惰性加载 backend.config 单例。"""
    try:
        from backend.config import config as central
        return central
    except Exception:
        return None


@dataclass
class LLMConfig:
    """大模型配置。"""
    provider: str = "qwen"
    api_key: str = ""
    base_url: str = "https://ark.cn-beijing.volces.com/api/coding/v3"
    model: str = "ark-code-latest"
    temperature: float = 0.3
    max_tokens: int = 8192
    context_limit: int = 131072
    max_chunk_chars: int = 12000
    token_safety_ratio: float = 0.85

    @classmethod
    def from_central(cls) -> "LLMConfig":
        """从 backend.config 单例创建（保证配置一致）。"""
        central = _load_central_config()
        if central is None:
            return cls()
        return cls(
            provider=os.environ.get("LLM_PROVIDER", "ark"),
            api_key=central.LLM_API_KEY,
            base_url=central.LLM_BASE_URL,
            model=central.LLM_MODEL,
            temperature=central.LLM_TEMPERATURE,
            max_tokens=8192,  # 合同审核需要更长输出
            context_limit=int(os.environ.get("LLM_CONTEXT_LIMIT", "131072")),
            max_chunk_chars=int(os.environ.get("LLM_CHUNK_CHARS", "12000")),
            token_safety_ratio=0.85,
        )


@dataclass
class SearchConfig:
    """法律检索配置。"""
    enabled: bool = True
    search_api: str = "duckduckgo"
    api_key: str = ""
    primary_domain: str = "flk.npc.gov.cn"
    fallback_domains: list = field(
        default_factory=lambda: ["pkulaw.com", "wenshu.court.gov.cn"]
    )


@dataclass
class AppConfig:
    """合同审核整体配置。"""
    llm: LLMConfig = field(default_factory=LLMConfig.from_central)
    search: SearchConfig = field(default_factory=SearchConfig)
    data_dir: str = os.path.join(_MODULE_DIR, "data")
    prompts_dir: str = os.path.join(_MODULE_DIR, "prompts")
    output_dir: str = os.path.join(_MODULE_DIR, "output")

    def __post_init__(self):
        # 确保 output 目录存在
        os.makedirs(self.output_dir, exist_ok=True)


# 模块级默认配置
app_config = AppConfig()
