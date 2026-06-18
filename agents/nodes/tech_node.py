"""
agents/nodes/tech_node.py

기술성(Technical Feasibility) 검증 노드.

역할:
1. 기술 관련 가설(H4) 검증
2. Repository에서 기술 증거(Evidence) 조회
3. 기술 구현 가능성 평가
4. 기술 리스크 및 비용 리스크 분석
5. Grounding 검증 수행
6. Agent 실행 결과 저장

현재는 Mock Evidence를 기반으로 동작하지만,
실서비스에서는 기술 문서, API 문서, 벤치마크 결과,
PoC 결과 등을 활용하여 분석하게 된다.

검증 대상 가설:

H4:
"핵심 기능은 현재 기술로 프로토타입 구현 가능하다."

흐름:

H4
 ↓
Evidence 조회
 ↓
기술성 평가
 ↓
Grounding 검증
 ↓
State 저장
 ↓
Repository 저장
"""

from agents.state import VentureScoutState
from agents.mock_repository import MockRepository
from agents.grounding import validate_grounded_output

# 테스트용 Repository
repo = MockRepository()


def tech_node(state: VentureScoutState) -> VentureScoutState:
    """
    기술성 분석 노드

    입력:
        VentureScoutState

    출력:
        state["tech_result"]

    주요 평가 항목:
    - 구현 가능성
    - 필요한 기술 스택
    - API 의존성
    - 비용 리스크
    - 성능 리스크
    """

    # ------------------------------------------------------------------
    # H4(기술 가설) 관련 Evidence 조회
    # ------------------------------------------------------------------

    evidence = repo.get_evidence_for_hypothesis("H4")

    # Grounding 검증에 사용할 허용 Evidence ID
    allowed_ids = [
        e["evidence_id"]
        for e in evidence
    ]

    # ------------------------------------------------------------------
    # Tech Agent 분석 결과 생성
    # 실제 서비스에서는 LLM + Evidence 기반 생성
    # ------------------------------------------------------------------

    output = {

        # Agent 식별 정보
        "agent_name": "tech",

        # 검증 대상 가설
        "hypothesis_id": "H4",

        # 빠른 분석 모드
        "depth": "light",

        # 현재 판단 신뢰도
        "confidence": "low",

        # 어떤 Evidence를 근거로 사용했는가
        "grounded_on": allowed_ids,

        # 기술성 분석 요약
        "summary": (
            "STT와 LLM 요약 API 조합으로 "
            "프로토타입 구현 가능성은 있으나, "
            "긴 회의 처리 지연과 API 비용이 주요 리스크다."
        ),

        # 핵심 발견사항
        "key_findings": [
            "STT와 요약 기능은 상용 API로 구현 가능",
            "긴 회의는 토큰 비용과 처리 시간이 증가할 수 있음",
        ],

        # 주요 리스크
        "risks": [
            "latency 증가",
            "API 비용 증가",
            "회의 데이터 보안 요구",
        ],

        # 권장 검증 액션
        "recommendations": [
            "30분 회의 10건 기준 처리 시간 측정",
            "회의 후 배치 요약 MVP부터 검증",
        ],

        # 추가 조사 필요 여부
        "needs_more_research": True,

        # Agent 전용 상세 결과
        "output_json": {

            # 기술 구현 가능성 신호
            "feasibility_signal": "low",

            # 필요한 외부 서비스
            "required_models_or_apis": [
                "STT API",
                "LLM summarization API",
            ],

            # 비용 리스크
            "cost_risks": [
                "token cost",
                "audio transcription cost",
            ],
        },
    }

    # ------------------------------------------------------------------
    # Grounding 검증
    #
    # 1. Evidence 참조가 올바른가?
    # 2. grounded_on이 비어있지 않은가?
    # 3. 과장 표현이 없는가?
    # ------------------------------------------------------------------

    ok, errors = validate_grounded_output(
        output,
        allowed_ids
    )

    # Grounding 품질 점수
    output["groundedness_score"] = (
        1.0 if ok else 0.0
    )

    # 과장 또는 검증 실패 여부
    output["overclaim_flag"] = not ok

    # 검증 오류 목록
    output["validation_errors"] = errors

    # ------------------------------------------------------------------
    # State 저장
    # ------------------------------------------------------------------

    state["tech_result"] = output

    # ------------------------------------------------------------------
    # 실행 결과 저장
    # 실제 서비스에서는 DB 저장
    # ------------------------------------------------------------------

    repo.insert_agent_run(output)

    return state