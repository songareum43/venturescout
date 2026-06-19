from pydantic import ValidationError

from shared.contracts import AgentRun, EvidenceItem, IPOverlapCandidate
from shared.state import VentureScoutState


def test_agent_run_requires_grounding():
    run = AgentRun(
        agent_run_id="run_1",
        job_id="job_1",
        hypothesis_id="H5",
        agent_name="ip",
        grounded_on=["ev_0412"],
        confidence="mid",
        depth="full",
    )
    assert run.grounded_on == ["ev_0412"]
    assert run.output_json == {}


def test_agent_run_rejects_empty_grounding():
    try:
        AgentRun(
            job_id="job_1",
            hypothesis_id="H5",
            agent_name="ip",
            grounded_on=[],
            confidence="mid",
            depth="full",
        )
    except ValidationError as exc:
        assert "grounded_on" in str(exc)
    else:
        raise AssertionError("AgentRun accepted empty grounded_on")


def test_evidence_stance_enum():
    evidence = EvidenceItem(
        evidence_id="ev_1",
        job_id="job_1",
        hypothesis_id="H1",
        document_id="d1",
        source_type="seed_review",
        evidence_text="...",
        stance="contradicts",
        relevance_score=0.7,
        reliability_score=0.6,
    )
    assert evidence.stance == "contradicts"


def test_ip_overlap_candidate_matches_signature_table():
    candidate = IPOverlapCandidate(
        candidate_id="cand_1",
        job_id="job_1",
        hypothesis_id="H5",
        limitation_id="lim_1",
        evidence_id="ev_1",
        plan_technical_element="meeting summarization",
        lexical_score=0.72,
        similarity_score=0.84,
        hybrid_score=0.8,
        rank=1,
    )
    assert candidate.hybrid_score == 0.8


def test_agent_run_accepts_alternatives_agent_name():
    run = AgentRun(
        agent_run_id="run_1",
        job_id="job_1",
        hypothesis_id="all",
        agent_name="alternatives",
        grounded_on=["ev_1"],
        confidence="low",
        depth="light",
    )
    assert run.agent_name == "alternatives"


def test_venture_scout_state_has_critic_scorecard_field():
    assert "critic_scorecard" in VentureScoutState.__annotations__
