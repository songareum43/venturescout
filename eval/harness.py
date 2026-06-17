"""
Track D — 평가 하네스 (ADR-019: process 기반, outcome 정답 없음).

창업 검증은 verdict 정답이 없으므로 '맞췄나'가 아니라 '과정이 건강한가'를 잰다.
헤드라인 = **Critic ON/OFF 정량화**(멀티에이전트가 실제로 뭘 바꾸는가).

지금은 그래프가 전부 mock이라:
  - 지금 계산: JSON Validity·Groundedness·Overclaim·latency·Critic ON/OFF diff
  - 승격 후(TODO): Precision@K·Contradiction Coverage(B 실검색+정답라벨), Cost(Bedrock 토큰)

graph.py는 건드리지 않는다. Critic OFF는 여기서 critic 없는 그래프를 따로 배선해 만든다.
"""
from __future__ import annotations
import time
from typing import Optional
from langgraph.graph import StateGraph, START, END

from shared.state import VentureScoutState
from shared.contracts import AgentFinding, CriticResult
from agents.graph import (
    build_graph,                                    # Critic ON (척추 그대로)
    structuring_node, market_node, competitor_node,
    tech_node, ip_node, bm_node,                    # critic_node만 빼고 재사용
)

ANALYSIS_NODES = [
    ("market", market_node), ("competitor", competitor_node),
    ("tech", tech_node), ("ip", ip_node), ("bm", bm_node),
]


# ── Critic OFF 그래프 (critic 노드 없이 structuring → 분석 5노드 → END) ──
def build_graph_no_critic():
    """Critic OFF 베이스라인. ⑦ 적대검증 없이 findings만 모으고 끝낸다.

    ON 그래프와 동일 배선에서 critic 노드/엣지만 제거 → 멀티에이전트 효과의
    '대조군'. graph.py 원본 불변(여기서 별도 컴파일).
    """
    g = StateGraph(VentureScoutState)
    g.add_node("structuring", structuring_node)
    for name, fn in ANALYSIS_NODES:
        g.add_node(name, fn)
    g.add_edge(START, "structuring")
    for name, _ in ANALYSIS_NODES:
        g.add_edge("structuring", name)
        g.add_edge(name, END)                       # critic 없이 바로 종료
    return g.compile()


# ── process 지표 (mock에서도 계산 가능) ──
def json_validity(findings: list[AgentFinding]) -> float:
    """findings가 전부 계약(AgentFinding) 스키마를 만족하는 비율. (재검증)"""
    if not findings:
        return 0.0
    ok = 0
    for f in findings:
        try:
            AgentFinding.model_validate(f.model_dump())
            ok += 1
        except Exception:
            pass
    return ok / len(findings)


def groundedness(findings: list[AgentFinding]) -> float:
    """grounded_on(근거 id)이 비어있지 않은 finding 비율. 근거 없는 주장 = 비그라운드."""
    if not findings:
        return 0.0
    return sum(1 for f in findings if f.grounded_on) / len(findings)


def overclaim_count(findings: list[AgentFinding]) -> int:
    """overclaim = 근거 없이(또는 빈약하게) 높은 confidence를 주장하는 finding 수.
    프록시: grounded_on 비었는데 confidence가 low가 아닌 경우. (ADR-014 정직성 위반)"""
    return sum(1 for f in findings if not f.grounded_on and f.confidence != "low")


# ── 헤드라인: Critic ON/OFF 비교 ──
def _naive_decision(findings: list[AgentFinding]) -> str:
    """Critic OFF 베이스라인 판정: 적대검증 없는 낙관 규칙.
    근거 있는 finding이 하나라도 있으면 'go'(편향 그대로) — Critic이 이걸 교정하는지 본다."""
    return "go" if any(f.grounded_on for f in findings) else "more_research"


def _invoke_timed(graph, idea: dict) -> tuple[dict, float]:
    """이미 컴파일된 graph를 1회 invoke → (최종 state, 소요초). 컴파일은 타이밍 밖."""
    t0 = time.perf_counter()
    state = graph.invoke({"idea": idea})
    return state, time.perf_counter() - t0


def _compare_from_runs(off_state: dict, off_latency: float,
                       on_state: dict, on_latency: float) -> dict:
    """이미 돌린 OFF/ON 결과로 헤드라인 지표 산출 (순수 함수 — 재invoke 없음)."""
    off_findings = off_state.get("findings", [])
    critic: Optional[CriticResult] = on_state.get("critic")

    off_decision = _naive_decision(off_findings)
    on_decision = critic.decision if critic else None
    n_objections = len(critic.objections) if critic else 0

    return {
        "off_decision": off_decision,               # 적대검증 없는 낙관 판정
        "on_decision": on_decision,                 # Critic 교정 판정
        "decision_changed": off_decision != on_decision,
        "objections_added": n_objections,           # Critic이 제기한 반박 수
        "overclaims_in_off": overclaim_count(off_findings),
        "critic_latency_overhead_s": round(on_latency - off_latency, 4),
    }


def compare_critic(idea: dict) -> dict:
    """멀티에이전트 헤드라인 지표. ON vs OFF를 같은 idea로 돌려 차이를 정량화.
    OFF·ON 각 1회(총 2회) invoke — 최소 호출."""
    off_state, off_latency = _invoke_timed(build_graph_no_critic(), idea)
    on_state, on_latency = _invoke_timed(build_graph(), idea)
    return _compare_from_runs(off_state, off_latency, on_state, on_latency)


# ── 전체 평가 ──
def evaluate(idea: dict) -> dict:
    """idea 하나에 대한 process 지표 묶음. 지금 계산 가능한 건 실측, 나머진 None+TODO.

    OFF·ON 그래프를 각 1회만 돌리고(총 2회), ON 결과를 agent_metrics·헤드라인이
    공유한다 — 예전엔 evaluate가 ON을 한 번 더 돌려 총 3회였음(실 LLM 시 비용 3배).
    """
    off_state, off_latency = _invoke_timed(build_graph_no_critic(), idea)
    on_state, on_latency = _invoke_timed(build_graph(), idea)
    findings = on_state.get("findings", [])

    return {
        "agent_metrics": {
            "json_validity": json_validity(findings),
            "groundedness": groundedness(findings),
            "overclaim_count": overclaim_count(findings),
        },
        # ★ 헤드라인 (ADR-019) — 위에서 돌린 OFF/ON 재사용(재invoke 없음)
        "multiagent_effect": _compare_from_runs(off_state, off_latency, on_state, on_latency),
        "system_metrics": {
            "latency_s": round(on_latency, 4),
            # TODO(승격): Bedrock 토큰·$ 집계 — 실 LLM 연결 후 (지금 mock은 비용 0)
            "cost_usd": None,
        },
        "retrieval_metrics": {
            # TODO(승격): B 실검색 + 정답 라벨셋 필요 — mock에선 측정 불가
            "precision_at_k": None,        # 회수 근거 중 적합 비율
            "contradiction_coverage": None,  # 반대 근거를 얼마나 길어올렸나
        },
    }


def _print_report(idea: dict) -> None:
    import json
    rep = evaluate(idea)
    print("=== VentureScout 평가 하네스 (mock) ===")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    me = rep["multiagent_effect"]
    print("\n--- 헤드라인 요약 ---")
    print(f"  Critic OFF 판정 : {me['off_decision']}")
    print(f"  Critic ON  판정 : {me['on_decision']}")
    print(f"  판정 바뀜       : {me['decision_changed']}")
    print(f"  Critic 반박 수  : {me['objections_added']}")
    print("  (※ mock 단계라 숫자는 자리만 — C 실LLM 연결 후 의미있는 값)")


if __name__ == "__main__":
    _print_report({"technical_elements": ["추천", "임베딩"], "revenue_hint": "commission"})