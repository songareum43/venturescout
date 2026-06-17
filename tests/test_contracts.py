"""계약 스키마 검증 — C 계약(AgentRun/EvidenceItem) 기준."""
import pytest
from pydantic import ValidationError

from shared.contracts import AgentRun, EvidenceItem


def test_agent_run_carries_grounding_and_output():
    r = AgentRun(job_id="job_1", hypothesis_id="H5", agent_name="ip",
                 depth="full", confidence="mid", grounded_on=["ev_0412"],
                 output_json={"signal": "중첩 신호 중간"})
    assert r.grounded_on == ["ev_0412"]
    # signal/next_experiment 등 분석 본문은 loose한 output_json에 담긴다.
    assert r.output_json["signal"] == "중첩 신호 중간"


def test_agent_run_rejects_empty_grounding():
    """grounded_on min_length=1 — 근거 없는 주장 금지(ADR-014)."""
    with pytest.raises(ValidationError):
        AgentRun(job_id="job_1", agent_name="ip", depth="full",
                 confidence="mid", grounded_on=[])


def test_evidence_stance_enum():
    e = EvidenceItem(evidence_id="ev_1", job_id="job_1", hypothesis_id="H1",
                     document_id="d1", source_type="seed_review", evidence_text="...",
                     stance="contradicts", relevance_score=0.5, reliability_score=0.6)
    assert e.stance == "contradicts"
