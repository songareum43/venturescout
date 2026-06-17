"""
Track C — LangGraph 골격 (척추).
① 구조화 → ②③④⑤⑥ 분석(①의 출력 공통 입력) → ⑦ Critic.
①(시장맥락 분기)·②특허체인은 병렬, ②③④⑤⑥은 leaf, ⑦이 supervisor/critic.

지금은 노드 stub. 각 노드는 AgentRun(혼합: depth=full/light)을 state["agent_runs"]에 누적.
실제 LLM 연결은 Bedrock ChatBedrockConverse로.

※ 계약: shared.contracts의 AgentRun/CriticResult (C 소유). 분석 본문(signal·next_experiment·
  BM payload 등)은 strict 필드가 아니라 AgentRun.output_json(loose) 안에 담는다(ADR-016).
"""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END
from shared.state import VentureScoutState
from shared.contracts import AgentRun, CriticResult

# TODO(C): Bedrock ChatBedrockConverse 연결, 프롬프트·few-shot·가드레일 중앙 배포

# standalone invoke(예: __main__·harness)용 job_id 폴백. api.py는 실제 job_id를 주입한다.
_MOCK_JOB_ID = "00000000-0000-0000-0000-000000000000"


def _job_id(state: VentureScoutState) -> str:
    return state.get("job_id") or _MOCK_JOB_ID


def structuring_node(state: VentureScoutState) -> dict:
    """① 자유 텍스트 → 가설·기술요소 구조화 (사용자 확인 권장)."""
    # TODO(C): LLM 구조화
    return {"hypotheses": [], "idea": state.get("idea", {})}


def _leaf_run(
    state: VentureScoutState,
    agent: str,
    depth: str,
    signal: str,
    *,
    next_experiment: str | None = None,
    extra: dict | None = None,
) -> AgentRun:
    """leaf 분석 노드 공통 AgentRun 생성.

    signal·next_experiment는 strict 필드가 아니라 output_json(loose)에 담는다
    (C 계약 AgentRun엔 전용 필드가 없음 — ADR-016 본문 loose 원칙).
    """
    output: dict = {"signal": signal}
    if next_experiment:
        output["next_experiment"] = next_experiment
    if extra:
        output.update(extra)
    return AgentRun(
        job_id=_job_id(state),
        hypothesis_id="H0",                  # mock
        agent_name=agent,
        depth=depth,
        confidence="low",
        grounded_on=["ev_mock_0001"],        # 계약상 필수(min_length=1). # TODO(B) 실 evidence_id
        output_json=output,
        status="done",
    )


def market_node(state):       # ② B 소유, full
    return {"agent_runs": [_leaf_run(state, "market", "full", "[MOCK] market")]}

def competitor_node(state):   # ③ B 소유, light
    return {"agent_runs": [_leaf_run(state, "competitor", "light", "[MOCK] competitor")]}

def tech_node(state):         # ④ C 소유, light
    return {"agent_runs": [_leaf_run(state, "tech", "light", "[MOCK] tech")]}

def ip_node(state):           # ⑤ C 소유, full (시그니처) — 후보 읽어 판정
    # TODO(B): vector_search(state["idea"]["technical_elements"]) 실연결 시 grounding
    return {"agent_runs": [_leaf_run(state, "ip", "full", "[MOCK] ip")]}


def _bm_payload(idea: dict) -> dict:
    """⑥ BM payload 5필드 (loose, ADR-016 — 프롬프트 바뀌어도 스키마 마이그레이션 없음).

    전부 str. 지금은 mock("[MOCK] ") — C가 Bedrock 붙이면 idea 기반으로 채움.
    - revenue_model      : 수익 구조 (거래 수수료 / 구독 / 광고 …)
    - pricing_hypothesis : 가격 가설 (가격대·과금 단위)
    - market_size_signal : 시장 규모 신호 (TAM·성장 방향)
    - unit_economics     : 단위경제 방향 (LTV vs CAC 정성 판단)
    - key_risk           : 이 BM이 깨지는 핵심 리스크
    """
    # TODO(C): Bedrock으로 payload 5필드 채우기 (BM 분석 프롬프트는 별도 준비)
    return {
        "revenue_model":      "[MOCK] 거래 수수료 + 프리미엄 구독 혼합",
        "pricing_hypothesis": "[MOCK] 거래액 3% 수수료 / 팀 단위 월 $29 구독",
        "market_size_signal": "[MOCK] 추천 커머스 SaaS TAM 확대 추세(우상향)",
        "unit_economics":     "[MOCK] LTV > CAC 추정(구독 리텐션 가정) — 미검증",
        "key_risk":           "[MOCK] 무료 대체재·플랫폼 자체 추천기능에 마진 잠식",
    }


def bm_node(state):           # ⑥ D 소유, light
    """⑥ Business Model 에이전트(light). idea를 읽어 수익구조·가격·시장·단위경제·리스크 판단.

    light 취지(ADR-014): 근거묶음 + Low confidence + next_experiment를 갖춰 ⑦ Critic이
    검증 가능하게 한다(근거 없는 한 줄 overclaim 금지). BM 본문(payload 5필드)·signal·
    next_experiment는 AgentRun.output_json에 담는다(C 계약, loose).
    """
    idea = state.get("idea", {})        # ① 구조화 출력. 지금은 mock이라 미사용.
    return {
        "agent_runs": [
            _leaf_run(
                state, "bm", "light",
                "[MOCK] 거래 수수료+구독 혼합 수익모델, 단위경제 미검증",
                next_experiment="[MOCK] 가격 민감도 테스트(랜딩 A/B)로 과금단위·전환율 검증",
                extra=_bm_payload(idea),
            )
        ]
    }


def critic_node(state):       # ⑦ C 소유 (척추) — 반박 + 판단
    # TODO(C): ②~⑥ agent_runs 적대 검증, overclaim 차단
    return {"critic": CriticResult(decision="more_research", confidence="low",
                                   summary="[MOCK] 근거 부족, 추가 검증 필요")}


def build_graph():
    g = StateGraph(VentureScoutState)
    for name, fn in [
        ("structuring", structuring_node), ("market", market_node),
        ("competitor", competitor_node), ("tech", tech_node),
        ("ip", ip_node), ("bm", bm_node), ("critic", critic_node),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "structuring")
    for n in ["market", "competitor", "tech", "ip", "bm"]:
        g.add_edge("structuring", n)        # 분석 병렬 분기
        g.add_edge(n, "critic")             # critic이 종합
    g.add_edge("critic", END)
    return g.compile()


if __name__ == "__main__":
    graph = build_graph()
    print(graph.invoke({"idea": {"technical_elements": ["STT", "summary"]}}))
