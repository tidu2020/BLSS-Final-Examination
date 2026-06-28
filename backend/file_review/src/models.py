from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime
from dataclasses import dataclass, field


class RiskLevel(str, Enum):
    HIGH = "🔴高风险"
    MEDIUM = "🟡中风险"
    LOW = "🟢低风险"
    FATAL  = "🟡需重点谈判"
    ACCEPTABLE = "🟢可接受"


class PartyInfo(BaseModel):
    name: str = ""
    role_label: str = ""


class ClauseSummary(BaseModel):
    title: str = ""
    summary: str = ""
    risk_assessment: str = ""


class ContractInfo(BaseModel):
    contract_name: str = ""
    contract_type: str = ""
    type_keywords: List[str] = Field(default_factory=list)
    party_a: PartyInfo = Field(default_factory=PartyInfo)
    party_b: PartyInfo = Field(default_factory=PartyInfo)
    main_obligation_a: str = ""
    main_obligation_b: str = ""
    subject_matter: str = ""
    price: str = ""
    payment_method: str = ""
    existing_clauses: List[ClauseSummary] = Field(default_factory=list)
    missing_clauses: List[str] = Field(default_factory=list)
    inferred_party: str = ""


class CaseCause(BaseModel):
    level1: str = ""
    level2: str = ""
    level3: str = ""
    level4: Optional[str] = None
    full_path: str = ""


class LegalReference(BaseModel):
    article: str = ""
    content: str = ""
    source_url: str = ""
    verified: bool = False


class ClauseReview(BaseModel):
    clause_title: str = ""
    original_text: str = ""
    risk_level: str = "🟢低风险"
    review_category: str = ""
    legal_basis: List[LegalReference] = Field(default_factory=list)
    problem_analysis: str = ""
    suggested_revision: str = ""
    revision_reason: str = ""
    negotiation_priority: str = "🟢可协商"
    risk_type: str = ""
    actual_impact: str = ""


class StructureOptimization(BaseModel):
    problem: str = ""
    alternatives: List[str] = Field(default_factory=list)
    checklist: List[str] = Field(default_factory=list)


class ActionItems(BaseModel):
    legal_actions: List[str] = Field(default_factory=list)
    business_actions: List[str] = Field(default_factory=list)


class DepartmentReviewResult(BaseModel):
    department: str = ""
    review_time: str = ""
    status: str = "pending"
    clause_reviews: List[ClauseReview] = Field(default_factory=list)
    structure_optimizations: List[StructureOptimization] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)
    supplementary_notes: List[str] = Field(default_factory=list)


@dataclass
class LawVerification:
    article: str = ""
    status: str = ""
    reason: str = ""
    correct_article: str = ""


@dataclass
class BusinessPitfall:
    category: str = ""
    description: str = ""
    remedy: str = ""


@dataclass
class LegalIssue:
    category: str = ""
    description: str = ""
    related_laws: str = ""


@dataclass
class KnowledgeBase:
    business_pitfalls: List[BusinessPitfall] = field(default_factory=list)
    legal_issues: List[LegalIssue] = field(default_factory=list)
    external_refs: List[LegalReference] = field(default_factory=list)


class ReviewReport(BaseModel):
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    search_enabled: bool = False
    contract_type: str = ""
    case_cause: CaseCause = Field(default_factory=CaseCause)
    review_stance: str = ""
    contract_info: ContractInfo = Field(default_factory=ContractInfo)
    legal_review: DepartmentReviewResult = Field(default_factory=lambda: DepartmentReviewResult(department="法务部"))
    business_review: DepartmentReviewResult = Field(default_factory=lambda: DepartmentReviewResult(department="商务部"))
    overall_status: str = "pending"
    action_items: ActionItems = Field(default_factory=ActionItems)
    supplementary_notes: List[str] = Field(default_factory=list)
    search_keywords: List[str] = Field(default_factory=list)
    knowledge_base: Optional[KnowledgeBase] = None
    law_verifications: List[LawVerification] = Field(default_factory=list)