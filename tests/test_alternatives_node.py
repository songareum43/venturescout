from agents.graph import _kill_reason
from shared.contracts import AgentRun, IPOverlapCandidate


def test_kill_reason_is_ip_conflict_when_ip_and_contradiction_present():
    scorecard = {
        "high_ip_candidates": ["cand_1"],
        "contradicting_evidence": ["ev_1"],
        "low_confidence_agents": [],
    }
    assert _kill_reason(scorecard) == "ip_conflict"


def test_kill_reason_is_weak_evidence_when_only_low_confidence():
    scorecard = {
        "high_ip_candidates": [],
        "contradicting_evidence": [],
        "low_confidence_agents": ["market", "competitor", "tech"],
    }
    assert _kill_reason(scorecard) == "weak_evidence"


def _candidate(candidate_id: str, evidence_id: str, hybrid_score: float = 0.8) -> IPOverlapCandidate:
    return IPOverlapCandidate(
        candidate_id=candidate_id,
        job_id="job_1",
        hypothesis_id="H5",
        limitation_id="lim_1",
        evidence_id=evidence_id,
        plan_technical_element="meeting summarization",
        lexical_score=0.7,
        similarity_score=0.85,
        hybrid_score=hybrid_score,
        rank=1,
    )


def _run(agent_name: str, grounded_on: list[str]) -> AgentRun:
    return AgentRun(
        agent_run_id=f"run_{agent_name}",
        job_id="job_1",
        hypothesis_id="H1",
        agent_name=agent_name,
        grounded_on=grounded_on,
        confidence="low",
        depth="light",
    )


def test_alternatives_evidence_ids_for_ip_conflict_combines_contradiction_and_ip_evidence():
    from agents.graph import _alternatives_evidence_ids

    scorecard = {
        "high_ip_candidates": ["cand_1"],
        "contradicting_evidence": ["ev_contra"],
    }
    candidates = [_candidate("cand_1", "ev_ip")]
    result = _alternatives_evidence_ids("ip_conflict", scorecard, [], candidates)
    assert result == ["ev_contra", "ev_ip"]


def test_alternatives_evidence_ids_for_weak_evidence_collects_low_confidence_grounded_on():
    from agents.graph import _alternatives_evidence_ids

    scorecard = {"low_confidence_agents": ["market", "competitor"]}
    agent_runs = [
        _run("market", ["ev_m1", "ev_m2"]),
        _run("competitor", ["ev_c1"]),
        _run("tech", ["ev_t1"]),  # tech은 low_confidence 아님 -> 제외돼야 함
    ]
    result = _alternatives_evidence_ids("weak_evidence", scorecard, agent_runs, [])
    assert result == ["ev_c1", "ev_m1", "ev_m2"]
