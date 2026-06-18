"""
scripts/run_mock_graph.py

VentureScout 전체 LangGraph 워크플로우를
Mock 데이터로 실행하는 테스트 스크립트.

역할:
1. 초기 State 생성
2. Mock 사업 아이디어 입력
3. LangGraph 실행
4. 각 Agent 순차 수행
5. 최종 의사결정 확인
6. 최종 보고서 출력

사용 목적:
- 전체 파이프라인 정상 동작 확인
- Agent 연결 검증
- 디버깅
- 데모 시연

실행 흐름:

MOCK_RAW_INPUT
      ↓
Structuring Node
      ↓
Market Agent
      ↓
Competitor Agent
      ↓
Tech Agent
      ↓
IP Agent
      ↓
BM Agent
      ↓
Critic Agent
      ↓
Decision 출력
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.graph import build_graph
from agents.mock_data import MOCK_RAW_INPUT



# ------------------------------------------------------------------
# LangGraph 시작 상태(State)
#
# 실제 서비스에서는
# API 요청 또는 DB에서 생성
# ------------------------------------------------------------------


initial_state = {

    # 실제 데이터 전환 지점:
    # 운영에서는 API가 analysis_jobs를 만들고 생성된 job_id를 넣는다.
    # 작업 ID
    "job_id": "job_mock_001",

    # 실제 데이터 전환 지점:
    # 운영에서는 ideas를 insert한 뒤 생성된 idea_id를 넣는다.
    # 아이디어 ID
    "idea_id": "idea_mock_001",

    # 실제 데이터 전환 지점:
    # 운영에서는 업로드 파일 파서 또는 request body에서 추출한 원문 텍스트를 넣는다.
    # 사용자 원본 입력
    "raw_input": MOCK_RAW_INPUT,

    # --------------------------------------------------------------
    # Structuring Node가 채울 값들
    # --------------------------------------------------------------

    "title": None,

    "idea_type": None,

    "target_customer": None,

    "problem_statement": None,

    "solution_summary": None,

    "business_model_hint": None,

    "technical_elements": [],

    "patent_keywords": [],

    # 사용자 확인 여부
    "user_confirmed": False,

    # 생성될 가설 목록
    "hypotheses": [],

    # --------------------------------------------------------------
    # Agent 결과 저장 영역
    # --------------------------------------------------------------

    "market_result": None,

    "competitor_result": None,

    "tech_result": None,

    "ip_result": None,

    "bm_result": None,

    # --------------------------------------------------------------
    # 최종 결과
    # --------------------------------------------------------------

    "critic_result": None,

    "decision": None,

    "final_report": None,
}



# ------------------------------------------------------------------
# LangGraph 생성
#
# graph.py에서 정의한 Workflow 로딩
# ------------------------------------------------------------------

app = build_graph()


# ------------------------------------------------------------------
# Graph 실행
#
# 전체 Agent Workflow 수행
# ------------------------------------------------------------------

result = app.invoke(initial_state)


# ------------------------------------------------------------------
# 최종 결과 출력
# ------------------------------------------------------------------

critic = result.get("critic")

print("Decision:", critic.decision if critic else None)

print("Final Report:")

print(critic.summary if critic else result.get("final_report"))
