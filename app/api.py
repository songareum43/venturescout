"""
Track D — FastAPI (비동기 job + SSE 스트리밍).
얇은 클라이언트 원칙: 에이전트 로직은 graph(C)에, API는 호출 + 이벤트 봉투 중계만.

★ ADR-007 — D가 SSE 이벤트 봉투 포맷 소유(겉), 내부는 LangGraph astream_events(안).
★ ADR-023 — 단일 실행: astream_events 한 번으로 단계 중계 + 최종 state 캡처
            (ainvoke 재실행 없음). findings/evidence_pool reducer 전제(state.py).
"""
from __future__ import annotations
import asyncio
import json
import os
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.graph import build_graph

app = FastAPI(title="VentureScout API")

# 실 LLM 붙으면 0으로 (ADR open). 데모 시 단계 가시성 확보용 인위 지연.
DEMO_DELAY = float(os.getenv("DEMO_DELAY", "0.4"))

# 노드명 → 표시 라벨 (D 소유 봉투의 일부). C가 노드 추가/개명 시 여기만 보강.
STAGE_LABELS: dict[str, str] = {
    "structuring": "① 구조화 — 아이디어를 가설로 분해",
    "market": "② 시장 (full)",
    "competitor": "③ 경쟁 (light)",
    "tech": "④ 기술 (light)",
    "ip": "⑤ IP 청구항 중첩 (시그니처·full)",
    "bm": "⑥ 비즈니스 모델 (light)",
    "critic": "⑦ Critic — 적대 검증 + 판단",
}
KNOWN_NODES = set(STAGE_LABELS)

# 그래프 1회 컴파일 (워밍업). C가 노드 교체해도 api.py 불변.
_graph = build_graph()


class AnalyzeRequest(BaseModel):
    idea: str


def _sse(payload: dict) -> str:
    """SSE 한 이벤트로 직렬화 (ensure_ascii=False → 한글 그대로)."""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(req: AnalyzeRequest) -> StreamingResponse:
    """SSE 스트리밍: job → 에이전트 단계(running/done) → 최종 report.

    봉투 포맷 (ADR §3, UI·평가가 의존):
      {"type":"job",   "status":"running|done|failed", "stage":null}
      {"type":"stage", "stage":"<노드명>", "label":"<표시>", "status":"running|done"}
      {"type":"report","decision":"...", "summary":"...", "findings":[...]}
    """

    async def event_stream() -> AsyncIterator[str]:
        yield _sse({"type": "job", "status": "running", "stage": None})

        # astream_events 한 번으로 단계 중계 + 최종 state 누적 (재실행 없음)
        findings: list[dict] = []
        critic: dict | None = None

        # ① 입력: 현재 idea는 평문 → graph가 idea dict 기대. 최소 래핑(① 구조화가 채움).
        #   키는 DB 스키마(ideas.raw_input)와 일치시킴 — C가 실 구조화 붙일 때 그대로 읽도록.
        init_state = {"idea": {"raw_input": req.idea}}

        try:
            async for ev in _graph.astream_events(init_state, version="v2"):
                etype = ev["event"]
                name = ev.get("name")
                if name not in KNOWN_NODES:
                    continue

                if etype == "on_chain_start":
                    yield _sse({
                        "type": "stage", "stage": name,
                        "label": STAGE_LABELS[name], "status": "running",
                    })
                    if DEMO_DELAY:
                        await asyncio.sleep(DEMO_DELAY)

                elif etype == "on_chain_end":
                    out = ev.get("data", {}).get("output") or {}
                    if isinstance(out, dict):
                        for f in out.get("findings", []) or []:
                            findings.append(
                                f.model_dump() if hasattr(f, "model_dump") else f
                            )
                        c = out.get("critic")
                        if c is not None:
                            critic = c.model_dump() if hasattr(c, "model_dump") else c
                    yield _sse({
                        "type": "stage", "stage": name,
                        "label": STAGE_LABELS[name], "status": "done",
                    })

        except Exception as exc:  # noqa: BLE001 — 데모용 광역 캐치 후 봉투로 보고
            yield _sse({"type": "job", "status": "failed", "stage": None,
                        "error": f"{type(exc).__name__}: {exc}"})
            return

        # 최종 리포트 (critic 판단 + 누적 findings = Evidence Board 소스)
        decision = (critic or {}).get("decision", "more_research")
        summary = (critic or {}).get("summary", "근거 부족 — 추가 검증 필요")
        yield _sse({
            "type": "report",
            "decision": decision,
            "summary": summary,
            "confidence": (critic or {}).get("confidence"),
            "objections": (critic or {}).get("objections", []),
            "next_experiments": (critic or {}).get("next_experiments", []),
            "findings": findings,
        })
        yield _sse({"type": "job", "status": "done", "stage": None})

    return StreamingResponse(event_stream(), media_type="text/event-stream")