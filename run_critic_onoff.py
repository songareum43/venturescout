"""일회용: Critic ON/OFF 비교 (N=1). harness 순수 헬퍼 재사용 + 현재 graph 계약(raw_input)에 맞춘 state 주입."""

import os
import sys
import time
import uuid
import json

# Bedrock 인증이 access key/secret로 가도록 bearer token 제거(있다면)
os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
from dotenv import load_dotenv

load_dotenv(override=True)
os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
os.environ["AGENT_LLM_PROVIDER"] = "bedrock"
os.environ["RETRIEVAL"] = "live"

from agents.graph import build_graph
from eval.harness import build_graph_no_critic, _naive_decision, overclaim_count
from agents.llm import reset_usage, usage_snapshot

RAW = (
    "크로스펑셔널 팀을 위한 올인원 업무 관리(work management) SaaS를 만들려고 합니다. "
    "태스크 보드, 타임라인, 간트 차트, 워크플로우 자동화, 문서 협업, AI 기반 생산성 "
    "기능을 한 플랫폼에 통합합니다. Asana, monday.com, ClickUp, Notion, Wrike, "
    "Smartsheet 같은 프로젝트 관리 도구와 경쟁합니다. 가격은 사용자당 월 구독(per-seat) "
    "free / standard / business / enterprise 4단계입니다. 타깃은 제품·엔지니어링 팀이고, "
    "핵심 문제는 여러 도구를 오가며 생기는 컨텍스트 전환 낭비입니다."
)


def fresh_state():
    tok = uuid.uuid4().hex
    return {"job_id": f"live_job_{tok}", "idea_id": f"live_idea_{tok}", "raw_input": RAW}


def timed(graph, st):
    t0 = time.perf_counter()
    out = graph.invoke(st)
    return out, time.perf_counter() - t0


def main():
    print("[OFF] critic 없는 그래프 실행 중...", flush=True)
    off_state, off_lat = timed(build_graph_no_critic(), fresh_state())
    off_runs = off_state.get("agent_runs", [])
    off_decision = _naive_decision(off_runs)

    print("[ON] critic 포함 그래프 실행 중...", flush=True)
    reset_usage()  # ON 1회분 토큰만 측정
    on_state, on_lat = timed(build_graph(), fresh_state())
    cost = usage_snapshot()
    critic = on_state.get("critic")
    on_decision = critic.decision if critic else None

    result = {
        "off_decision": off_decision,
        "on_decision": on_decision,
        "decision_changed": off_decision != on_decision,
        "objections_added": len(critic.objections) if critic else 0,
        "overclaims_in_off": overclaim_count(off_runs),
        "off_latency_s": round(off_lat, 1),
        "on_latency_s": round(on_lat, 1),
        "critic_latency_overhead_s": round(on_lat - off_lat, 1),
        "on_cost_usd": cost["cost_usd"],
        "on_tokens": {
            "input": cost["input_tokens"],
            "output": cost["output_tokens"],
            "calls": cost["calls"],
        },
        "off_agent_runs": len(off_runs),
        "on_agent_runs": len(on_state.get("agent_runs", [])),
    }
    print("\n=== Critic ON/OFF (N=1) ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if critic:
        print("\n[ON] critic summary:")
        print(" ", critic.summary)
        print("\n[ON] objections:")
        for o in critic.objections:
            print("  -", o)


if __name__ == "__main__":
    main()
