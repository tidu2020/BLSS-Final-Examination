"""大模型客户端：OpenAI 兼容协议（chat/completions）。

使用 Python 标准库 urllib 实现，无外部依赖，符合课程"标准库"要求。
兼容豆包 / Ark / OpenAI / 公司私有部署等任意 OpenAI 协议兼容服务。

配置项（见 config.py）：
- LLM_API_KEY：API 密钥（生产环境务必用环境变量）
- LLM_BASE_URL：API 基址，如 https://ark.cn-beijing.volces.com/api/coding/v3
- LLM_MODEL：模型名，如 ark-code-latest
"""

from __future__ import annotations

import json
import logging
from typing import List, Dict, Optional

import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


class LlmError(Exception):
    """大模型调用异常。"""


class LlmClient:
    """OpenAI 兼容协议的大模型客户端。

    通过 chat/completions 接口生成回复。
    失败时抛出 LlmError，由调用方决定是否回退到规则拼装。

    设计为可注入（依赖注入），方便测试时替换为 mock。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int = 30,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ):
        self.api_key = api_key
        # 规范化 base_url：去掉尾部斜杠
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    @property
    def available(self) -> bool:
        """是否可用（配置完整）。"""
        return bool(self.api_key and self.base_url and self.model)

    def chat(
        self,
        messages: List[Dict],
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """调用 chat/completions 生成回复。

        Args:
            messages: OpenAI 消息列表，如
                [{"role": "system", "content": "..."},
                 {"role": "user", "content": "..."}]
            temperature: 采样温度，法务场景建议 0.2~0.4
            max_tokens: 最大生成 token 数

        Returns:
            模型生成的文本

        Raises:
            LlmError: 调用失败（网络/鉴权/服务端错误）
        """
        if not self.available:
            raise LlmError("大模型未配置（缺少 api_key/base_url/model）")

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
        )
        req.add_header("Content-Type", "application/json; charset=utf-8")
        # OpenAI 协议标准鉴权头
        req.add_header("Authorization", f"Bearer {self.api_key}")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise LlmError(
                f"大模型 HTTP {e.code} 错误：{body[:200]}"
            ) from None
        except urllib.error.URLError as e:
            raise LlmError(f"大模型网络错误：{e.reason}") from None
        except Exception as e:  # 超时等
            raise LlmError(f"大模型调用异常：{e}") from None

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LlmError(f"大模型响应非 JSON：{raw[:200]}") from e

        # 解析 OpenAI 响应格式
        choices = result.get("choices") or []
        if not choices:
            raise LlmError(f"大模型返回无 choices：{raw[:200]}")

        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if not content:
            raise LlmError(f"大模型返回空内容：{raw[:200]}")

        return content


# ---------- 独立测试入口 ----------

if __name__ == "__main__":
    """命令行测试：验证大模型连通性。

    用法：
        python -m backend.ai.llm_client
    """
    from backend.config import config

    if not config.LLM_API_KEY:
        print("未配置 LLM_API_KEY，请在环境变量或 config.py 中设置")
        raise SystemExit(1)

    client = LlmClient(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        model=config.LLM_MODEL,
        timeout=config.LLM_TIMEOUT,
    )
    print(f"测试大模型：{config.LLM_MODEL}")
    print(f"Base URL：{config.LLM_BASE_URL}")
    print("-" * 60)

    messages = [
        {
            "role": "system",
            "content": "你是国有企业法务助手，请简洁专业地回答。",
        },
        {
            "role": "user",
            "content": "合同相对方要求第三方付款，这种安排需要注意什么？",
        },
    ]
    try:
        reply = client.chat(messages, temperature=0.3, max_tokens=512)
        print("调用成功，回复：")
        print(reply)
    except LlmError as e:
        print(f"调用失败：{e}")
