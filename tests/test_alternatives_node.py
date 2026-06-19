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


def test_route_after_critic_goes_to_alternatives_on_kill():
    from langgraph.graph import END

    from agents.graph import _route_after_critic

    assert _route_after_critic({"decision": "kill"}) == "alternatives"


def test_route_after_critic_goes_to_end_for_other_decisions():
    from langgraph.graph import END

    from agents.graph import _route_after_critic

    assert _route_after_critic({"decision": "go"}) == END
    assert _route_after_critic({"decision": "pivot"}) == END
    assert _route_after_critic({"decision": "more_research"}) == END
    assert _route_after_critic({}) == END


def test_alternatives_node_skips_when_no_matching_evidence():
    from shared.contracts import AnalysisJob

    from agents.graph import alternatives_node

    state = {
        "analysis_job": AnalysisJob(job_id="job_1", idea_id="idea_1"),
        "critic_scorecard": {
            "high_ip_candidates": ["cand_1"],
            "contradicting_evidence": ["ev_missing"],
            "low_confidence_agents": [],
        },
        "agent_runs": [],
        "evidence_items": {},  # ev_missing이 없어 evidence 0건 -> graceful skip
        "ip_overlap_candidates": [],
    }
    result = alternatives_node(state)
    assert result == {"agent_runs": []}


def test_alternatives_node_skips_gracefully_when_llm_call_fails(monkeypatch):
    """alternatives는 보조 제안이라, LLM 호출이 실패해도 이미 완성된 kill 리포트를
    무너뜨리면 안 된다 — 다른 노드처럼 예외를 그대로 던지지 않고 graceful skip해야 한다.
    """
    from agents import graph
    from shared.contracts import AnalysisJob, CriticResult, EvidenceItem

    def boom(**kwargs):
        raise RuntimeError("bedrock timeout")

    monkeypatch.setattr(graph, "_agent_output_with_llm", boom)

    evidence_item = EvidenceItem(
        evidence_id="ev_contra",
        job_id="job_1",
        hypothesis_id="H1",
        document_id="doc_1",
        source_type="seed_review",
        evidence_text="...",
        stance="contradicts",
        relevance_score=0.9,
        reliability_score=0.9,
    )
    state = {
        "analysis_job": AnalysisJob(job_id="job_1", idea_id="idea_1"),
        "critic": CriticResult(decision="kill", confidence="low", summary="..."),
        "critic_scorecard": {
            "high_ip_candidates": ["cand_1"],
            "contradicting_evidence": ["ev_contra"],
            "low_confidence_agents": [],
        },
        "agent_runs": [],
        "evidence_items": {"ev_contra": evidence_item},
        "ip_overlap_candidates": [],
    }
    result = graph.alternatives_node(state)
    assert result == {"agent_runs": []}


def test_build_graph_routes_critic_conditionally_to_alternatives():
    from agents.graph import build_graph

    app = build_graph()
    g = app.get_graph()
    assert "alternatives" in g.nodes

    edge_conditional = {(e.source, e.target): e.conditional for e in g.edges}
    assert edge_conditional[("critic", "alternatives")] is True
    assert edge_conditional[("critic", "__end__")] is True
    assert edge_conditional[("alternatives", "__end__")] is False


def test_alternatives_stage_registered_in_api():
    from app.api import KNOWN_NODES, STAGE_LABELS

    assert "alternatives" in STAGE_LABELS
    assert "alternatives" in KNOWN_NODES


def test_agent_ko_label_for_alternatives():
    from app.ui import AGENT_KO

    assert AGENT_KO["alternatives"] == "대안 제안"


def test_render_board_shows_alternatives_section_on_kill():
    from app.ui import _render_board

    report = {
        "decision": "kill",
        "summary": "근거가 약해 kill",
        "agent_runs": [
            {
                "agent_name": "alternatives",
                "confidence": "low",
                "grounded_on": ["ev_1"],
                "output_json": {
                    "alternatives": [
                        {"title": "B2B 전환", "rationale": "...", "next_experiment": "..."},
                    ]
                },
            },
        ],
    }
    board = _render_board(report)
    assert "🔁 대안 제안" in board
    assert "B2B 전환" in board


def test_render_board_hides_alternatives_section_when_not_kill():
    from app.ui import _render_board

    report = {
        "decision": "go",
        "summary": "근거 충분",
        "agent_runs": [
            {
                "agent_name": "alternatives",
                "confidence": "low",
                "grounded_on": ["ev_1"],
                "output_json": {"alternatives": [{"title": "X"}]},
            },
        ],
    }
    board = _render_board(report)
    assert "🔁 대안 제안" not in board
