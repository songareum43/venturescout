"""
Track D — FastAPI (비동기 job + SSE 스트리밍).
얇은 클라이언트 원칙: 에이전트 로직은 graph(C)에, API는 호출 + 이벤트 봉투 중계만.

★ ADR-007 — D가 SSE 이벤트 봉투 포맷 소유(겉), 내부는 LangGraph astream_events(안).
★ ADR-023 — 단일 실행: astream_events 한 번으로 단계 중계 + 최종 state 캡처(ainvoke 재실행 없음).
★ job_id 오케스트레이션 — /analyze 진입점에서 ideas+analysis_jobs 행을 만들어 job_id를 발급하고,
   그 job_id를 graph에 (초기 state + RunnableConfig 둘 다)로 흘린다. C는 state["job_id"]로,
   B(persistence)는 RunnableConfig.configurable.job_id로 읽는다(두 소비자 모두 충족).
   계약: agent_runs/AgentRun, CriticResult (shared.contracts, C 소유).
"""
from __future__ import annotations
import asyncio
import json
import os
from typing import AsyncIterator

import psycopg2
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.graph import build_graph
from agents.input_validation import InsufficientInputError, validate_input_detail

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
    "alternatives": "⑧ 대안 제안 (kill 시에만)",
}
KNOWN_NODES = set(STAGE_LABELS)

# 그래프 1회 컴파일 (워밍업). C가 노드 교체해도 api.py 불변.
_graph = build_graph()


class AnalyzeRequest(BaseModel):
    idea: str


def _sse(payload: dict) -> str:
    """SSE 한 이벤트로 직렬화 (ensure_ascii=False → 한글 그대로)."""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


# ── job_id 라이프사이클 (D 소유 진입점) ───────────────────────────────────────
# DATABASE_URL은 .env에서 옴(검증은 로컬 docker postgres로). config.py(B)가 머지되면
# 그쪽 db_dsn으로 교체 가능 — 지금은 D가 독립적으로 최소 연결만 갖는다.

def _db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)


def _create_job(raw_input: str) -> tuple[str, str]:
    """ideas → analysis_jobs 행을 만들고 (job_id, idea_id) 반환. status=running.

    analysis_jobs.idea_id가 NOT NULL FK라 ideas를 먼저 만든다(스키마: ideas←analysis_jobs).
    """
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ideas (raw_input) VALUES (%s) RETURNING idea_id",
                (raw_input,),
            )
            idea_id = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO analysis_jobs (idea_id, status, started_at) "
                "VALUES (%s, 'running', now()) RETURNING job_id",
                (idea_id,),
            )
            job_id = str(cur.fetchone()[0])
        conn.commit()
        return job_id, idea_id
    finally:
        conn.close()


def _finish_job(job_id: str, status: str, decision: str | None, summary: str | None) -> None:
    """분석 종료 시 analysis_jobs 상태/결정 업데이트 (done|failed)."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE analysis_jobs SET status=%s, decision=%s, decision_summary=%s, "
                "finished_at=now() WHERE job_id=%s",
                (status, decision, summary, job_id),
            )
        conn.commit()
    finally:
        conn.close()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(req: AnalyzeRequest) -> StreamingResponse:
    """SSE 스트리밍: job → 에이전트 단계(running/done) → 최종 report.

    봉투 포맷 (ADR §3, UI·평가가 의존):
      {"type":"job",   "status":"running|done|failed", "stage":null, "job_id":"..."}
      {"type":"stage", "stage":"<노드명>", "label":"<표시>", "status":"running|done"}
      {"type":"report","decision":"...", "summary":"...", "agent_runs":[...]}
    """

    async def event_stream() -> AsyncIterator[str]:
        try:
            validate_input_detail(req.idea)
        except InsufficientInputError as exc:
            yield _sse({
                "type": "job",
                "status": "failed",
                "stage": None,
                "error_code": "insufficient_input",
                "error": str(exc),
            })
            return

        # ① job_id 발급 (ideas+analysis_jobs INSERT). 실패하면 바로 봉투로 보고.
        try:
            job_id, idea_id = await asyncio.to_thread(_create_job, req.idea)
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "job", "status": "failed", "stage": None,
                        "error": f"job 생성 실패: {type(exc).__name__}: {exc}"})
            return

        yield _sse({"type": "job", "status": "running", "stage": None, "job_id": job_id})

        # astream_events 한 번으로 단계 중계 + agent_runs 누적 (재실행 없음)
        agent_runs: list[dict] = []
        critic: dict | None = None
        # 근거 출처 맵 {evidence_id: source_type} — UI가 UUID 대신 한글 출처로 보여주게.
        evidence_sources: dict[str, str] = {}

        # ① 입력: job_id/idea_id/raw_input을 초기 state로(C는 state로 읽음).
        init_state = {
            "job_id": job_id,
            "idea_id": idea_id,
            "raw_input": req.idea,
            "idea": {"raw_input": req.idea},
        }
        # B(persistence)는 RunnableConfig.configurable.job_id로 읽으므로 config에도 주입.
        config = {"configurable": {"job_id": job_id, "idea_id": idea_id}}

        try:
            async for ev in _graph.astream_events(init_state, version="v2", config=config):
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
                        for r in out.get("agent_runs", []) or []:
                            agent_runs.append(
                                r.model_dump() if hasattr(r, "model_dump") else r
                            )
                        # evidence_items {id: EvidenceItem} → {id: source_type} 누적
                        for eid, ev_item in (out.get("evidence_items") or {}).items():
                            src = getattr(ev_item, "source_type", None)
                            if src is None and isinstance(ev_item, dict):
                                src = ev_item.get("source_type")
                            if src:
                                evidence_sources[str(eid)] = src
                        c = out.get("critic")
                        if c is not None:
                            critic = c.model_dump() if hasattr(c, "model_dump") else c
                    yield _sse({
                        "type": "stage", "stage": name,
                        "label": STAGE_LABELS[name], "status": "done",
                    })

        except InsufficientInputError as exc:
            await asyncio.to_thread(_finish_job, job_id, "failed", None, None)
            yield _sse({
                "type": "job",
                "status": "failed",
                "stage": None,
                "job_id": job_id,
                "error_code": "insufficient_input",
                "error": str(exc),
            })
            return
        except Exception as exc:  # noqa: BLE001 — 데모용 광역 캐치 후 봉투로 보고
            await asyncio.to_thread(_finish_job, job_id, "failed", None, None)
            yield _sse({"type": "job", "status": "failed", "stage": None, "job_id": job_id,
                        "error": f"{type(exc).__name__}: {exc}"})
            return

        # 최종 리포트 (critic 판단 + 누적 agent_runs = Evidence Board 소스)
        if critic is None:
            await asyncio.to_thread(_finish_job, job_id, "failed", None, None)
            yield _sse({
                "type": "job",
                "status": "failed",
                "stage": None,
                "job_id": job_id,
                "error": "Critic 결과가 생성되지 않아 분석을 중단했습니다.",
            })
            return

        decision = critic["decision"]
        summary = critic["summary"]

        # 잡 종료 기록 (status=done, decision 저장)
        await asyncio.to_thread(_finish_job, job_id, "done", decision, summary)

        yield _sse({
            "type": "report",
            "job_id": job_id,
            "decision": decision,
            "summary": summary,
            "confidence": (critic or {}).get("confidence"),
            "objections": (critic or {}).get("objections", []),
            "next_experiments": (critic or {}).get("next_experiments", []),
            "agent_runs": agent_runs,
            "evidence_sources": evidence_sources,
        })
        yield _sse({"type": "job", "status": "done", "stage": None, "job_id": job_id})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
