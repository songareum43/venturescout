"""
agents/mock_data.py

개발 및 테스트용 단일 mock 데이터 원천.

이 파일은 실제 DB가 붙기 전까지 9개 Tier 0 테이블의 최소 흐름을
흉내 낸다. graph와 retrieval은 여기 있는 dict/list를 읽어서
shared.contracts의 Pydantic 모델로 변환한다.

실제 데이터 전환 지점:
- 사용자가 업로드한 파일/텍스트는 MOCK_RAW_INPUT 대신 API 또는 파일 파서 결과로 들어와야 한다.
- ideas, hypotheses, documents, evidence_items, ip_overlap_candidates는 이 파일의 고정 dict/list가 아니라 DB 조회 결과로 채워야 한다.
- 실제 전환 후 이 파일은 테스트 fixture 전용으로만 남기고, 운영 코드에서는 직접 import하지 않게 분리하는 것이 좋다.
"""

MOCK_JOB_ID = "job_mock_001"
MOCK_IDEA_ID = "idea_mock_001"


# ------------------------------------------------------------------
# 사용자가 입력한 원문 아이디어
# ------------------------------------------------------------------

MOCK_RAW_INPUT = """
AI 회의록 자동화 SaaS.
회의 음성을 자동으로 텍스트로 변환하고, 핵심 내용을 요약하며,
액션 아이템을 자동 추출해 Slack과 Notion에 연동한다.
타깃 고객은 회의가 많은 B2B 조직이며, 회의 후 정리 시간이 오래 걸리는
문제를 해결한다. 수익모델은 좌석 단위 월 구독형 SaaS다.
"""


# ------------------------------------------------------------------
# ideas 테이블에 들어갈 구조화 결과
# ------------------------------------------------------------------

MOCK_STRUCTURED_IDEA = {
    "idea_id": MOCK_IDEA_ID,
    "raw_input": MOCK_RAW_INPUT,
    "title": "AI 회의록 자동화 SaaS",
    "idea_type": "ai_saas",
    "target_customer": "회의가 많은 B2B 조직",
    "problem_statement": "회의 후 요약, 결정사항 정리, 후속 업무 추적에 시간이 많이 든다.",
    "solution_summary": (
        "회의 음성을 텍스트로 변환하고, 요약과 액션 아이템을 생성한 뒤 "
        "Slack/Notion으로 동기화한다."
    ),
    "business_model_hint": "좌석 단위 월 구독형 SaaS",
    "technical_elements": [
        "speech-to-text",
        "meeting summarization",
        "action item extraction",
        "workflow integration",
    ],
    "patent_keywords": [
        "meeting transcription",
        "automatic summarization",
        "action item generation",
    ],
    "user_confirmed": False,
}


# ------------------------------------------------------------------
# hypotheses 테이블에 들어갈 가설 원장
# ------------------------------------------------------------------

MOCK_HYPOTHESES = [
    {
        "hypothesis_id": "H1",
        "code": "H1",
        "axis": "customer_problem",
        "statement": "타깃 고객은 회의 후 정리와 후속 업무 추적 문제를 반복적으로 겪는다.",
        "confidence": "low",
        "next_validation": "타깃 고객 10명 인터뷰",
    },
    {
        "hypothesis_id": "H2",
        "code": "H2",
        "axis": "competition",
        "statement": "기존 회의 도구는 특정 업무 흐름의 후속 조치까지 충분히 해결하지 못한다.",
        "confidence": "low",
        "next_validation": "인접 제품 5개 기능/가격 비교",
    },
    {
        "hypothesis_id": "H3",
        "code": "H3",
        "axis": "business_model",
        "statement": "B2B 팀은 좌석 단위 SaaS 구독료를 지불할 의사가 있다.",
        "confidence": "low",
        "next_validation": "구매자 persona별 가격 인터뷰",
    },
    {
        "hypothesis_id": "H4",
        "code": "H4",
        "axis": "technology",
        "statement": "핵심 기능은 현재 STT와 LLM API로 프로토타입 구현이 가능하다.",
        "confidence": "low",
        "next_validation": "30분 회의 10건 기준 지연시간과 비용 측정",
    },
    {
        "hypothesis_id": "H5",
        "code": "H5",
        "axis": "ip",
        "statement": "기존 특허 claim과 직접 중첩되지 않는 구현 경로가 있다.",
        "confidence": "low",
        "next_validation": "상위 특허 claim limitation 후보 5건 수동 검토",
    },
]


# ------------------------------------------------------------------
# documents 테이블에 들어갈 근거 출처
# ------------------------------------------------------------------

MOCK_DOCUMENTS = [
    {
        "document_id": "doc_market_001",
        "source_type": "seed_review",
        "title": "회의 후속 업무 pain point seed",
        "clean_text": "회의 후 요약과 후속 업무 추적은 반복적인 운영 부담이다.",
        "reliability_score": 0.55,
        "freshness_score": 0.70,
        "is_user_provided": False,
    },
    {
        "document_id": "doc_competitor_001",
        "source_type": "seed_competitor",
        "title": "인접 회의 자동화 도구 seed",
        "clean_text": "회의 요약 도구는 많지만 vertical workflow 후속 조치 자동화는 차별화 여지가 있다.",
        "reliability_score": 0.60,
        "freshness_score": 0.65,
        "is_user_provided": False,
    },
    {
        "document_id": "doc_bm_001",
        "source_type": "seed_pricing",
        "title": "B2B SaaS seat pricing seed",
        "clean_text": "B2B 생산성 SaaS는 좌석 단위 과금이 흔하지만 구매 의사 검증이 필요하다.",
        "reliability_score": 0.55,
        "freshness_score": 0.60,
        "is_user_provided": False,
    },
    {
        "document_id": "doc_tech_001",
        "source_type": "seed_tech",
        "title": "STT와 LLM 요약 구현 seed",
        "clean_text": "STT API와 LLM 요약 API를 조합하면 회의 요약 프로토타입 구현이 가능하다.",
        "reliability_score": 0.70,
        "freshness_score": 0.75,
        "is_user_provided": False,
    },
    {
        "document_id": "doc_tech_002",
        "source_type": "seed_tech",
        "title": "긴 회의 처리 비용 리스크 seed",
        "clean_text": "긴 회의 음성은 처리 지연과 토큰 비용 증가 문제가 생길 수 있다.",
        "reliability_score": 0.68,
        "freshness_score": 0.75,
        "is_user_provided": False,
    },
    {
        "document_id": "doc_patent_001",
        "source_type": "patent",
        "ext_id": "US-MOCK-001",
        "title": "Meeting transcription and summary mock patent",
        "clean_text": "A claim limitation describes converting meeting speech to text and generating a summary.",
        "reliability_score": 0.95,
        "freshness_score": 0.50,
        "is_user_provided": False,
    },
    {
        "document_id": "doc_patent_002",
        "source_type": "patent",
        "ext_id": "US-MOCK-002",
        "title": "Action item extraction mock patent",
        "clean_text": "A claim limitation describes extracting tasks from transcribed conversation text.",
        "reliability_score": 0.95,
        "freshness_score": 0.50,
        "is_user_provided": False,
    },
]


# ------------------------------------------------------------------
# evidence_items 테이블에 들어갈 근거 원자
# ------------------------------------------------------------------

MOCK_EVIDENCE = [
    {
        "evidence_id": "ev_market_001",
        "hypothesis_id": "H1",
        "document_id": "doc_market_001",
        "source_type": "seed_review",
        "stance": "supports",
        "evidence_text": "회의 후 요약과 후속 업무 추적은 반복적인 운영 부담이다.",
        "relevance_score": 0.76,
        "reliability_score": 0.55,
    },
    {
        "evidence_id": "ev_competitor_001",
        "hypothesis_id": "H2",
        "document_id": "doc_competitor_001",
        "source_type": "seed_competitor",
        "stance": "supports",
        "evidence_text": "회의 요약 도구는 많지만 vertical workflow 후속 조치 자동화는 차별화 여지가 있다.",
        "relevance_score": 0.72,
        "reliability_score": 0.60,
    },
    {
        "evidence_id": "ev_bm_001",
        "hypothesis_id": "H3",
        "document_id": "doc_bm_001",
        "source_type": "seed_pricing",
        "stance": "neutral",
        "evidence_text": "B2B 생산성 SaaS는 좌석 단위 과금이 흔하지만 구매 의사 검증이 필요하다.",
        "relevance_score": 0.68,
        "reliability_score": 0.55,
    },
    {
        "evidence_id": "ev_tech_001",
        "hypothesis_id": "H4",
        "document_id": "doc_tech_001",
        "source_type": "seed_tech",
        "stance": "supports",
        "evidence_text": "STT API와 LLM 요약 API를 조합하면 회의 요약 프로토타입 구현이 가능하다.",
        "relevance_score": 0.82,
        "reliability_score": 0.70,
    },
    {
        "evidence_id": "ev_tech_002",
        "hypothesis_id": "H4",
        "document_id": "doc_tech_002",
        "source_type": "seed_tech",
        "stance": "contradicts",
        "evidence_text": "긴 회의 음성은 처리 지연과 토큰 비용 증가 문제가 생길 수 있다.",
        "relevance_score": 0.78,
        "reliability_score": 0.68,
    },
    {
        "evidence_id": "ev_ip_001",
        "hypothesis_id": "H5",
        "document_id": "doc_patent_001",
        "source_type": "patent",
        "stance": "contradicts",
        "evidence_text": (
            "A claim limitation describes converting meeting speech to text "
            "and generating a summary."
        ),
        "relevance_score": 0.86,
        "reliability_score": 0.95,
    },
    {
        "evidence_id": "ev_ip_002",
        "hypothesis_id": "H5",
        "document_id": "doc_patent_002",
        "source_type": "patent",
        "stance": "neutral",
        "evidence_text": (
            "A claim limitation describes extracting tasks from transcribed "
            "conversation text."
        ),
        "relevance_score": 0.74,
        "reliability_score": 0.95,
    },
]


# ------------------------------------------------------------------
# ip_overlap_candidates 테이블에 들어갈 IP 시그니처 후보
# ------------------------------------------------------------------

MOCK_IP_CANDIDATES = [
    {
        "candidate_id": "cand_001",
        "hypothesis_id": "H5",
        "evidence_id": "ev_ip_001",
        "limitation_id": "lim_001",
        "plan_technical_element": "meeting summarization",
        "limitation_text": "converting meeting speech to text and generating a summary",
        "lexical_score": 0.72,
        "similarity_score": 0.84,
        "hybrid_score": 0.80,
        "rank": 1,
    },
    {
        "candidate_id": "cand_002",
        "hypothesis_id": "H5",
        "evidence_id": "ev_ip_002",
        "limitation_id": "lim_002",
        "plan_technical_element": "action item extraction",
        "limitation_text": "extracting tasks from transcribed conversation text",
        "lexical_score": 0.65,
        "similarity_score": 0.78,
        "hybrid_score": 0.74,
        "rank": 2,
    },
]
