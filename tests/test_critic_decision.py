from agents.graph import _decide


def test_coverage_gap_is_more_research():
    decision, _, _ = _decide(
        missing_evidence=["market has no grounded_on evidence"],
        invalid_grounding=[],
        uncovered_hypotheses=[],
        low_confidence=[],
        high_ip_candidates=[],
        contradicting_evidence=[],
    )
    assert decision == "more_research"


def test_fatal_combo_is_kill():
    decision, _, _ = _decide(
        missing_evidence=[],
        invalid_grounding=[],
        uncovered_hypotheses=[],
        low_confidence=[],
        high_ip_candidates=["cand_1"],
        contradicting_evidence=["ev_1"],
    )
    assert decision == "kill"


def test_weak_evidence_is_kill():
    decision, _, _ = _decide(
        missing_evidence=[],
        invalid_grounding=[],
        uncovered_hypotheses=[],
        low_confidence=["market", "competitor", "tech"],
        high_ip_candidates=[],
        contradicting_evidence=[],
    )
    assert decision == "kill"


def test_ip_risk_alone_is_pivot():
    decision, _, _ = _decide(
        missing_evidence=[],
        invalid_grounding=[],
        uncovered_hypotheses=[],
        low_confidence=[],
        high_ip_candidates=["cand_1"],
        contradicting_evidence=[],
    )
    assert decision == "pivot"


def test_clean_evidence_is_go():
    decision, _, _ = _decide(
        missing_evidence=[],
        invalid_grounding=[],
        uncovered_hypotheses=[],
        low_confidence=[],
        high_ip_candidates=[],
        contradicting_evidence=[],
    )
    assert decision == "go"


def test_contradicting_evidence_without_fatal_combo_is_pivot():
    decision, _, _ = _decide(
        missing_evidence=[],
        invalid_grounding=[],
        uncovered_hypotheses=[],
        low_confidence=["market"],
        high_ip_candidates=[],
        contradicting_evidence=["ev_1"],
    )
    assert decision == "pivot"
