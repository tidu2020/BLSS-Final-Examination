from .llm_client import LLMClient
from .models import LegalReference
from ..config import SearchConfig
from typing import List, Optional, Tuple
import json
import logging

logger = logging.getLogger(__name__)


LEGAL_SEARCH_SYSTEM_PROMPT = """你是一位中国法律检索专家。请根据审查焦点构造检索策略并检索相关法条。

输出 JSON 格式：
{
  "search_queries": [
    {"query": "检索词", "target": "检索目标说明"}
  ],
  "retrieved_articles": [
    {
      "article": "《民法典》第XXX条",
      "content": "法条原文内容",
      "source_url": "https://flk.npc.gov.cn/...",
      "verified": true
    }
  ],
  "search_status": "检索成功/部分成功/未检索到",
  "notes": "检索说明"
}

注意：
1. 优先使用 flk.npc.gov.cn 来源的法条。
2. 若无法获取最新法条，标注 verified: false。
3. 生成类案检索关键词建议。"""


class LegalSearcher:
    def __init__(self, llm_client: LLMClient, config: Optional[SearchConfig] = None):
        self.llm = llm_client
        self.config = config or SearchConfig()

    def search(
        self,
        contract_type: str,
        case_cause: str,
        review_focus: List[str],
    ) -> Tuple[List[LegalReference], List[str], bool]:
        if not self.config.enabled:
            return [], [], False

        focus_text = "、".join(review_focus) if review_focus else "违约责任、违约金、合同解除"

        user_prompt = f"""合同类型：{contract_type}
匹配案由：{case_cause}
审查焦点：{focus_text}
限定检索域名：{self.config.primary_domain}

请生成检索策略和关键词。"""

        result = self.llm.chat_json(
            system_prompt=LEGAL_SEARCH_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
        )

        search_keywords = [q.get("query", "") for q in result.get("search_queries", [])]
        if not search_keywords:
            search_keywords = [focus_text]

        search_api = self.config.search_api.lower()
        if search_api == "duckduckgo":
            return self._ddg_web_search(search_keywords, focus_text)
        else:
            articles = [
                LegalReference(
                    article=a.get("article", ""),
                    content=a.get("content", ""),
                    source_url=a.get("source_url", ""),
                    verified=a.get("verified", False),
                )
                for a in result.get("retrieved_articles", [])
            ]
            search_status = result.get("search_status", "")
            search_success = search_status in ("检索成功", "部分成功")
            return articles, search_keywords, search_success

    def _ddg_web_search(self, queries: List[str], context: str) -> Tuple[List[LegalReference], List[str], bool]:
        articles = []
        seen_urls = set()

        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("duckduckgo-search 未安装，降级为 LLM 内置知识")
            return [], queries, False

        for query in queries[:5]:
            if not query.strip():
                continue

            search_query = f"{query} 法律法规 site:flk.npc.gov.cn"

            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(search_query, max_results=3, region="cn-zh"))
                for r in results:
                    result_url = r.get("href", "")
                    title = r.get("title", "")
                    body = r.get("body", "")[:500]

                    if result_url and title and result_url not in seen_urls:
                        seen_urls.add(result_url)
                        articles.append(LegalReference(
                            article=title,
                            content=body,
                            source_url=result_url,
                            verified="flk.npc.gov.cn" in result_url,
                        ))
            except Exception as e:
                logger.warning(f"DuckDuckGo搜索 '{query}' 失败: {str(e)[:200]}")
                continue

        search_success = len(articles) > 0
        if not search_success:
            logger.warning("DuckDuckGo搜索未获取到法律依据，请在报告中标注")
        return articles, queries, search_success