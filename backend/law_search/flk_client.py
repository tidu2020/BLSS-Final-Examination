"""
国家法律法规数据库 API 客户端

基于 flk.npc.gov.cn 的真实 API 接口：
- POST /law-search/search/list         搜索法律列表
- GET  /law-search/search/flfgDetails  获取法律详情（含目录树）
- GET  /law-search/prompts/search      搜索建议
- GET  /law-search/search/enumData     获取分类枚举
- GET  /law-search/index/aggregateData 获取统计数据
"""

import re
import json
import requests
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class LawSearchResult:
    """法律搜索结果"""
    title: str              # 法律标题
    bbbs: str               # 法律唯一 ID（用于获取详情）
    gbrq: str               # 公布日期
    sxrq: str               # 施行日期
    sxx: int                # 时效性：1=已废止, 2=已修改, 3=有效, 4=尚未生效
    flxz: str               # 法律性质：法律、行政法规、地方性法规等
    zdjg_name: str          # 制定机关
    source: str = "国家法律法规数据库"  # 数据来源
    detail_url: str = ""    # 详情页链接

    @property
    def is_valid(self) -> bool:
        """是否现行有效"""
        return self.sxx == 3

    @property
    def status_text(self) -> str:
        status_map = {1: "已废止", 2: "已修改", 3: "有效", 4: "尚未生效"}
        return status_map.get(self.sxx, "未知")

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "bbbs": self.bbbs,
            "gbrq": self.gbrq,
            "sxrq": self.sxrq,
            "sxx": self.sxx,
            "status": self.status_text,
            "is_valid": self.is_valid,
            "flxz": self.flxz,
            "zdjg_name": self.zdjg_name,
            "source": self.source,
            "detail_url": self.detail_url,
        }


@dataclass
class LawTreeNode:
    """法律目录树节点"""
    id: str
    title: str
    index: int
    children: List['LawTreeNode'] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "children": [c.to_dict() for c in self.children]
        }


class FLKApiClient:
    """
    国家法律法规数据库 API 客户端
    基于 flk.npc.gov.cn 的真实 API 接口
    """

    BASE_URL = "https://flk.npc.gov.cn"

    def __init__(self, timeout: int = 15):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': 'https://flk.npc.gov.cn/',
            'Content-Type': 'application/json;charset=utf-8'
        })
        self.timeout = timeout

    def _clean_html(self, text: str) -> str:
        """去除 HTML 标签"""
        return re.sub(r'<[^>]+>', '', text)

    # ---------- 搜索建议 ----------

    def search_suggest(self, title: str) -> List[str]:
        """
        搜索建议（输入提示）
        GET /law-search/prompts/search?title=xxx
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/law-search/prompts/search",
                params={"title": title},
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") == 200:
                return [
                    self._clean_html(item["title"])
                    for item in data.get("data", [])
                ]
            return []
        except Exception as e:
            print(f"搜索建议请求失败: {e}")
            return []

    # ---------- 搜索法律法规 ----------

    def search_laws(
        self,
        keyword: str,
        search_type: int = 2,
        page: int = 1,
        page_size: int = 10,
        only_valid: bool = False,
        flfg_code_id: Optional[List] = None,
        zdjg_code_id: Optional[List] = None,
        gbrq_year: Optional[List] = None,
    ) -> Tuple[List[LawSearchResult], int]:
        """
        搜索法律法规
        POST /law-search/search/list

        Args:
            keyword: 搜索关键词
            search_type: 搜索类型 1=精确 2=模糊（默认）
            page: 页码
            page_size: 每页数量
            only_valid: 是否只返回现行有效的法律
            flfg_code_id: 法律分类 ID 列表
            zdjg_code_id: 制定机关 ID 列表
            gbrq_year: 公布年份筛选

        Returns:
            (搜索结果列表, 总数)
        """
        try:
            sxx = [3] if only_valid else []
            payload = {
                "searchRange": 1,
                "sxrq": [],
                "gbrq": [],
                "searchType": search_type,
                "sxx": sxx,
                "gbrqYear": gbrq_year or [],
                "flfgCodeId": flfg_code_id or [],
                "zdjgCodeId": zdjg_code_id or [],
                "searchContent": keyword,
                "page": page,
                "pageSize": page_size
            }

            resp = self.session.post(
                f"{self.BASE_URL}/law-search/search/list",
                json=payload,
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 200 and "rows" not in data:
                return [], 0

            results = []
            for row in data.get("rows", []):
                results.append(LawSearchResult(
                    title=self._clean_html(row.get("title", "")),
                    bbbs=row.get("bbbs", ""),
                    gbrq=row.get("gbrq", ""),
                    sxrq=row.get("sxrq", ""),
                    sxx=row.get("sxx", 0),
                    flxz=row.get("flxz", ""),
                    zdjg_name=row.get("zdjgName", ""),
                    detail_url=f"{self.BASE_URL}/detail2?id={row.get('bbbs', '')}"
                ))

            total = data.get("total", 0)
            return results, total

        except Exception as e:
            print(f"搜索法律法规请求失败: {e}")
            return [], 0

    # ---------- 法律详情与目录树 ----------

    def get_law_detail(self, bbbs: str) -> Optional[Dict]:
        """
        获取法律详情（含目录树结构）
        GET /law-search/search/flfgDetails?bbbs=xxx
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/law-search/search/flfgDetails",
                params={"bbbs": bbbs},
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") == 200:
                return data.get("data")
            return None

        except Exception as e:
            print(f"获取法律详情失败: {e}")
            return None

    def get_law_toc(self, bbbs: str) -> Optional[Dict]:
        """
        获取法律目录树
        从 flfgDetails 接口的 content 字段解析
        """
        detail = self.get_law_detail(bbbs)
        if not detail or not detail.get("content"):
            return None
        return detail.get("content")

    def find_article_node(self, bbbs: str, article_num: str) -> Optional[Dict]:
        """
        在目录树中查找指定条文节点

        Args:
            bbbs: 法律 ID
            article_num: 条文序号，如 "第一百四十三条" 或 "第143条"

        Returns:
            匹配的目录树节点，包含 id 和 title
        """
        toc = self.get_law_toc(bbbs)
        if not toc:
            return None

        # 标准化条文序号
        normalized = article_num.strip()
        if not normalized.startswith("第"):
            normalized = f"第{normalized}"
        if not normalized.endswith("条"):
            normalized = f"{normalized}条"

        def search_node(node: Dict) -> Optional[Dict]:
            title = node.get("title", "")
            if re.match(rf"^{re.escape(normalized)}(\s|$)", title):
                return node
            for child in node.get("children", []):
                result = search_node(child)
                if result:
                    return result
            return None

        return search_node(toc)

    # ---------- 枚举与统计 ----------

    def get_enum_data(self) -> Optional[Dict]:
        """
        获取法律分类枚举数据
        GET /law-search/search/enumData
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/law-search/search/enumData",
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") == 200:
                return data.get("data")
            return None

        except Exception as e:
            print(f"获取枚举数据失败: {e}")
            return None

    def get_aggregate_data(self) -> Optional[Dict]:
        """
        获取统计数据
        GET /law-search/index/aggregateData
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/law-search/index/aggregateData",
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") == 200:
                return data.get("data")
            return None

        except Exception as e:
            print(f"获取统计数据失败: {e}")
            return None