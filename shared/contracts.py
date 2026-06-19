"""VentureScout Tier 0 공통 스키마 계약.

DB는 9개 Tier 0 테이블을 기준으로 한다:
ideas, analysis_jobs, hypotheses, documents, evidence_items, agent_runs,
patent_claims, claim_limitations, ip_overlap_candidates.

이 Pydantic 모델들은 ORM이 아니라 Track C 에이전트와 mock 검색 도구가
주고받는 엄격한 메시지 계약이다. 의도적으로 느슨하게 둔 영역은
`output_json`이며, 에이전트 프롬프트가 바뀌어도 DB 마이그레이션 없이
분석 본문을 담기 위한 칸이다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Confidence = Literal["high", "mid", "low"]
Decision = Literal["go", "pivot", "kill", "more_research"]
Depth = Literal["full", "light"]
JobStatus = Literal["pending", "running", "done", "failed"]
Stance = Literal["supports", "contradicts", "neutral"]

AgentName = Literal[
    "structuring",
    "market",
    "competitor",
    "tech",
    "ip",
    "bm",
    "critic",
    "alternatives",
]


class IdeaRecord(BaseModel):
    """테이블: ideas. Structuring이 raw input을 이 형태로 구조화한다."""

    idea_id: str
    raw_input: str
    title: str | None = None
    idea_type: str | None = None
    target_customer: str | None = None
    problem_statement: str | None = None
    solution_summary: str | None = None
    business_model_hint: str | None = None
    technical_elements: list[str] = Field(default_factory=list)
    patent_keywords: list[str] = Field(default_factory=list)
    user_confirmed: bool = False
    created_at: str | None = None


class AnalysisJob(BaseModel):
    """테이블: analysis_jobs. 하나의 분석 job이 하위 행들을 묶는다."""

    job_id: str
    idea_id: str
    status: JobStatus = "pending"
    current_stage: str | None = None
    progress_pct: int = Field(default=0, ge=0, le=100)
    decision: Decision | None = None
    decision_summary: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class Hypothesis(BaseModel):
    """테이블: hypotheses. 근거와 agent run을 묶는 세로축이다."""

    hypothesis_id: str
    job_id: str
    idea_id: str
    code: str
    axis: str
    statement: str
    confidence: Confidence
    next_validation: str


class DocumentRecord(BaseModel):
    """테이블: documents. 특허, seed, web 출처를 통합 저장한다."""

    document_id: str
    source_type: str
    ext_id: str | None = None
    title: str | None = None
    canonical_url: str | None = None
    clean_text: str = ""
    embedding: list[float] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    reliability_score: float | None = None
    freshness_score: float | None = None
    is_user_provided: bool = False


class EvidenceItem(BaseModel):
    """테이블: evidence_items. 모든 에이전트 주장은 이 ID를 인용해야 한다."""

    evidence_id: str
    job_id: str
    hypothesis_id: str
    document_id: str
    source_type: str
    evidence_text: str
    stance: Stance
    relevance_score: float
    reliability_score: float


class AgentRun(BaseModel):
    """테이블: agent_runs. 모든 에이전트 실행의 공통 envelope."""

    agent_run_id: str | None = None
    job_id: str
    hypothesis_id: str | None = None
    agent_name: AgentName
    model_name: str | None = None
    depth: Depth
    confidence: Confidence
    grounded_on: list[str] = Field(..., min_length=1)
    output_json: dict[str, Any] = Field(default_factory=dict)
    target_run_id: str | None = None
    groundedness_score: float | None = None
    overclaim_flag: bool = False
    status: str = "done"


class PatentClaim(BaseModel):
    """테이블: patent_claims. 사람이 읽는 특허 청구항 단위."""

    claim_id: str
    document_id: str
    claim_no: int
    claim_text: str
    is_independent: bool
    parent_claim_no: int | None = None


class ClaimLimitation(BaseModel):
    """테이블: claim_limitations. IP 시그니처 흐름의 검색 단위."""

    limitation_id: str
    claim_id: str
    limitation_order: int
    normalized_text: str
    embedding: list[float] | None = None


class IPOverlapCandidate(BaseModel):
    """테이블: ip_overlap_candidates. 기계가 만든 후보이며 판단은 아니다."""

    candidate_id: str
    job_id: str
    hypothesis_id: str
    limitation_id: str
    evidence_id: str
    plan_technical_element: str
    lexical_score: float
    similarity_score: float
    hybrid_score: float
    rank: int


class CriticResult(BaseModel):
    """Critic 최종 판단을 다루기 위한 편의 뷰."""

    decision: Decision
    confidence: Confidence
    summary: str
    grounded_on: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    next_experiments: list[str] = Field(default_factory=list)


# 예전 문서/도구가 OverlapCandidate를 참조해도 깨지지 않게 둔 호환 별칭.
OverlapCandidate = IPOverlapCandidate
