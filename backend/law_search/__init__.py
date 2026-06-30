"""
法律条文搜索与验证模块（独立板块）

基于国家法律法规数据库 (flk.npc.gov.cn) 的真实 API，
支持法条搜索、时效性验证、条文原文获取。

来源：https://github.com/tidu2020/law-search
"""

from backend.law_search.flk_client import FLKApiClient, LawSearchResult, LawTreeNode
from backend.law_search.verifier import (
    LawVerifier,
    LawInfo,
    ArticleResult,
    search_laws,
    verify_article,
)

__all__ = [
    "FLKApiClient",
    "LawSearchResult",
    "LawTreeNode",
    "LawVerifier",
    "LawInfo",
    "ArticleResult",
    "search_laws",
    "verify_article",
]