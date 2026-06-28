"""AI 审核引擎核心算法：双算法自研实现。

包含两个算法类：
1. KeywordMatcher：基于关键词集合交集的粗筛（Top-20 候选）
2. SimilarityRetriever：基于 TF-IDF + 余弦相似度的精排（Top-5）

两者均继承自 DataAnalyzer 抽象基类，体现面向对象设计原则。
算法原理详见 spec 第 5 节。
"""

from __future__ import annotations

import collections
import math
import re
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple


# ---------- 中文分词工具 ----------

# 单字 + 英数 token
TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fa5]")


def tokenize(text: str) -> List[str]:
    """简易分词：英文/数字整体为一个 token，中文按单字切分。

    这是自研 TF-IDF 的基础分词器。生产版可换为 jieba。
    """
    return TOKEN_RE.findall(text)


def tokenize_bigram(text: str) -> List[str]:
    """单字 + 双字组合分词。

    用于关键词匹配：单字过粗，双字可命中"合同""担保"等术语。
    """
    cn = [t for t in tokenize(text) if re.match(r"[\u4e00-\u9fa5]", t)]
    tokens = list(cn)  # 保留单字
    for i in range(len(cn) - 1):
        tokens.append(cn[i] + cn[i + 1])  # 双字
    return tokens


# ---------- 抽象基类 ----------

class DataAnalyzer(ABC):
    """数据分析器抽象基类。

    所有检索算法继承此类，统一接口。体现 OOP 多态。
    """

    @abstractmethod
    def analyze(self, query: str, items: List[Dict],
                top_k: int = 5) -> List[Dict]:
        """对 items 按 query 相关性排序，返回 top_k 条。

        Args:
            query: 用户查询文本
            items: 待分析的知识条目列表
            top_k: 返回前 K 条

        Returns:
            按相关性降序排列的条目列表
        """
        ...


# ---------- 算法一：KeywordMatcher ----------

class KeywordMatcher(DataAnalyzer):
    """关键词粗筛器。

    原理：
    1. 对查询和每条 FAQ 抽取关键词集合
       - 单字（去停用词）
       - 双字组合
       - 法务术语词典命中
    2. 计算查询关键词集合与 FAQ 关键词集合的交集大小
    3. 按交集大小降序，取 Top-N 候选

    作用：快速过滤掉明显无关的条目，缩小精排范围。
    """

    STOP_WORDS = {
        # 常用虚词
        "什么", "是", "的", "在", "和", "与", "及", "了", "吗", "呢",
        "啊", "呀", "吧", "把", "被", "让", "使", "给", "为", "对",
        # 常用实词（过于泛化，无区分度）
        "作用", "区别", "之间", "中", "等", "主要", "通常", "一般",
        "如何", "怎么", "可以", "能够", "应该", "需要", "这个", "那个",
        "哪些", "哪种", "什么", "是不是", "能不能", "有没有",
        # 单字虚词
        "一", "二", "三", "个", "种", "项", "类", "的", "了", "在",
    }

    LEGAL_TERMS = {
        # 民商法
        "合同", "相对方", "担保", "保证", "抵押", "质押", "留置",
        "代理", "表见代理", "无权代理", "印章", "公章", "法定代表人",
        # 程序法
        "诉讼", "仲裁", "证据", "管辖", "起诉", "答辩", "上诉", "再审",
        "举证", "质证", "鉴定", "保全", "执行", "判决", "裁定",
        # 知识产权
        "著作权", "版权", "商标", "专利", "发明", "实用新型", "外观设计",
        "登记", "侵权", "合理使用", "法定许可",
        # 物权
        "租赁", "房屋", "集装箱", "所有权", "用益物权", "相邻关系",
        # 侵权
        "侵权", "赔偿", "损害", "过错", "无过错", "连带责任",
        "滑倒", "工伤", "人身损害",
        # 财税
        "发票", "税务", "税收", "增值税", "所得税",
        # 劳动
        "劳动", "员工", "社保", "工伤", "劳动合同", "解除",
        # 公司法
        "公司", "股东", "出资", "股权", "治理", "决议",
        # 合规
        "合规", "内控", "风控", "数据", "个人信息", "隐私",
        "国企", "国有资产",
    }

    def extract(self, text: str) -> set:
        """从文本中抽取关键词集合。

        Returns:
            关键词集合（单字 + 双字 + 法务术语）
        """
        if not text:
            return set()

        cn = [t for t in tokenize(text) if re.match(r"[\u4e00-\u9fa5]", t)]
        keywords = set()

        # 单字（去停用词）
        for ch in cn:
            if ch not in self.STOP_WORDS:
                keywords.add(ch)

        # 双字组合（去停用词）
        for i in range(len(cn) - 1):
            bi = cn[i] + cn[i + 1]
            if bi not in self.STOP_WORDS:
                keywords.add(bi)

        # 法务术语词典命中
        for term in self.LEGAL_TERMS:
            if term in text:
                keywords.add(term)

        return keywords

    def filter(self, query: str, items: List[Dict],
               top_n: int = 20) -> List[Dict]:
        """粗筛：返回关键词交集得分 Top-N 的条目。

        Args:
            query: 用户查询
            items: 知识条目
            top_n: 返回前 N 条

        Returns:
            候选条目列表（按得分降序）
        """
        q_kws = self.extract(query)
        if not q_kws:
            return []

        scored: List[Tuple[int, Dict]] = []
        for item in items:
            # 用问题 + 法律解答作为抽取源
            item_text = item.get("question", "") + " " + \
                item.get("legal_answer", "")
            item_kws = self.extract(item_text)
            score = len(q_kws & item_kws)
            if score > 0:
                scored.append((score, item))

        # 按得分降序
        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored[:top_n]]

    def analyze(self, query: str, items: List[Dict],
                top_k: int = 5) -> List[Dict]:
        """DataAnalyzer 接口实现：等价于 filter(top_n=top_k)。"""
        return self.filter(query, items, top_n=top_k)


# ---------- 算法二：SimilarityRetriever ----------

class SimilarityRetriever(DataAnalyzer):
    """TF-IDF + 余弦相似度精排器。

    原理：
    1. fit()：对所有 FAQ 构建词汇表 + IDF 表 + 文档向量
    2. rank()：对查询向量化，与候选文档向量计算余弦相似度，
       按相似度降序返回 Top-K

    自行实现 TF-IDF 和余弦相似度，不依赖 sklearn。
    """

    def __init__(self):
        self.vocab: List[str] = []
        self.vocab_index: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.doc_vectors: List[List[float]] = []
        self.items: List[Dict] = []

    # ---------- 训练 ----------

    def fit(self, items: List[Dict]) -> "SimilarityRetriever":
        """构建词汇表和文档向量。

        Args:
            items: 知识条目列表，每条至少含 question 和 legal_answer

        Returns:
            self（链式调用）
        """
        self.items = list(items)
        docs = [self._doc_text(it) for it in self.items]

        # 统计 df（文档频率）
        N = len(docs)
        df = collections.Counter()
        for doc in docs:
            for w in set(self._tokenize(doc)):
                df[w] += 1

        # 构建词汇表
        self.vocab = list(df.keys())
        self.vocab_index = {w: i for i, w in enumerate(self.vocab)}

        # 计算 IDF：log(N / (df + 1))，加 1 平滑防止除零
        self.idf = {
            w: math.log((N + 1) / (df[w] + 1)) + 1.0  # sklearn 风格平滑
            for w in self.vocab
        }

        # 向量化所有文档
        self.doc_vectors = [self._vectorize(doc) for doc in docs]
        return self

    def _doc_text(self, item: Dict) -> str:
        """拼接条目文本作为文档。"""
        return " ".join([
            item.get("question", ""),
            item.get("legal_answer", ""),
            item.get("compliance_risk", ""),
        ])

    def _tokenize(self, text: str) -> List[str]:
        """分词：单字 + 双字。"""
        return tokenize_bigram(text)

    def _vectorize(self, doc: str) -> List[float]:
        """将文档转换为 TF-IDF 向量。

        TF = 词频 / 文档总词数（归一化）
        TF-IDF = TF * IDF
        """
        tokens = self._tokenize(doc)
        total = len(tokens) or 1  # 防止除零
        tf = collections.Counter(tokens)

        vec = [0.0] * len(self.vocab)
        for w, count in tf.items():
            if w in self.vocab_index:
                idx = self.vocab_index[w]
                vec[idx] = (count / total) * self.idf.get(w, 0.0)
        return vec

    # ---------- 检索 ----------

    def rank(self, query: str, candidates: List[Dict],
             top_k: int = 5) -> List[Dict]:
        """对候选集精排，返回 Top-K。

        Args:
            query: 用户查询
            candidates: 粗筛后的候选条目
            top_k: 返回前 K 条

        Returns:
            按相似度降序的条目列表
        """
        if not candidates:
            return []

        q_vec = self._vectorize(query)
        q_norm = self._norm(q_vec)
        if q_norm == 0:
            # 查询向量全零，按候选顺序返回
            return candidates[:top_k]

        scored: List[Tuple[float, Dict]] = []
        for cand in candidates:
            idx = self._find_item_index(cand)
            if idx < 0:
                # 候选不在已训练集合中，实时向量化
                doc_vec = self._vectorize(self._doc_text(cand))
            else:
                doc_vec = self.doc_vectors[idx]

            score = self._cosine(q_vec, doc_vec, q_norm)
            scored.append((score, cand))

        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored[:top_k]]

    def analyze(self, query: str, items: List[Dict],
                top_k: int = 5) -> List[Dict]:
        """DataAnalyzer 接口实现：fit + rank 一站式。"""
        if not self.items or self.items != items:
            self.fit(items)
        return self.rank(query, items, top_k=top_k)

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """已 fit 状态下的快捷检索。"""
        return self.rank(query, self.items, top_k=top_k)

    # ---------- 工具方法 ----------

    def _find_item_index(self, item: Dict) -> int:
        """在 self.items 中查找条目索引（按 id）。"""
        target_id = item.get("id")
        for i, it in enumerate(self.items):
            if it.get("id") == target_id:
                return i
        return -1

    @staticmethod
    def _norm(vec: List[float]) -> float:
        """向量 L2 范数。"""
        return math.sqrt(sum(x * x for x in vec))

    @staticmethod
    def _cosine(a: List[float], b: List[float],
                a_norm: float = None, b_norm: float = None) -> float:
        """计算余弦相似度。

        cos(θ) = (A·B) / (|A|·|B|)
        """
        if a_norm is None:
            a_norm = SimilarityRetriever._norm(a)
        if b_norm is None:
            b_norm = SimilarityRetriever._norm(b)
        if a_norm == 0 or b_norm == 0:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        return dot / (a_norm * b_norm)

    def similarity(self, text_a: str, text_b: str) -> float:
        """计算两段文本的相似度（用于校验器去重判断）。"""
        vec_a = self._vectorize(text_a)
        vec_b = self._vectorize(text_b)
        return self._cosine(vec_a, vec_b)


# ---------- 命令行入口 ----------

def main():
    """命令行入口：演示双算法效果。"""
    import json
    import os

    kb_path = "data/knowledge_base.json"
    if not os.path.exists(kb_path):
        print(f"知识库文件不存在：{kb_path}，请先运行 preprocessor.py")
        return

    with open(kb_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    print(f"加载知识库：{len(items)} 条\n")

    # 演示查询
    queries = [
        "合同相对方要求第三方付款可以吗",
        "著作权登记有什么风险",
        "商场顾客滑倒责任",
        "证据逾期提交的后果",
    ]

    matcher = KeywordMatcher()
    retriever = SimilarityRetriever()
    retriever.fit(items)

    for q in queries:
        print(f"=" * 60)
        print(f"查询：{q}")
        print("-" * 60)

        # 粗筛
        candidates = matcher.filter(q, items, top_n=20)
        print(f"[KeywordMatcher] 粗筛候选：{len(candidates)} 条")

        # 精排
        ranked = retriever.rank(q, candidates, top_k=5)
        print(f"[SimilarityRetriever] 精排 Top-5：")
        for i, it in enumerate(ranked, 1):
            q_text = it["question"][:40]
            print(f"  {i}. [{it['id']}] {q_text}")


if __name__ == "__main__":
    main()
