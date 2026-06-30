"""
法律检索模块（独立板块）

基于 backend/law_search 模块，通过国家法律法规数据库 (flk.npc.gov.cn)
进行法条搜索和条文原文获取。

接口兼容原 LegalSearcher，供 ContractReviewPipeline 调用。
"""

from .models import LegalReference
from ..config import SearchConfig
from backend.law_search import LawVerifier, FLKApiClient, LawSearchResult
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class LegalSearcher:
    """
    法律检索器

    基于 FLK API 进行法条搜索，辅助合同审查的法律依据检索。
    """

    def __init__(self, llm_client=None, config: Optional[SearchConfig] = None):
        """
        Args:
            llm_client: 保留参数，用于接口兼容（本模块不再依赖 LLM）
            config: 搜索配置
        """
        self.config = config or SearchConfig()
        self.verifier = LawVerifier()
        self.flk = FLKApiClient()

    def search(
        self,
        contract_type: str,
        case_cause: str,
        review_focus: List[str],
    ) -> Tuple[List[LegalReference], List[str], bool]:
        """
        根据合同类型、案由和审查焦点搜索相关法律依据

        Args:
            contract_type: 合同类型
            case_cause: 匹配案由
            review_focus: 审查焦点列表

        Returns:
            (法律依据列表, 搜索关键词列表, 是否搜索成功)
        """
        if not self.config.enabled:
            return [], [], False

        # 构造搜索关键词
        keywords = self._build_keywords(contract_type, case_cause, review_focus)

        articles = []
        for keyword in keywords[:3]:
            try:
                results = self.verifier.query(keyword, num_results=3)
                for r in results:
                    articles.append(LegalReference(
                        article=f"《{r.law_name}》{r.article_num}",
                        content=r.text,
                        source_url=r.official_url,
                        verified=r.is_valid,
                    ))
            except Exception as e:
                logger.warning(f"法条搜索 '{keyword}' 失败: {str(e)[:200]}")
                continue

        # 如果自然语言查询没结果，尝试直接 API 搜索
        if not articles:
            for keyword in keywords[:3]:
                try:
                    results, _ = self.flk.search_laws(keyword=keyword, page_size=3, only_valid=True)
                    if not results:
                        results, _ = self.flk.search_laws(keyword=keyword, page_size=3)
                    for r in results:
                        articles.append(LegalReference(
                            article=r.title,
                            content=f"来源：{r.source} | 时效性：{r.status_text}",
                            source_url=r.detail_url,
                            verified=r.is_valid,
                        ))
                except Exception as e:
                    logger.warning(f"FLK API 搜索 '{keyword}' 失败: {str(e)[:200]}")
                    continue

        search_success = len(articles) > 0
        if not search_success:
            logger.warning("FLK 搜索未获取到法律依据，请在报告中标注")

        return articles, keywords, search_success

    def _build_keywords(
        self,
        contract_type: str,
        case_cause: str,
        review_focus: List[str],
    ) -> List[str]:
        """构造搜索关键词列表"""
        keywords = []

        # 关键词1：合同类型 + 审查焦点
        if contract_type and review_focus:
            keywords.append(f"{contract_type} {review_focus[0]}")

        # 关键词2：案由
        if case_cause:
            keywords.append(case_cause)

        # 关键词3：审查焦点
        for focus in review_focus[:2]:
            if focus and focus not in " ".join(keywords):
                keywords.append(focus)

        if not keywords:
            keywords = ["合同纠纷 违约责任"]

        return keywords