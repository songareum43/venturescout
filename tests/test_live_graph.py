import pytest

from agents import graph
from agents.input_validation import InsufficientInputError
from shared.contracts import EvidenceItem, IPOverlapCandidate


IDEA = {
    "title": "현장 설비 유지보수 지원 서비스",
    "idea_type": "B2B SaaS",
    "target_customer": "중소 제조업체의 설비 유지보수 팀",
    "problem_statement": "고장 이력과 정비 지식이 분산되어 복구가 늦다.",
    "solution_summary": "정비 기록을 검색하고 점검 절차를 추천한다.",
    "business_model_hint": "사업장 단위 월 구독",
    "technical_elements": ["정비 기록 검색", "고장 원인 추천"],
    "patent_keywords": ["maintenance record retrieval", "fault diagnosis"],
}

HYPOTHESES = [
    {
        "hypothesis_id": f"H{i}",
        "code": f"H{i}",
        "axis": axis,
        "statement": statement,
        "confidence": "low",
        "next_validation": "실제 데이터로 검증",
    }
    for i, (axis, statement) in enumerate(
        [
            ("customer_problem", "정비 지식 분산으로 복구가 지연된다."),
            ("competition", "기존 설비 관리 도구와 차별화할 수 있다."),
            ("business_model", "사업장 단위 구독 의사가 있다."),
            ("technology", "정비 기록 검색과 추천을 구현할 수 있다."),
            ("ip", "핵심 기술이 기존 청구항과 과도하게 중첩되지 않는다."),
        ],
        start=1,
    )
]


@pytest.fixture
def isolated_live_graph(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("RETRIEVAL", "live")

    monkeypatch.setattr(
        graph,
        "_structured_idea_payload",
        lambda **kwargs: (
            {
                **IDEA,
                "idea_id": kwargs["idea_id"],
                "raw_input": kwargs["raw_input"],
            },
            HYPOTHESES,
        ),
    )

    def fake_retrieve(hypothesis_id, query, *, job_id="", k=5, source_types=None):
        return [
            EvidenceItem(
                evidence_id=f"ev-{hypothesis_id}",
                job_id=job_id,
                hypothesis_id=hypothesis_id,
                document_id=f"doc-{hypothesis_id}",
                source_type="patent" if hypothesis_id == "H5" else "web",
                evidence_text=f"{query} 관련 실제 검색 결과",
                stance="neutral",
                relevance_score=0.8,
                reliability_score=0.8,
            )
        ]

    monkeypatch.setattr(graph, "retrieve", fake_retrieve)
    monkeypatch.setattr(
        graph,
        "vector_search",
        lambda technical_elements, *, job_id="", hypothesis_id="", k=10: [
            IPOverlapCandidate(
                candidate_id="candidate-1",
                job_id=job_id,
                hypothesis_id=hypothesis_id,
                limitation_id="limitation-1",
                evidence_id="ev-H5",
                plan_technical_element=technical_elements[0],
                lexical_score=0.7,
                similarity_score=0.8,
                hybrid_score=0.76,
                rank=1,
            )
        ],
    )

    def fake_agent_output(**kwargs):
        return {
            "summary": "검색 근거에 기반한 분석",
            "signal": "추가 검증 필요",
            "key_findings": [],
            "risks": [],
            "recommendations": [],
            "next_experiment": "현장 검증",
            "feasibility_signal": "mid",
            "architecture_assumption": [],
            "required_models_or_apis": [],
            "risk_register": [],
            "validation_plan": [],
            "go_no_go_metrics": {},
            "overlap_signal": "low",
            "high_overlap_elements": [],
            "design_around_options": [],
            "claim_review_queue": [],
            "legal_guardrail_note": "법률 판단이 아님",
            "manual_review_questions": [],
            "revenue_model": "사업장 구독",
            "pricing_hypothesis": "검증 필요",
            "market_size_signal": "검증 필요",
            "unit_economics": "검증 필요",
            "key_risk": "도입 의사",
            "objections": [],
            "missing_evidence": [],
            "next_experiments": ["현장 인터뷰"],
            "llm_succeeded": True,
        }

    monkeypatch.setattr(graph, "_agent_output_with_llm", fake_agent_output)


def test_live_graph_uses_submitted_input(isolated_live_graph):
    result = graph.build_graph().invoke(
        {
            "job_id": "job-test",
            "idea_id": "idea-test",
            "raw_input": (
                "중소 제조업체 유지보수 팀을 위해 분산된 정비 기록을 검색하고 "
                "고장 원인과 점검 절차를 추천하는 사업장 단위 구독 서비스를 만든다."
            ),
        }
    )

    assert result["idea"].title == IDEA["title"]
    assert result["documents"] == {}
    assert len(result["evidence_items"]) == 5
    assert len(result["agent_runs"]) == 6


def test_short_input_stops_before_analysis():
    with pytest.raises(InsufficientInputError):
        graph.build_graph().invoke(
            {
                "job_id": "job-test",
                "idea_id": "idea-test",
                "raw_input": "아무거나",
            }
        )
