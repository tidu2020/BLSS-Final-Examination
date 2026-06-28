"""合同审核模块自带的 LLM 客户端（标准库实现）。

不依赖 openai 库，使用 urllib 直接调用 OpenAI 兼容协议。
为保持 file_review 模块独立性，与 backend.ai.llm_client 实现解耦。

提供：
- LLMClient：核心客户端，含 LRU 缓存 + JSON 修复 + 重试
- estimate_tokens：粗略 token 估算
- ContextLengthExceededError：上下文超限异常
- _repair_json：LLM 输出 JSON 自动修复
"""

from __future__ import annotations

import json
import re
import time
import hashlib
import logging
import urllib.request
import urllib.error
from functools import lru_cache
from typing import Optional, Dict, Any, Tuple, Iterator

from ..config import LLMConfig

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文 1.5 token，英文按词估算）。"""
    if not text:
        return 0
    chinese_chars = sum(
        1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f'
    )
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.25 + len(text.split()) * 1.3)


def _repair_json(raw: str) -> dict:
    """LLM 输出 JSON 修复：去 markdown 包裹、修尾随逗号、单引号转双引号。"""
    if not raw:
        return {"clause_reviews": [], "structure_optimizations": [],
                "department_action_items": [], "supplementary_notes": [],
                "search_keywords": []}

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    repaired = raw.strip()
    if repaired.startswith("```json"):
        repaired = repaired[7:]
    elif repaired.startswith("```"):
        repaired = repaired[3:]
    if repaired.endswith("```"):
        repaired = repaired[:-3]
    repaired = repaired.strip()

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        repaired = raw[start:end + 1]
        # 清理控制字符
        repaired = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', "", repaired)
        # 修尾随逗号
        repaired = re.sub(r',\s*}', '}', repaired)
        repaired = re.sub(r',\s*]', ']', repaired)

        # 单引号转双引号
        def _fix_single_quotes(s: str) -> str:
            in_string = False
            quote_char = None
            result = []
            i = 0
            while i < len(s):
                c = s[i]
                if c == '\\':
                    result.append(c)
                    if i + 1 < len(s):
                        result.append(s[i + 1])
                        i += 2
                    continue
                elif c == '"' and not in_string:
                    in_string = True
                    quote_char = '"'
                    result.append(c)
                elif c == '"' and in_string and quote_char == '"':
                    in_string = False
                    quote_char = None
                    result.append(c)
                elif c == "'" and not in_string:
                    result.append('"')
                elif c == "'" and in_string and quote_char == "'":
                    in_string = False
                    quote_char = None
                    result.append('"')
                elif c == "'" and in_string:
                    result.append('\\\'')
                else:
                    result.append(c)
                i += 1
            return ''.join(result)

        repaired = _fix_single_quotes(repaired)

        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(repaired, strict=False)
        except json.JSONDecodeError:
            pass

    logger.warning("JSON 修复失败，返回空字典。原始响应前200字符: %s", raw[:200])
    return {"clause_reviews": [], "structure_optimizations": [],
            "department_action_items": [], "supplementary_notes": [],
            "search_keywords": []}


class ContextLengthExceededError(Exception):
    """上下文窗口超限异常。"""

    def __init__(self, estimated_tokens: int, limit: int):
        self.estimated_tokens = estimated_tokens
        self.limit = limit
        super().__init__(
            f"预估 token ({estimated_tokens}) 超出上下文窗口 ({limit})"
        )


class LLMClient:
    """OpenAI 兼容协议的 LLM 客户端（标准库 urllib 实现）。

    特性：
    - LRU 缓存：相同输入命中缓存直接返回
    - JSON 修复：chat_json 自动修复 LLM 输出格式
    - 重试机制：可重试错误自动重试 2 次
    - 上下文超限检测：抛出 ContextLengthExceededError
    """

    MAX_RETRIES = 2
    RETRY_DELAYS = [2, 5]
    CACHE_MAXSIZE = 100

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()
        self._cache: Dict[str, str] = {}
        self._cache_order: list = []

    @property
    def available(self) -> bool:
        """是否配置完整可用。"""
        return bool(self.config.api_key and self.config.base_url and self.config.model)

    # ---------- 缓存 ----------

    def _cache_key(self, system_prompt: str, user_prompt: str) -> str:
        raw = f"{self.config.model}|{system_prompt}|{user_prompt}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def _cache_put(self, key: str, value: str) -> None:
        if len(self._cache) >= self.CACHE_MAXSIZE:
            old_key = self._cache_order.pop(0)
            self._cache.pop(old_key, None)
        self._cache[key] = value
        self._cache_order.append(key)

    def _cached_chat(self, system_prompt: str, user_prompt: str,
                     temperature: Optional[float] = None,
                     max_tokens: Optional[int] = None) -> str:
        cache_key = self._cache_key(system_prompt, user_prompt)
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.info("LLM cache hit for %s...", system_prompt[:50])
            return cached
        result = self.chat(system_prompt, user_prompt, temperature, max_tokens)
        self._cache_put(cache_key, result)
        return result

    # ---------- 核心 HTTP 调用 ----------

    def _http_chat(self, messages: list, temperature: float,
                  max_tokens: int, stream: bool = False) -> dict:
        """通过 urllib 调用 chat/completions 接口。"""
        if not self.available:
            raise RuntimeError("LLM 未配置（缺少 api_key/base_url/model）")

        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,  # 标准库 urllib 不支持流式
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("Authorization", f"Bearer {self.config.api_key}")

        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            err = RuntimeError(f"LLM HTTP {e.code} 错误：{body[:300]}")
            err.error_str = body.lower()
            raise err from None
        except urllib.error.URLError as e:
            err = RuntimeError(f"LLM 网络错误：{e.reason}")
            err.error_str = str(e.reason).lower()
            raise err from None

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM 响应非 JSON：{raw[:200]}") from e

    # ---------- 公共 API ----------

    def check_prompt_tokens(self, system_prompt: str,
                             user_prompt: str) -> Tuple[int, bool]:
        """检查 prompt 是否超过上下文窗口阈值。"""
        estimated = estimate_tokens(system_prompt) + estimate_tokens(user_prompt)
        limit = int(self.config.context_limit * self.config.token_safety_ratio)
        return estimated, limit < estimated

    def chat(self, system_prompt: str, user_prompt: str,
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None) -> str:
        """调用 chat/completions 生成回复。

        Returns:
            模型生成的文本

        Raises:
            ContextLengthExceededError: 上下文超限
            RuntimeError: 调用失败（重试耗尽）
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        effective_temp = temperature if temperature is not None else self.config.temperature
        effective_max = max_tokens or self.config.max_tokens

        last_error = None
        t0 = time.monotonic()
        for attempt in range(self.MAX_RETRIES):
            try:
                result = self._http_chat(messages, effective_temp, effective_max)
                choices = result.get("choices") or []
                if not choices:
                    raise RuntimeError(f"LLM 返回无 choices：{json.dumps(result)[:200]}")
                message = choices[0].get("message") or {}
                content = message.get("content") or ""
                finish_reason = choices[0].get("finish_reason", "")

                elapsed = time.monotonic() - t0
                logger.info(
                    "LLM chat completed in %.1fs, finish_reason=%s, model=%s, output_chars=%d",
                    elapsed, finish_reason, self.config.model, len(content),
                )

                # 输出截断：尝试增大 max_tokens 重试
                if finish_reason == "length" and effective_max < self.config.context_limit:
                    increased = min(effective_max * 2, self.config.context_limit)
                    logger.warning(
                        "输出截断，max_tokens %d -> %d", effective_max, increased
                    )
                    effective_max = increased
                    continue

                return content
            except ContextLengthExceededError:
                raise
            except Exception as e:
                last_error = e
                error_str = getattr(e, "error_str", str(e).lower())

                # 上下文超限
                if any(kw in error_str for kw in [
                    "context_length_exceeded", "context length",
                    "maximum context", "reduce the length",
                ]):
                    raise ContextLengthExceededError(
                        estimated_tokens=0, limit=self.config.context_limit,
                    ) from e

                # 可重试错误
                is_retriable = any(kw in error_str for kw in [
                    "429", "ratelimit", "toomanyrequests", "timeout",
                    "500", "502", "503", "504", "connection",
                ])

                if attempt < self.MAX_RETRIES - 1 and is_retriable:
                    delay = self.RETRY_DELAYS[attempt]
                    logger.warning(
                        "API 重试 %d/%d，%ds 后...", attempt + 1, self.MAX_RETRIES, delay
                    )
                    time.sleep(delay)
                else:
                    break

        raise last_error

    def chat_json(self, system_prompt: str, user_prompt: str,
                  temperature: Optional[float] = None,
                  max_tokens: Optional[int] = None) -> dict:
        """调用 LLM 并解析为 JSON（自动修复格式）。"""
        json_system = (
            system_prompt
            + "\n\n【必须严格遵守】只输出纯 JSON 对象，以 { 开头、} 结尾。"
            + "不要用 ```json 包裹，不要加任何解释文字。"
        )

        raw = self._cached_chat(
            system_prompt=json_system,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("JSON 解析失败，尝试修复... 响应前200字符: %s", raw[:200])
            parsed = _repair_json(raw)

        if "clause_reviews" in parsed and not isinstance(parsed["clause_reviews"], list):
            parsed["clause_reviews"] = []
        return parsed
