"""
Bedrock Claude 모드로 VentureScout LangGraph를 실행하는 스크립트.

AWS 인증 정보와 Bedrock 모델 접근 권한이 준비되어 있어야 한다.
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("AGENT_LLM_PROVIDER", "bedrock")

from agents.graph import build_graph
from agents.mock_data import MOCK_JOB_ID, MOCK_IDEA_ID, MOCK_RAW_INPUT


initial_state = {
    # 실제 데이터 전환 지점:
    # 지금은 mock ID와 mock raw_input으로 Bedrock 연결만 확인한다.
    # 운영에서는 FastAPI/Chainlit이 만든 job_id, idea_id와 파일 파싱 결과 raw_input을 넣는다.
    "job_id": MOCK_JOB_ID,
    "idea_id": MOCK_IDEA_ID,
    "raw_input": MOCK_RAW_INPUT,
}


result = build_graph().invoke(initial_state)
critic = result.get("critic")

print("Model:", result["agent_runs"][-1].model_name)
print("Decision:", critic.decision if critic else None)
print("Final Report:")
print(critic.summary if critic else result.get("final_report"))
