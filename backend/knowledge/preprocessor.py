"""FAQ 预处理器：解析 FAQ_四部分版_最终.md 为结构化知识库 JSON。

输入：Markdown 文件，结构为：
    # 法务问答汇编
    ---
    ## 202312
    ### Q1：问题？
    **法律解答：**
    ...
    **合规风险：**
    ...
    **实操建议：**
    ...
    ---
    **相关法条：**
    ...
    ---
    ### Q2：...

输出：list[dict]，每条对应一条 FAQ，字段见 spec 6.1 节。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import List, Dict, Optional

# 四段式字段名 -> Markdown 中段落标题
FIELD_MARKERS = {
    "legal_answer": "**法律解答：**",
    "compliance_risk": "**合规风险：**",
    "practical_advice": "**实操建议：**",
    "legal_basis": "**相关法条：**",
}

# 段落标题的顺序（用于按出现顺序切分）
FIELD_ORDER = ["legal_answer", "compliance_risk",
               "practical_advice", "legal_basis"]


class DataPreprocessor:
    """FAQ Markdown 解析器。

    使用面向对象封装：解析状态封装在实例中，方法职责单一。
    """

    # 月份标识正则：## 202312
    MONTH_RE = re.compile(r"^##\s*(\d{6})\s*$")
    # 问题标识正则：### Q1：问题内容？
    QUESTION_RE = re.compile(r"^###\s*Q(\d+)[：:]\s*(.+?)\s*$")

    def __init__(self, source_file: str = "FAQ_四部分版_最终.md"):
        self.source_file = source_file
        self._items: List[Dict] = []
        self._current_month: Optional[str] = None
        self._current_case_id: Optional[str] = None  # 案例链 ID（暂未启用）

    # ---------- 公开接口 ----------

    def parse(self) -> List[Dict]:
        """解析源文件，返回知识条目列表。"""
        if not os.path.exists(self.source_file):
            raise FileNotFoundError(f"FAQ 源文件不存在：{self.source_file}")

        with open(self.source_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 按行扫描，遇到 ### 开启一条新条目
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            month_match = self.MONTH_RE.match(line.strip())
            if month_match:
                self._current_month = month_match.group(1)
                i += 1
                continue

            q_match = self.QUESTION_RE.match(line.strip())
            if q_match:
                q_num = int(q_match.group(1))
                question = q_match.group(2).strip()
                # 从下一行开始，找到下一个 ### 或文件末尾
                block_lines, consumed = self._collect_block(lines, i + 1)
                item = self._build_item(q_num, question, block_lines)
                self._items.append(item)
                i += consumed + 1
                continue

            i += 1

        return self._items

    def save(self, items: List[Dict], output_path: str) -> None:
        """将解析结果写入 JSON 文件。"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    def parse_and_save(self, output_path: str) -> List[Dict]:
        """解析并保存，返回条目列表。"""
        items = self.parse()
        self.save(items, output_path)
        return items

    # ---------- 内部方法 ----------

    def _collect_block(self, lines: List[str], start: int):
        """收集一个 Q 块的所有行，直到遇到下一个 ### 或文件末尾。

        返回：(块内容行列表, 消耗的行数)
        """
        block = []
        i = start
        while i < len(lines):
            line = lines[i]
            # 遇到下一个问题或月份标识则停止
            if self.QUESTION_RE.match(line.strip()) or \
               self.MONTH_RE.match(line.strip()):
                break
            block.append(line)
            i += 1
        return block, (i - start)

    def _build_item(self, q_num: int, question: str,
                    block_lines: List[str]) -> Dict:
        """根据问题编号、问题文本和块内容构建一条知识条目。"""
        # 切分四段
        fields = self._split_fields(block_lines)

        # 构建 FAQ ID：FAQ-{月份}-{三位序号}
        month = self._current_month or "000000"
        faq_id = f"FAQ-{month}-{q_num:03d}"

        # 提取分类标签（基于关键词，简化版）
        category, tags = self._infer_category(question, fields)

        item = {
            "id": faq_id,
            "case_id": None,
            "month": month,
            "question": question,
            "legal_answer": fields["legal_answer"],
            "compliance_risk": fields["compliance_risk"],
            "practical_advice": fields["practical_advice"],
            "legal_basis": fields["legal_basis"],
            "source": os.path.basename(self.source_file),
            "source_work_order_id": None,
            "category": category,
            "tags": tags,
            "status": "confirmed",
            "created_by": "system",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        return item

    def _split_fields(self, block_lines: List[str]) -> Dict[str, str]:
        """将块内容按四段式标题切分为四个字段。

        策略：扫描每行，匹配字段标题；遇到标题后，后续非分隔行内容
        归入该字段，直到遇到下一个字段标题。
        """
        fields = {name: [] for name in FIELD_ORDER}
        current_field: Optional[str] = None

        for line in block_lines:
            stripped = line.strip()

            # 跳过分隔线
            if stripped == "---":
                continue

            # 匹配字段标题
            matched_field = None
            for fname, marker in FIELD_MARKERS.items():
                if stripped.startswith(marker):
                    matched_field = fname
                    # 标题行可能同行带有内容，提取标题后的部分
                    rest = stripped[len(marker):].strip()
                    if rest:
                        fields[fname].append(rest)
                    break

            if matched_field:
                current_field = matched_field
                continue

            # 内容行归入当前字段
            if current_field and stripped:
                fields[current_field].append(stripped)

        # 拼接为字符串
        return {name: "\n".join(lines) for name, lines in fields.items()}

    def _infer_category(self, question: str, fields: Dict[str, str]) \
            -> tuple:
        """根据问题文本和字段内容推断分类和标签。

        简化版：基于关键词规则匹配。生产版可换为更复杂的分类器。
        """
        text = question + " " + fields.get("legal_answer", "")

        # 分类规则：关键词 -> (大类, 子类)
        category_rules = [
            (("著作权", "版权", "卡通", "表情包"), "知识产权-著作权"),
            (("商标", "品牌"), "知识产权-商标"),
            (("专利", "发明"), "知识产权-专利"),
            (("合同", "相对方", "第三方付款", "印章"),
             "合同法-合同履行"),
            (("担保", "保证", "抵押", "质押"), "担保法"),
            (("诉讼", "仲裁", "证据", "管辖", "起诉"),
             "程序法-诉讼仲裁"),
            (("侵权", "赔偿", "滑倒", "工伤"), "侵权责任法"),
            (("租赁", "房屋", "集装箱"), "物权法-租赁"),
            (("发票", "税务", "税收"), "财税法"),
            (("劳动", "员工", "工伤", "社保"), "劳动法"),
            (("公司", "股东", "出资", "治理"), "公司法"),
            (("数据", "个人信息", "隐私"), "数据合规"),
            (("合规", "内控", "风控"), "合规管理"),
        ]

        category = "其他"
        tags = set()

        for keywords, cat in category_rules:
            if any(kw in text for kw in keywords):
                category = cat
                tags.update(kw for kw in keywords if kw in text)
                break

        # 从问题中抽取可能的关键词作为标签
        # 简化：抽取 2-4 字的中文词
        cn_words = re.findall(r"[\u4e00-\u9fa5]{2,4}", question)
        for w in cn_words[:5]:
            tags.add(w)

        return category, sorted(tags)[:8]


# ---------- 命令行入口 ----------

def main():
    """命令行入口：解析 FAQ 并保存为 JSON。"""
    import argparse

    parser = argparse.ArgumentParser(description="FAQ 预处理器")
    parser.add_argument("--source", default="FAQ_四部分版_最终.md",
                        help="FAQ 源 Markdown 文件路径")
    parser.add_argument("--output", default="data/knowledge_base.json",
                        help="输出 JSON 文件路径")
    args = parser.parse_args()

    preprocessor = DataPreprocessor(source_file=args.source)
    items = preprocessor.parse_and_save(args.output)

    print(f"解析完成：共 {len(items)} 条知识条目")
    print(f"输出位置：{args.output}")

    # 简要统计
    if items:
        months = set(it["month"] for it in items)
        categories = set(it["category"] for it in items)
        print(f"覆盖月份：{sorted(months)}")
        print(f"覆盖分类：{len(categories)} 类")
        # 抽样展示第一条
        first = items[0]
        print(f"\n首条样例 [{first['id']}]：")
        print(f"  问题：{first['question'][:50]}")
        print(f"  分类：{first['category']}")
        print(f"  标签：{first['tags']}")
        print(f"  法条长度：{len(first['legal_basis'])} 字符")


if __name__ == "__main__":
    main()
