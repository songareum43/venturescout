"""
Bedrock Claude 모드로 VentureScout LangGraph를 실행하는 스크립트.

AWS 인증 정보와 Bedrock 모델 접근 권한이 준비되어 있어야 한다.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 검증 전용 실행 파일이므로 기존 shell 값이 mock이어도 Bedrock으로 강제한다.
os.environ["AGENT_LLM_PROVIDER"] = "bedrock"

from agents.bedrock_status import summarize_bedrock_run
from agents.graph import build_graph
from agents.llm import validate_bedrock_environment


def main() -> None:
    preflight = validate_bedrock_environment()
    print("[Bedrock preflight]")
    print(json.dumps(preflight, ensure_ascii=False, indent=2))

    raw_input = os.getenv("VENTURESCOUT_RAW_INPUT", "").strip()
    if not raw_input:
        raise SystemExit(
            "VENTURESCOUT_RAW_INPUT is required for a live run. "
            "No mock input fallback is used."
        )

    run_token = uuid.uuid4().hex
    initial_state = {
        # 실제 데이터 전환 지점:
        # 지금은 mock ID와 raw_input으로 Bedrock 연결을 검증한다.
        # 운영에서는 FastAPI/Chainlit이 만든 ID와 파일 파싱 결과를 넣는다.
        "job_id": os.getenv("VENTURESCOUT_JOB_ID", f"live_job_{run_token}"),
        "idea_id": os.getenv("VENTURESCOUT_IDEA_ID", f"live_idea_{run_token}"),
        "raw_input": raw_input,
    }

    result = build_graph().invoke(initial_state)
    critic = result.get("critic")
    verification = summarize_bedrock_run(result)

    print("\n[Bedrock verification]")
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    print("\nDecision:", critic.decision if critic else None)
    print("Final Report:")
    print(
        json.dumps(
            result.get("final_report"),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )

    if not verification["bedrock_verified"]:
        raise SystemExit(
            "Bedrock 호출에 실패한 에이전트가 있어 fallback 결과를 사용했습니다."
        )
    if not verification["bm_fields_generated_by_claude"]:
        raise SystemExit(
            "BM 5필드가 Claude 분석값으로 완전히 승격되지 않았습니다."
        )

    print("\nBedrock 승격 검증 완료: 모든 AgentRun 성공, BM 5필드 Claude 생성 확인")


if __name__ == "__main__":
    main()
