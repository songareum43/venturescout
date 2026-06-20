# Kill 판정 시 대안 제안 에이전트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `decision == "kill"`일 때만 그래프 안에서 자동으로 이어지는 `alternatives_node`를 추가해, kill 원인(IP 충돌형 vs 근거 약함형)에 따라 다른 전략으로 포지셔닝/타겟/가격정책 조정 대안 2~3개를 제안하고, API/UI까지 노출한다.

**Architecture:** `critic_node`가 이미 계산하는 `scorecard`를 state로 흘려보내고(`critic_scorecard`), `critic` 다음에 조건부 엣지(`add_conditional_edges`)로 `alternatives_node`를 연결한다. `alternatives_node`는 kill 원인을 순수 함수로 판별하고, 인용 가능한 evidence_id를 코드가 직접 골라 LLM에 넘긴 뒤 기존 `_agent_run()`/`_agent_output_with_llm()` 패턴 그대로 `AgentRun`을 만든다. API(`STAGE_LABELS`)와 UI(`AGENT_KO`, `_render_board`)에 한 줄씩 추가해 노출한다.

**Tech Stack:** Python, LangGraph(`StateGraph`/`add_conditional_edges`), Pydantic(`shared.contracts`), pytest

**스펙 문서:** [docs/superpowers/specs/2026-06-19-kill-alternatives-agent-design.md](../specs/2026-06-19-kill-alternatives-agent-design.md)

**테스트 실행 명령:** 프로젝트 루트에서 `python -m pytest tests/<file> -v` (글로벌 Python 3.12 + pytest 9.0.3 사용 — `.venv`에는 pytest가 설치돼 있지 않으므로 venv를 활성화하지 말 것)

---

### Task 1: 계약/상태 플러밍 — `AgentName`에 `"alternatives"` 추가, `VentureScoutState`에 `critic_scorecard` 추가

**Files:**
- Modify: `shared/contracts.py:26-34`
- Modify: `shared/state.py:36-39`
- Test: `tests/test_contracts.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_contracts.py` 끝에 추가:

```python
from shared.state import VentureScoutState


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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_contracts.py -v`
Expected: `test_agent_run_accepts_alternatives_agent_name`이 `pydantic.ValidationError`로 FAIL
(`agent_name`이 아직 `Literal`에 `"alternatives"`를 포함하지 않음), `test_venture_scout_state_has_critic_scorecard_field`도 FAIL.

- [ ] **Step 3: `shared/contracts.py` 수정**

`shared/contracts.py:26-34`의 현재 내용:

```python
AgentName = Literal[
    "structuring",
    "market",
    "competitor",
    "tech",
    "ip",
    "bm",
    "critic",
]
```

다음으로 교체:

```python
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
```

- [ ] **Step 4: `shared/state.py` 수정**

`shared/state.py:36-39`의 현재 내용:

```python
    # 최종 의사결정 뷰.
    critic: CriticResult
    decision: Decision
    final_report: dict[str, Any]
```

다음으로 교체:

```python
    # 최종 의사결정 뷰.
    critic: CriticResult
    decision: Decision
    final_report: dict[str, Any]
    critic_scorecard: dict[str, Any]
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_contracts.py -v`
Expected: 전부 PASS (기존 4개 + 새 2개 = 6개)

- [ ] **Step 6: 커밋**

```bash
git add shared/contracts.py shared/state.py tests/test_contracts.py
git commit -m "feat: alternatives agent_name과 critic_scorecard state 필드 추가"
```

---

### Task 2: `_kill_reason()` 순수 함수 추가

**Files:**
- Modify: `agents/graph.py:24` (import), `agents/graph.py:863` (위치 — `_decide()` 바로 다음)
- Test: `tests/test_alternatives_node.py` (신규 생성)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_alternatives_node.py` 신규 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: FAIL with `ImportError: cannot import name '_kill_reason'`

- [ ] **Step 3: `typing` import에 `Literal` 추가**

`agents/graph.py:24`의 현재 내용:

```python
from typing import Any
```

다음으로 교체:

```python
from typing import Any, Literal
```

- [ ] **Step 4: `_kill_reason()` 구현**

`agents/graph.py`의 `_decide()` 함수 끝(현재 863번 줄, `return ("pivot", ...)` 블록 다음) 바로 뒤에 추가:

```python
def _kill_reason(scorecard: dict[str, Any]) -> Literal["ip_conflict", "weak_evidence"]:
    """kill이 _decide()의 어느 경로(치명적 문제/근거 약함)에서 나왔는지 scorecard로 되짚는다."""
    if scorecard.get("high_ip_candidates") and scorecard.get("contradicting_evidence"):
        return "ip_conflict"
    return "weak_evidence"
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: PASS (2개)

- [ ] **Step 6: 커밋**

```bash
git add agents/graph.py tests/test_alternatives_node.py
git commit -m "feat: kill 원인(IP 충돌/근거 약함) 판별하는 _kill_reason 추가"
```

---

### Task 3: `_alternatives_evidence_ids()` 순수 함수 추가

**Files:**
- Modify: `agents/graph.py` (import 블록, `_kill_reason()` 바로 다음)
- Test: `tests/test_alternatives_node.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_alternatives_node.py`에 추가:

```python
from shared.contracts import AgentRun, IPOverlapCandidate


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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: 위 2개 신규 테스트가 `ImportError: cannot import name '_alternatives_evidence_ids'`로 FAIL (Task 2의 2개는 계속 PASS)

- [ ] **Step 3: `IPOverlapCandidate` import 추가**

`agents/graph.py:44-55`의 현재 내용:

```python
from shared.contracts import (
    AgentName,
    AgentRun,
    AnalysisJob,
    Confidence,
    CriticResult,
    Decision,
    Depth,
    EvidenceItem,
    Hypothesis,
    IdeaRecord,
)
```

다음으로 교체:

```python
from shared.contracts import (
    AgentName,
    AgentRun,
    AnalysisJob,
    Confidence,
    CriticResult,
    Decision,
    Depth,
    EvidenceItem,
    Hypothesis,
    IdeaRecord,
    IPOverlapCandidate,
)
```

- [ ] **Step 4: `_alternatives_evidence_ids()` 구현**

`_kill_reason()` 바로 다음에 추가:

```python
def _alternatives_evidence_ids(
    kill_reason: str,
    scorecard: dict[str, Any],
    agent_runs: list[AgentRun],
    candidates: list[IPOverlapCandidate],
) -> list[str]:
    """대안 제안이 인용할 수 있는 evidence_id를 kill 원인별로 코드가 직접 고른다.

    LLM이 임의로 근거를 인용하지 못하게, 어떤 evidence_id를 써도 되는지 여기서 먼저 정한다.
    """
    if kill_reason == "ip_conflict":
        ip_evidence_ids = {
            candidate.evidence_id
            for candidate in candidates
            if candidate.candidate_id in scorecard.get("high_ip_candidates", [])
        }
        return sorted(set(scorecard.get("contradicting_evidence", [])) | ip_evidence_ids)
    low_confidence_names = set(scorecard.get("low_confidence_agents", []))
    return sorted({
        evidence_id
        for run in agent_runs
        if run.agent_name in low_confidence_names
        for evidence_id in run.grounded_on
    })
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: PASS (4개)

- [ ] **Step 6: 커밋**

```bash
git add agents/graph.py tests/test_alternatives_node.py
git commit -m "feat: kill 원인별 인용 가능 evidence_id를 고르는 _alternatives_evidence_ids 추가"
```

---

### Task 4: `_route_after_critic()` 순수 함수 추가

**Files:**
- Modify: `agents/graph.py` (`critic_node()` 끝, `build_graph()` 시작 직전)
- Test: `tests/test_alternatives_node.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_alternatives_node.py`에 추가:

```python
from langgraph.graph import END


def test_route_after_critic_goes_to_alternatives_on_kill():
    from agents.graph import _route_after_critic

    assert _route_after_critic({"decision": "kill"}) == "alternatives"


def test_route_after_critic_goes_to_end_for_other_decisions():
    from agents.graph import _route_after_critic

    assert _route_after_critic({"decision": "go"}) == END
    assert _route_after_critic({"decision": "pivot"}) == END
    assert _route_after_critic({"decision": "more_research"}) == END
    assert _route_after_critic({}) == END
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: 위 2개가 `ImportError`로 FAIL

- [ ] **Step 3: `_route_after_critic()` 구현**

`agents/graph.py`에서 `critic_node()` 함수가 끝나는 지점(현재 1090번 줄, `return result` 다음)과
`def build_graph():`(현재 1093번 줄) 사이에 추가:

```python
def _route_after_critic(state: VentureScoutState) -> str:
    """critic 직후 라우팅: kill이면 alternatives로, 그 외엔 그래프를 끝낸다."""
    return "alternatives" if state.get("decision") == "kill" else END
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: PASS (6개)

- [ ] **Step 5: 커밋**

```bash
git add agents/graph.py tests/test_alternatives_node.py
git commit -m "feat: critic 이후 kill 분기 라우팅 함수 _route_after_critic 추가"
```

---

### Task 5: `critic_node()`가 `critic_scorecard`를 state로 흘려보내게 수정

**Files:**
- Modify: `agents/graph.py:1064-1070`

`critic_node()`는 LLM 호출(`_agent_output_with_llm`)과 DB 적재(`_agent_run` 내부 `try_persist_agent_run`)를
포함해서 LLM/DB 없이 단위 테스트할 수 없다(기존 `tests/test_critic_decision.py`도 `_decide()`만 테스트하는
이유와 동일). 이 Task는 새 테스트를 추가하지 않고, 이미 통과하는 전체 테스트 스위트로 회귀만 확인한다.

- [ ] **Step 1: 변경 전 전체 테스트 통과 확인 (베이스라인)**

Run: `python -m pytest tests/ -v`
Expected: 이 시점까지 추가된 테스트 전부 PASS (회귀 없음 확인용 베이스라인)

- [ ] **Step 2: `critic_node()` 반환 dict 수정**

`agents/graph.py:1064-1070`의 현재 내용:

```python
    result = {
        "critic": critic,
        "agent_runs": [critic_run],
        "analysis_job": analysis_job,
        "decision": critic.decision,
        "final_report": critic.model_dump(),
    }
```

다음으로 교체:

```python
    result = {
        "critic": critic,
        "agent_runs": [critic_run],
        "analysis_job": analysis_job,
        "decision": critic.decision,
        "final_report": critic.model_dump(),
        "critic_scorecard": scorecard,
    }
```

(`scorecard`는 같은 함수 안에서 이미 계산돼 있는 dict — [agents/graph.py:961-970] 참조. 새 계산 없음.)

- [ ] **Step 3: 전체 테스트 재실행 (회귀 확인)**

Run: `python -m pytest tests/ -v`
Expected: Step 1과 동일하게 전부 PASS (이 변경으로 깨지는 테스트 없어야 함)

- [ ] **Step 4: 커밋**

```bash
git add agents/graph.py
git commit -m "feat: critic_node가 critic_scorecard를 state로 전달"
```

---

### Task 6: `alternatives_node()` 구현

**Files:**
- Modify: `agents/graph.py` (`_route_after_critic()` 바로 위, `critic_node()` 끝 다음)
- Test: `tests/test_alternatives_node.py`

LLM을 호출하는 경로(`_agent_output_with_llm`)는 기존 다른 노드들과 동일하게 단위 테스트 대상이 아니다.
이 Task는 evidence가 비어 LLM 호출 전에 graceful skip되는 경로만 단위 테스트한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_alternatives_node.py`에 추가:

```python
from shared.contracts import AnalysisJob


def test_alternatives_node_skips_when_no_matching_evidence():
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: `ImportError: cannot import name 'alternatives_node'`로 FAIL

- [ ] **Step 3: `alternatives_node()` 구현**

`_route_after_critic()` 바로 위(즉 `critic_node()`가 끝나는 지점과 `_route_after_critic()` 사이)에 추가:

```python
def alternatives_node(state: VentureScoutState) -> dict:
    """decision == 'kill'일 때만 critic 다음에 실행되어, kill 원인별로 대안을 제안한다."""
    start_time = time.time()
    log_stage(logger, "8️⃣", "Alternatives (kill 대안 제안)")

    job_id = state["analysis_job"].job_id
    scorecard = state.get("critic_scorecard", {})
    agent_runs = state.get("agent_runs", [])
    evidence_items = state.get("evidence_items", {})
    candidates = state.get("ip_overlap_candidates", [])

    log_input(logger, {"job_id": job_id, "scorecard": scorecard})

    kill_reason = _kill_reason(scorecard)
    evidence_ids = _alternatives_evidence_ids(kill_reason, scorecard, agent_runs, candidates)
    evidence = [evidence_items[eid] for eid in evidence_ids if eid in evidence_items]

    log_processing(logger, "대안 근거 선택 완료", {
        "kill_reason": kill_reason,
        "evidence_count": len(evidence),
    })

    if not evidence:                       # graceful: 인용 가능 근거 0건 -> run 생략
        log_processing(logger, "대안 근거 0건 — alternatives run 생략(graceful)")
        return {"agent_runs": []}

    role = (
        "IP 시그니처 후보와 반박 근거가 동시에 있어 kill로 판정됐다. "
        "특허 회피 설계 또는 vertical 범위 축소 중심으로, 기존 아이디어의 핵심은 유지한 채 "
        "조정 가능한 대안 2~3개를 제안한다."
        if kill_reason == "ip_conflict" else
        "대부분의 핵심 가설이 low confidence라 근거가 약해 kill로 판정됐다. "
        "더 강한 근거가 있는 타겟/포지셔닝/가격정책으로 전환하는 대안 2~3개를 제안한다."
    )

    output_json = _agent_output_with_llm(
        agent_name="alternatives",
        hypothesis_id="all",
        role=role,
        required_fields=["kill_reason", "alternatives"],
        context={
            "idea": state.get("idea"),
            "kill_reason": kill_reason,
            "critic_objections": state["critic"].objections,
            "evidence": evidence,
        },
    )
    output_json["kill_reason"] = kill_reason

    agent_run = _agent_run(
        job_id=job_id,
        agent_name="alternatives",
        hypothesis_id="all",
        depth="light",
        confidence="low",
        evidence=evidence,
        output_json=output_json,
    )

    duration_ms = (time.time() - start_time) * 1000
    log_completion(logger, "Alternatives", duration_ms)

    return {"agent_runs": [agent_run]}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: PASS (7개)

- [ ] **Step 5: 커밋**

```bash
git add agents/graph.py tests/test_alternatives_node.py
git commit -m "feat: kill 원인별 대안 제안하는 alternatives_node 추가"
```

---

### Task 7: `build_graph()`에 조건부 라우팅 연결

**Files:**
- Modify: `agents/graph.py:1093-1118`
- Test: `tests/test_alternatives_node.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_alternatives_node.py`에 추가:

```python
def test_build_graph_routes_critic_conditionally_to_alternatives():
    from agents.graph import build_graph

    app = build_graph()
    g = app.get_graph()
    assert "alternatives" in g.nodes

    edge_conditional = {(e.source, e.target): e.conditional for e in g.edges}
    assert edge_conditional[("critic", "alternatives")] is True
    assert edge_conditional[("critic", "__end__")] is True
    assert edge_conditional[("alternatives", "__end__")] is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: FAIL — `"alternatives" in g.nodes`가 `False` (아직 그래프에 등록 안 됨), 또는
`edge_conditional[("critic", "alternatives")]`에서 `KeyError`

- [ ] **Step 3: `build_graph()` 수정**

`agents/graph.py:1093-1118`의 현재 내용:

```python
def build_graph():
    # LangGraph에 노드를 등록하고 실행 순서를 연결한다.
    # 현재 구조는 Structuring 이후 5개 분석 노드가 병렬로 실행되고, 마지막에 Critic이 합친다.
    # 조정 가능 지점:
    # IP가 Tech 결과를 반드시 읽어야 한다면 tech -> ip 순서로 바꿀 수 있다.
    # Market 결과에 따라 Competitor/BM을 생략하는 조건부 edge도 추가할 수 있다.
    graph = StateGraph(VentureScoutState)
    for name, fn in [
        ("structuring", structuring_node),
        ("market", market_node),
        ("competitor", competitor_node),
        ("tech", tech_node),
        ("ip", ip_node),
        ("bm", bm_node),
        ("critic", critic_node),
    ]:
        graph.add_node(name, fn)

    graph.add_edge(START, "structuring")
    # Structuring이 idea/hypotheses/documents를 만든 뒤, 각 가설별 agent가 같은 state를 읽는다.
    for node in ["market", "competitor", "tech", "ip", "bm"]:
        graph.add_edge("structuring", node)
        # 각 분석 노드가 agent_runs/evidence_items를 state에 누적하면 Critic이 마지막에 전체를 검수한다.
        graph.add_edge(node, "critic")
    graph.add_edge("critic", END)
    return graph.compile()
```

다음으로 교체:

```python
def build_graph():
    # LangGraph에 노드를 등록하고 실행 순서를 연결한다.
    # 현재 구조는 Structuring 이후 5개 분석 노드가 병렬로 실행되고, 마지막에 Critic이 합친다.
    # 조정 가능 지점:
    # IP가 Tech 결과를 반드시 읽어야 한다면 tech -> ip 순서로 바꿀 수 있다.
    # Market 결과에 따라 Competitor/BM을 생략하는 조건부 edge도 추가할 수 있다.
    graph = StateGraph(VentureScoutState)
    for name, fn in [
        ("structuring", structuring_node),
        ("market", market_node),
        ("competitor", competitor_node),
        ("tech", tech_node),
        ("ip", ip_node),
        ("bm", bm_node),
        ("critic", critic_node),
        ("alternatives", alternatives_node),
    ]:
        graph.add_node(name, fn)

    graph.add_edge(START, "structuring")
    # Structuring이 idea/hypotheses/documents를 만든 뒤, 각 가설별 agent가 같은 state를 읽는다.
    for node in ["market", "competitor", "tech", "ip", "bm"]:
        graph.add_edge("structuring", node)
        # 각 분석 노드가 agent_runs/evidence_items를 state에 누적하면 Critic이 마지막에 전체를 검수한다.
        graph.add_edge(node, "critic")
    # decision == "kill"일 때만 alternatives_node로 이어진다(그 외엔 그래프 종료).
    graph.add_conditional_edges(
        "critic", _route_after_critic, {"alternatives": "alternatives", END: END}
    )
    graph.add_edge("alternatives", END)
    return graph.compile()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: PASS (8개)

- [ ] **Step 5: 전체 테스트 스위트 재확인**

Run: `python -m pytest tests/ -v`
Expected: 전부 PASS (그래프 구조 변경이 다른 기존 테스트를 깨지 않는지 확인)

- [ ] **Step 6: 커밋**

```bash
git add agents/graph.py tests/test_alternatives_node.py
git commit -m "feat: critic 다음 kill 분기로 alternatives_node 조건부 연결"
```

---

### Task 8: API — `STAGE_LABELS`에 `"alternatives"` 등록

**Files:**
- Modify: `app/api.py:32-40`
- Test: `tests/test_alternatives_node.py`

`KNOWN_NODES`는 `STAGE_LABELS`의 키 집합([app/api.py:41](../../../app/api.py))이라, 이 등록을 빠뜨리면
`astream_events` 필터([app/api.py:162](../../../app/api.py))가 `alternatives` 노드의 stage/agent_run
이벤트를 그냥 버린다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_alternatives_node.py`에 추가:

```python
def test_alternatives_stage_registered_in_api():
    from app.api import KNOWN_NODES, STAGE_LABELS

    assert "alternatives" in STAGE_LABELS
    assert "alternatives" in KNOWN_NODES
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: FAIL — `"alternatives" in STAGE_LABELS`가 `False`

- [ ] **Step 3: `app/api.py` 수정**

`app/api.py:32-40`의 현재 내용:

```python
STAGE_LABELS: dict[str, str] = {
    "structuring": "① 구조화 — 아이디어를 가설로 분해",
    "market": "② 시장 (full)",
    "competitor": "③ 경쟁 (light)",
    "tech": "④ 기술 (light)",
    "ip": "⑤ IP 청구항 중첩 (시그니처·full)",
    "bm": "⑥ 비즈니스 모델 (light)",
    "critic": "⑦ Critic — 적대 검증 + 판단",
}
```

다음으로 교체:

```python
STAGE_LABELS: dict[str, str] = {
    "structuring": "① 구조화 — 아이디어를 가설로 분해",
    "market": "② 시장 (full)",
    "competitor": "③ 경쟁 (light)",
    "tech": "④ 기술 (light)",
    "ip": "⑤ IP 청구항 중첩 (시그니처·full)",
    "bm": "⑥ 비즈니스 모델 (light)",
    "critic": "⑦ Critic — 적대 검증 + 판단",
    "alternatives": "⑧ 대안 제안 (kill 시에만)",
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: PASS (9개)

- [ ] **Step 5: 커밋**

```bash
git add app/api.py tests/test_alternatives_node.py
git commit -m "feat: API STAGE_LABELS에 alternatives 단계 등록"
```

---

### Task 9: UI — `AGENT_KO` 추가 + `_render_board()`에 대안 섹션 추가

**Files:**
- Modify: `app/ui.py:39-43` (`AGENT_KO`)
- Modify: `app/ui.py:144-151` (`_render_board()` 끝부분)
- Test: `tests/test_alternatives_node.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_alternatives_node.py`에 추가:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: 3개 모두 FAIL — `AGENT_KO["alternatives"]`에서 `KeyError`,
`"🔁 대안 제안" in board`가 `False`

- [ ] **Step 3: `AGENT_KO` 수정**

`app/ui.py:39-43`의 현재 내용:

```python
AGENT_KO = {
    "market": "시장", "competitor": "경쟁", "tech": "기술",
    "ip": "IP(특허)", "bm": "비즈니스모델", "critic": "Critic",
    "structuring": "구조화",
}
```

다음으로 교체:

```python
AGENT_KO = {
    "market": "시장", "competitor": "경쟁", "tech": "기술",
    "ip": "IP(특허)", "bm": "비즈니스모델", "critic": "Critic",
    "structuring": "구조화", "alternatives": "대안 제안",
}
```

- [ ] **Step 4: `_render_board()` 수정**

`app/ui.py:144-151`의 현재 내용:

```python
    exps = report.get("next_experiments", [])
    if exps:
        lines.append("### 🔬 다음 실험 (이 판정을 검증/뒤집으려면)")
        lines.append("아래 검증을 먼저 수행하면 근거가 보강돼 판정이 더 확실해집니다.")
        lines.append("")
        for e in exps:
            lines.append(f"- {e}")
        lines.append("")

    return "\n".join(lines)
```

다음으로 교체:

```python
    exps = report.get("next_experiments", [])
    if exps:
        lines.append("### 🔬 다음 실험 (이 판정을 검증/뒤집으려면)")
        lines.append("아래 검증을 먼저 수행하면 근거가 보강돼 판정이 더 확실해집니다.")
        lines.append("")
        for e in exps:
            lines.append(f"- {e}")
        lines.append("")

    alt_run = next((r for r in runs if r.get("agent_name") == "alternatives"), None)
    if decision == "kill" and alt_run:
        alts = (alt_run.get("output_json") or {}).get("alternatives", [])
        lines.append("### 🔁 대안 제안 (이 방향이면 다시 검토할 만함)")
        for a in alts:
            lines.append(f"- **{a.get('title', '')}** — {a.get('rationale', '')}")
            lines.append(f"  - 다음 실험: {a.get('next_experiment', '')}")
        lines.append("")

    return "\n".join(lines)
```

(`runs`는 같은 함수 위쪽에서 이미 `runs = report.get("agent_runs", [])`로 정의돼 있음 — 새 변수 아님.)

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_alternatives_node.py -v`
Expected: PASS (12개)

- [ ] **Step 6: 전체 테스트 스위트 최종 확인**

Run: `python -m pytest tests/ -v`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add app/ui.py tests/test_alternatives_node.py
git commit -m "feat: Evidence Board에 kill 대안 제안 섹션 추가"
```

---

## 완료 후 수동 확인 (선택, LLM 키 필요)

자동 테스트는 모두 LLM 호출 없이 순수 로직만 검증한다. 실제 LLM 호출 경로(`alternatives_node`의
`_agent_output_with_llm` 부분)는 다른 노드들과 동일하게 자동 테스트 대상이 아니므로, 실제 동작
확인은 API 서버를 띄우고 kill이 나올 만한 입력으로 `/analyze`를 호출해 SSE `report` 이벤트의
`agent_runs`에 `agent_name="alternatives"` 항목이 포함되는지, Evidence Board에 "🔁 대안 제안"
섹션이 뜨는지 직접 확인한다.
