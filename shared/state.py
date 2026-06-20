"""Tier 0 VentureScout 흐름에서 쓰는 LangGraph 런타임 State."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from shared.contracts import (
    AgentRun,
    AnalysisJob,
    CriticResult,
    Decision,
    DocumentRecord,
    EvidenceItem,
    Hypothesis,
    IPOverlapCandidate,
    IdeaRecord,
)


class VentureScoutState(TypedDict, total=False):
    # 요청으로 들어오는 기본 문맥.
    job_id: str
    idea_id: str
    raw_input: str

    # 데이터가 흐르는 테이블 순서.
    idea: IdeaRecord
    analysis_job: AnalysisJob
    hypotheses: list[Hypothesis]
    documents: dict[str, DocumentRecord]
    evidence_items: Annotated[dict[str, EvidenceItem], operator.or_]
    agent_runs: Annotated[list[AgentRun], operator.add]
    ip_overlap_candidates: Annotated[list[IPOverlapCandidate], operator.add]

    # 최종 의사결정 뷰.
    critic: CriticResult
    decision: Decision
    final_report: dict[str, Any]
    critic_scorecard: dict[str, Any]

    # agents/nodes/* 상세 노드가 graph로 이관되는 동안 유지하는 호환 키.
    market_result: dict[str, Any] | None
    competitor_result: dict[str, Any] | None
    tech_result: dict[str, Any] | None
    ip_result: dict[str, Any] | None
    bm_result: dict[str, Any] | None
    critic_result: dict[str, Any] | None
