from .llm_client import LLMClient
from .models import ContractInfo, PartyInfo, ClauseSummary
from ..prompts.extraction import EXTRACTION_SYSTEM_PROMPT, EXTRACTION_USER_PROMPT
import json
import os


def _load_clause_checklist(data_dir: str = "data") -> dict:
    path = os.path.join(data_dir, "clause_checklist.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.pop("_meta", None)
            return data
    return {}


class ContractExtractor:
    CLAUSE_CHECKLIST = _load_clause_checklist()

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def extract(self, contract_text: str) -> ContractInfo:
        checklist_text = ""
        for ctype, cdata in self.CLAUSE_CHECKLIST.items():
            required = cdata.get("应备条款", [])
            common_missing = cdata.get("常见缺失", [])
            if required or common_missing:
                checklist_text += f"\n- {ctype}: 应备条款={required}, 常见缺失={common_missing}"

        enhanced_prompt = EXTRACTION_SYSTEM_PROMPT
        if checklist_text:
            enhanced_prompt += f"\n\n【条款完整性校验参考 - 各类合同应备条款清单】{checklist_text}"

        user_prompt = EXTRACTION_USER_PROMPT.format(contract_text=contract_text)
        result = self.llm.chat_json(
            system_prompt=enhanced_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
        )

        existing_clauses = []
        for clause_data in result.get("existing_clauses", []):
            if isinstance(clause_data, dict):
                existing_clauses.append(ClauseSummary(
                    title=clause_data.get("title", ""),
                    summary=clause_data.get("summary", ""),
                    risk_assessment=clause_data.get("risk_assessment", ""),
                ))
            else:
                existing_clauses.append(ClauseSummary(
                    title=str(clause_data),
                    summary="",
                    risk_assessment="",
                ))

        return ContractInfo(
            contract_name=result.get("contract_name", ""),
            contract_type=result.get("contract_type", ""),
            type_keywords=result.get("type_keywords", []),
            party_a=PartyInfo(**result.get("party_a", {})),
            party_b=PartyInfo(**result.get("party_b", {})),
            main_obligation_a=result.get("main_obligation_a", ""),
            main_obligation_b=result.get("main_obligation_b", ""),
            subject_matter=result.get("subject_matter", ""),
            price=result.get("price", ""),
            payment_method=result.get("payment_method", ""),
            existing_clauses=existing_clauses,
            missing_clauses=result.get("missing_clauses", []),
            inferred_party=result.get("inferred_party", ""),
        )