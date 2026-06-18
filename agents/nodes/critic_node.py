"""
agents/nodes/critic_node.py

Critic Agent 노드.

역할:
1. 모든 Agent 결과 종합 검토
2. 근거(Evidence) 누락 여부 확인
3. 과장(Overclaim) 표현 검출
4. Low Confidence 결과 식별
5. 반론(Objection) 생성
6. 최종 의사결정(Go / Pivot / Kill / More Research)
7. 최종 보고서 생성

VentureScout에서 가장 중요한 검증 단계이며,
다른 Agent들의 결과를 그대로 믿지 않고
한 번 더 비판적으로 검토한다.

쉽게 말하면:

Market Agent      → 시장 분석
Tech Agent        → 기술 분석
IP Agent          → 특허 분석
BM Agent          → 사업모델 분석

        ↓

      Critic

        ↓

"정말 이 결론을 믿어도 되는가?"

를 검토하는 역할

흐름:

각 Agent 결과
      ↓
Evidence 확인
      ↓
Overclaim 검사
      ↓
Low Confidence 검사
      ↓
반론 생성
      ↓
최종 의사결정
      ↓
Final Report 생성
"""

from agents.state import VentureScoutState
from agents.guardrails import detect_overclaim
from agents.mock_repository import MockRepository

# 테스트용 Repository
repo = MockRepository()


def critic_node(state: VentureScoutState) -> VentureScoutState:
    """
    Critic Agent

    입력:
        VentureScoutState

    출력:
        state["critic_result"]
        state["decision"]
        state["final_report"]

    주요 역할:
    - Agent 결과 검증
    - 반론 생성
    - 최종 판단
    """

    # ------------------------------------------------------------------
    # 각 Agent 결과 수집
    # ------------------------------------------------------------------

    results = [
        state.get("market_result"),
        state.get("competitor_result"),
        state.get("tech_result"),
        state.get("ip_result"),
        state.get("bm_result"),
    ]

    # None 제거
    results = [
        r
        for r in results
        if r
    ]

    # ------------------------------------------------------------------
    # Critic 분석용 변수
    # ------------------------------------------------------------------

    objections = []         # 반론
    overclaim_points = []   # 과장 표현
    missing_evidence = []   # 증거 부족

    grounded_on = []        # 전체 Evidence

    # ------------------------------------------------------------------
    # Agent 결과 검토
    # ------------------------------------------------------------------

    for r in results:

        # Evidence 수집
        grounded_on.extend(
            r.get("grounded_on", [])
        )

        # --------------------------------------------------------------
        # 근거 누락 검사
        # --------------------------------------------------------------

        if not r.get("grounded_on"):

            objections.append(
                f"{r['agent_name']} 결과에 evidence_id가 없습니다."
            )

        # --------------------------------------------------------------
        # 과장 표현 검사
        # --------------------------------------------------------------

        text = str(r)

        found = detect_overclaim(text)

        for phrase in found:

            overclaim_points.append(
                f"{r['agent_name']} overclaim: {phrase}"
            )

        # --------------------------------------------------------------
        # Confidence 검사
        # --------------------------------------------------------------

        if r.get("confidence") == "low":

            missing_evidence.append(
                f"{r['agent_name']} 결과는 "
                f"Low confidence이므로 추가 검증 필요"
            )

    # ------------------------------------------------------------------
    # 최종 의사결정
    #
    # 현재 Mock 시나리오 기준:
    # 기술 구현은 가능
    # 특허 리스크 존재
    # 경쟁 차별화 필요
    #
    # => Pivot
    # ------------------------------------------------------------------

    decision = "pivot"

    decision_reason = (
        "현재 mock 근거 기준으로는 기술 구현 가능성은 있으나, "
        "범용 회의록 자동화는 IP 중첩 신호와 경쟁 리스크가 있어 "
        "산업 특화 workflow 방향으로 피벗 검토가 타당하다."
    )

    # ------------------------------------------------------------------
    # Critic 결과 생성
    # ------------------------------------------------------------------

    output = {

        # Agent 정보
        "agent_name": "critic",

        "hypothesis_id": None,

        "depth": "full",

        "confidence": "mid",

        # 모든 Agent가 사용한 Evidence
        "grounded_on":
            sorted(set(grounded_on)),

        # 최종 판단 요약
        "summary":
            decision_reason,

        # 핵심 발견사항
        "key_findings": [
            "Tech 결과는 구현 가능성보다 비용/지연 검증 필요성을 강조한다.",
            "IP 결과는 법적 판단이 아니라 청구항 중첩 신호로 제한되어야 한다.",
        ],

        # 발견된 문제점
        "risks":
            objections
            + overclaim_points
            + missing_evidence,

        # 추천 액션
        "recommendations": [
            "산업군 1개를 정해 vertical workflow로 좁히기",
            "30분 회의 샘플 기준 처리 비용 측정",
            "상위 유사 특허 독립항 5건 수동 검토",
        ],

        # 추가 연구 필요
        "needs_more_research": True,

        # 상세 결과
        "output_json": {

            # 최종 결정
            "decision":
                decision,

            # 결정 근거
            "decision_reason":
                decision_reason,

            # 반론
            "objections":
                objections,

            # 과장 표현
            "overclaim_points":
                overclaim_points,

            # 증거 부족
            "missing_evidence":
                missing_evidence,

            # 다음 실험
            "next_experiments": [
                "타깃 고객 10명 인터뷰",
                "회의 후 업무 추적 MVP 제작",
                "상위 유사 특허 독립항 수동 검토",
            ],
        },
    }

    # ------------------------------------------------------------------
    # State 저장
    # ------------------------------------------------------------------

    state["critic_result"] = output

    # 최종 의사결정 저장
    state["decision"] = decision

    # 최종 보고서 저장
    state["final_report"] = output["output_json"]

    # ------------------------------------------------------------------
    # 실행 결과 저장
    # 실제 서비스에서는 DB 저장
    # ------------------------------------------------------------------

    repo.insert_agent_run(output)

    return state