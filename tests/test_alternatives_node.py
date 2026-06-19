from agents.graph import _kill_reason


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
