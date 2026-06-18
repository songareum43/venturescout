"""Track C 프롬프트 출력을 위한 Pydantic 스키마.

프롬프트가 직접 맞춰야 하는 형태이며, 안정적인 DB 계약인
`shared.contracts`와 의도적으로 맞춰져 있다. 엄격한 필드는 agent_runs
envelope에 들어가고, 에이전트별 추론 본문은 output_json에 담긴다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from shared.contracts import AgentName, Confidence, Decision, Depth


class HypothesisOut(BaseModel):
    code: str
    axis: str
    statement: str
    confidence: Confidence = "low"
    next_validation: str


class StructuringOutput(BaseModel):
    title: str
    idea_type: str
    target_customer: str
    problem_statement: str
    solution_summary: str
    business_model_hint: str
    technical_elements: list[str]
    patent_keywords: list[str]
    hypotheses: list[HypothesisOut]


class AgentRunOutput(BaseModel):
    """agent_runs envelope 안에 넣을 수 있는 프롬프트 출력."""

    agent_name: AgentName
    hypothesis_id: str | None = None
    depth: Depth
    confidence: Confidence
    grounded_on: list[str] = Field(..., min_length=1)
    summary: str
    key_findings: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    needs_more_research: bool = False
    output_json: dict[str, Any] = Field(default_factory=dict)


class TechOutput(AgentRunOutput):
    feasibility_signal: Confidence
    required_data: list[str] = Field(default_factory=list)
    required_models_or_apis: list[str] = Field(default_factory=list)
    infra_risks: list[str] = Field(default_factory=list)
    cost_risks: list[str] = Field(default_factory=list)


class IPOutput(AgentRunOutput):
    overlap_signal: Confidence
    high_overlap_elements: list[str] = Field(default_factory=list)
    design_around_options: list[str] = Field(default_factory=list)
    legal_guardrail_note: str = (
        "법적 침해 판단이 아니라, evidence에 연결된 IP 리스크 신호입니다."
    )


class CriticOutput(BaseModel):
    agent_name: AgentName = "critic"
    confidence: Confidence
    grounded_on: list[str] = Field(..., min_length=1)
    objections: list[str] = Field(default_factory=list)
    overclaim_points: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    decision: Decision
    decision_reason: str
    next_experiments: list[str] = Field(default_factory=list)
