"""
agents/nodes/ip_node.py

IP(Intellectual Property) / 특허 리스크 분석 노드.

역할:
1. IP 관련 가설(H5) 검증
2. 특허 청구항(Claim) 중첩 후보 조회
3. 기술 요소별 특허 중복 위험 분석
4. Design-Around(회피 설계) 전략 도출
5. Grounding 검증 수행
6. IP 분석 결과 저장

중요:
이 노드는 법적 침해 여부를 판단하지 않는다.

목적은
"현재 아이디어가 기존 특허와 얼마나 유사한가?"
를 사전에 탐지하는 것이다.

검증 대상 가설:

H5:
"기존 청구항과 직접 중첩하지 않는 구현 경로가 있다."

흐름:

H5
 ↓
특허 후보 검색
 ↓
Claim 유사도 분석
 ↓
중복 위험 요소 식별
 ↓
Design-Around 생성
 ↓
Grounding 검증
 ↓
IP 결과 저장
"""

from agents.state import VentureScoutState
from agents.mock_repository import MockRepository
from agents.grounding import validate_grounded_output

# 테스트용 Repository
repo = MockRepository()


def ip_node(state: VentureScoutState) -> VentureScoutState:
    """
    IP / 특허 분석 노드

    입력:
        VentureScoutState

    출력:
        state["ip_result"]

    주요 평가 항목:
    - 특허 중복 가능성
    - Claim Limitation 유사도
    - 기술 요소별 위험도
    - Design Around 전략
    """

    # ------------------------------------------------------------------
    # H5 관련 특허 후보 조회
    # ------------------------------------------------------------------

    candidates = repo.get_ip_overlap_candidates(
        state["job_id"],
        "H5"
    )

    # Grounding 검증용 Evidence ID
    evidence_ids = [
        c["evidence_id"]
        for c in candidates
    ]

    # 중첩 위험 기술 요소
    high_overlap_elements = []

    # 회피 설계 전략
    design_around_options = []

    # ------------------------------------------------------------------
    # 특허 중복 위험 분석
    #
    # hybrid_score:
    # 키워드 + 임베딩 유사도를 합친 최종 점수
    # ------------------------------------------------------------------

    for c in candidates:

        # 위험도 임계값
        if c["hybrid_score"] >= 0.78:

            high_overlap_elements.append(
                c["plan_technical_element"]
            )

    # ------------------------------------------------------------------
    # Design Around 전략 생성
    #
    # 기존 특허와 직접 충돌하지 않도록
    # 제품 방향을 조정하는 방법
    # ------------------------------------------------------------------

    if "meeting summarization" in high_overlap_elements:

        design_around_options.append(
            "범용 회의 요약보다 산업별 회의 양식 자동 변환으로 좁히기"
        )

    if "action item extraction" in high_overlap_elements:

        design_around_options.append(
            "액션아이템 생성보다 후속 업무 추적 워크플로우에 집중하기"
        )

    # ------------------------------------------------------------------
    # IP Agent 결과 생성
    # 실제 서비스에서는 LLM + Patent Search 활용
    # ------------------------------------------------------------------

    output = {

        # Agent 정보
        "agent_name": "ip",

        # 검증 대상 가설
        "hypothesis_id": "H5",

        # 상세 분석 수행
        "depth": "full",

        # 현재 신뢰도
        "confidence": "mid",

        # 사용한 Evidence
        "grounded_on": evidence_ids,

        # 분석 요약
        "summary": (
            "일부 기술요소는 USPTO 청구항 limitation과 "
            "중첩 신호가 있다. "
            "단, 이는 법적 침해 판단이 아니라 "
            "기술 구성요소 기반의 사전 리스크 신호다."
        ),

        # 핵심 발견사항
        "key_findings": [
            "meeting summarization 요소는 일부 청구항 limitation과 유사하다.",
            "action item extraction 요소도 관련 limitation 후보가 있다.",
        ],

        # 발견된 리스크
        "risks": [
            "범용 회의 요약 기능 중심 진입은 IP 중첩 신호가 있을 수 있다.",
        ],

        # 권장 액션
        "recommendations": [
            "상위 유사 특허 독립항 5건 수동 검토",
            "산업별 workflow automation으로 design-around 검토",
        ],

        # 추가 조사 필요
        "needs_more_research": True,

        # 상세 분석 결과
        "output_json": {

            # 특허 중복 위험 신호
            "overlap_signal": "mid",

            # 위험 요소
            "high_overlap_elements":
                high_overlap_elements,

            # 회피 설계 전략
            "design_around_options":
                design_around_options,

            # 법적 판단이 아님을 명시
            "legal_guardrail_note":
                "법적 침해 판단이 아니라 청구항 중첩 기반 IP 리스크 신호입니다.",

            # 특허 후보 상세 데이터
            "candidates":
                candidates,
        },
    }

    # ------------------------------------------------------------------
    # Grounding 검증
    #
    # 1. Evidence 참조 검증
    # 2. grounded_on 검증
    # 3. 과장 표현 검증
    # ------------------------------------------------------------------

    ok, errors = validate_grounded_output(
        output,
        evidence_ids
    )

    # Grounding 품질 점수
    output["groundedness_score"] = (
        1.0 if ok else 0.0
    )

    # 과장/검증 실패 여부
    output["overclaim_flag"] = not ok

    # 검증 오류 목록
    output["validation_errors"] = errors

    # ------------------------------------------------------------------
    # State 저장
    # ------------------------------------------------------------------

    state["ip_result"] = output

    # ------------------------------------------------------------------
    # Agent 실행 결과 저장
    # 실제 서비스에서는 DB 저장
    # ------------------------------------------------------------------

    repo.insert_agent_run(output)

    return state