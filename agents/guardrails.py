"""
agents/guardrails.py

LLM 출력의 과장(Overclaim) 표현을 탐지하기 위한 Guardrail 모듈.

역할:
1. 사업성/시장성/특허성 평가 시 과도한 단정 표현 차단
2. 근거 없는 확정적 주장 방지
3. Critic Agent 및 Output Validator 지원
4. AI 환각(Hallucination) 위험 감소

예:
❌ "침해 위험이 없다"
❌ "성공 가능성이 높다"
❌ "경쟁사가 없다"

✅ "현재 수집된 증거 기준으로는 위험이 낮아 보인다"
✅ "추가 검증이 필요하다"
"""

from agents.logger import get_logger

logger = get_logger("guardrails")

# ------------------------------------------------------------------
# 사용 금지 표현 목록
#
# VentureScout는 근거 기반 분석 시스템이므로
# 확정적 표현 대신 확률적/가설적 표현을 사용해야 함.
# ------------------------------------------------------------------

BANNED_CLAIMS = [

    # 사업 성공 단정
    "성공 가능성이 높다",

    # 법률/IP 단정
    "침해 위험이 없다",
    "침해하지 않는다",
    "법적으로 안전하다",

    # 경쟁 환경 단정
    "경쟁사가 없다",

    # 시장성 단정
    "시장 규모는 확실히 크다",

    # 특허 단정
    "특허 공백이다",
]


def detect_overclaim(text: str) -> list[str]:
    """
    금지된 과장 표현 탐지

    입력:
        분석 결과 텍스트

    출력:
        발견된 금지 표현 목록

    예:

    detect_overclaim(
        "침해 위험이 없다"
    )

    결과:

    ["침해 위험이 없다"]
    """

    found = [
        phrase
        for phrase in BANNED_CLAIMS
        if phrase in text
    ]

    if found:
        logger.warning(f"⚠️  금지된 과장 표현 발견: {found}")
    else:
        logger.debug("✓ 과장 표현 검사 통과")

    return found