from .llm_client import LLMClient
from .models import CaseCause
import json
import os

CASE_MATCH_SYSTEM_PROMPT = """你是一位中国民事案由匹配专家。请根据合同类型，将其映射到《民事案件案由规定》（2025年版，法〔2025〕227号，2026年1月1日施行）的案由树。

2025年版案由体系为十二大部分、四级结构：
- 第一部分 人格权纠纷
- 第二部分 婚姻家庭、继承纠纷
- 第三部分 物权纠纷
- 第四部分 合同、准合同纠纷（十、合同纠纷 / 十一、不当得利纠纷 / 十二、无因管理纠纷）
- 第五部分 知识产权与竞争纠纷
- 第六部分 数据、网络虚拟财产纠纷
- 第七部分 劳动争议、人事争议、新就业形态用工纠纷
- 第八部分 海事海商纠纷
- 第九部分 与公司、证券、保险、票据等有关的民事纠纷
- 第十部分 侵权责任纠纷
- 第十一部分 非讼程序案件案由
- 第十二部分 特殊诉讼程序案件案由

合同纠纷位于第四部分"十、合同纠纷"，包含案由编号78~147，共70个三级案由。

输出 JSON 格式：
{{
  "level1": "一级案由（如：第四部分 合同、准合同纠纷）",
  "level2": "二级案由（如：十、合同纠纷）",
  "level3": "三级案由（如：88.买卖合同纠纷）",
  "level4": "四级案由（如：信息网络买卖合同纠纷，如无则填null）",
  "full_path": "完整路径",
  "alternative_causes": ["备选案由1", "备选案由2"],
  "match_confidence": "高/中/低"
}}

【必须输出纯JSON，以{{开头、}}结尾，不要用```json```包裹】

若为无名合同或混合合同，找出最接近的1-2个典型合同案由作为比照基准。"""


class CaseCauseMatcher:
    def __init__(self, llm_client: LLMClient, data_dir: str = "data"):
        self.llm = llm_client
        self.data_dir = data_dir
        self.case_causes_db = self._load_case_causes()

    def _load_case_causes(self) -> dict:
        path = os.path.join(self.data_dir, "case_causes.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _get_relevant_context(self, contract_type: str) -> str:
        relevant_parts = {}
        for part_name, part_data in self.case_causes_db.items():
            if part_name.startswith("_"):
                continue
            if "合同" in part_name or "第四部分" in part_name or "知识产权" in part_name:
                relevant_parts[part_name] = part_data
        if not relevant_parts:
            return json.dumps(self.case_causes_db, ensure_ascii=False, indent=2)
        return json.dumps(relevant_parts, ensure_ascii=False, indent=2)

    def match(self, contract_type: str, type_keywords: list) -> CaseCause:
        db_context = self._get_relevant_context(contract_type)
        keyword_str = ", ".join(type_keywords) if type_keywords else "无"
        user_prompt = f"""合同类型：{contract_type}
关键词：{keyword_str}

已知案由数据库（2025年版，法〔2025〕227号，2026.01.01施行）：
{db_context}

请匹配最合适的三级案由，输出完整四级路径。"""

        result = self.llm.chat_json(
            system_prompt=CASE_MATCH_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
        )
        level4 = result.get("level4")
        if level4 == "null" or level4 is None:
            level4 = None
        return CaseCause(
            level1=result.get("level1", ""),
            level2=result.get("level2", ""),
            level3=result.get("level3", ""),
            level4=level4,
            full_path=result.get("full_path", ""),
        )