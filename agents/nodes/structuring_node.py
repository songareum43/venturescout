"""
agents/nodes/structuring_node.py

사용자의 원본 아이디어(raw_input)를 VentureScout가 분석 가능한
구조화 데이터로 변환하는 첫 번째 노드(Node).

역할:
1. 사용자 입력 해석
2. 아이디어 핵심 요소 추출
3. 사업 아이디어 구조화
4. 기술 요소 추출
5. 특허 검색 키워드 생성
6. 검증할 가설(Hypothesis) 생성

현재는 Mock 버전으로 고정 데이터를 입력하지만,
실서비스에서는 LLM(Claude/GPT)이 해당 작업을 수행한다.

흐름:

raw_input
    ↓
Structuring Node
    ↓
title
target_customer
technical_elements
patent_keywords
hypotheses
    ↓
후속 Agent 전달
"""

from agents.state import VentureScoutState


def structuring_node(state: VentureScoutState) -> VentureScoutState:
    """
    아이디어 구조화 노드

    입력:
        사용자의 자유 입력(raw_input)

    출력:
        구조화된 사업 아이디어 정보
        + 초기 가설 목록

    현재는 Mock 데이터 사용
    """

    # 사용자 원본 입력
    raw = state["raw_input"]

    # ------------------------------------------------------------------
    # 아이디어 기본 정보 추출
    # 실제 구현에서는 LLM이 추출
    # ------------------------------------------------------------------

    state["title"] = "AI 회의록 자동화 SaaS"

    state["idea_type"] = "ai_saas"

    state["target_customer"] = "회의가 많은 B2B 조직"

    state["problem_statement"] = (
        "회의 후 정리와 후속 업무 추적이 번거롭다."
    )

    state["solution_summary"] = (
        "AI가 회의 내용을 요약하고 액션아이템을 자동 생성한다."
    )

    state["business_model_hint"] = (
        "팀 단위 월 구독형 SaaS"
    )

    # ------------------------------------------------------------------
    # 핵심 기술 요소
    # 이후 Tech Agent, IP Agent가 활용
    # ------------------------------------------------------------------

    state["technical_elements"] = [
        "speech-to-text",          # 음성 → 텍스트 변환
        "meeting summarization",   # 회의 요약
        "action item extraction",  # 액션아이템 추출
        "workflow integration",    # Slack/Notion 연동
    ]

    # ------------------------------------------------------------------
    # 특허 검색용 키워드
    # 이후 Patent Search Agent 활용
    # ------------------------------------------------------------------

    state["patent_keywords"] = [
        "meeting transcription",
        "automatic summarization",
        "action item generation",
    ]

    # ------------------------------------------------------------------
    # 초기 가설 생성
    #
    # VentureScout는
    # "아이디어 평가"가 아니라
    # "가설 검증" 방식으로 동작
    # ------------------------------------------------------------------

    state["hypotheses"] = [

        # --------------------------------------------------------------
        # H1 고객 문제 가설
        # --------------------------------------------------------------
        {
            "hypothesis_id": "H1",

            "code": "H1",

            "axis": "고객문제",

            "statement":
                "타깃 고객은 회의 후 정리 문제를 반복적으로 겪는다.",

            "confidence": "low",

            "next_validation":
                "타깃 고객 인터뷰",

            "supporting_evidence": [],

            "contradicting_evidence": [],
        },

        # --------------------------------------------------------------
        # H4 기술 구현 가능성 가설
        # --------------------------------------------------------------
        {
            "hypothesis_id": "H4",

            "code": "H4",

            "axis": "기술",

            "statement":
                "핵심 기능은 현재 기술로 프로토타입 구현 가능하다.",

            "confidence": "low",

            "next_validation":
                "30분 회의 10건 기준 처리 시간과 비용 측정",

            "supporting_evidence": [],

            "contradicting_evidence": [],
        },

        # --------------------------------------------------------------
        # H5 특허/IP 가설
        # --------------------------------------------------------------
        {
            "hypothesis_id": "H5",

            "code": "H5",

            "axis": "IP",

            "statement":
                "기존 청구항과 직접 중첩하지 않는 구현 경로가 있다.",

            "confidence": "low",

            "next_validation":
                "상위 유사 특허 독립항 검토",

            "supporting_evidence": [],

            "contradicting_evidence": [],
        },
    ]

    # 다음 노드로 전달
    return state