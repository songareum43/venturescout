"""
Track D — Chainlit Evidence Board (얇은 클라이언트).
FastAPI /analyze SSE를 구독 → 에이전트 단계를 cl.Step으로 렌더 → 결과를 Board로.

★ ADR-006 — Chainlit-first + 얇은 클라이언트 원칙(로직은 FastAPI, 프론트는 호출+렌더).
              D3 게이트: 이 스트리밍이 안정 동작하면 ADR-006 accepted 확정,
              막히면 stream_events() 그대로 두고 뷰 레이어만 Streamlit으로 교체.
★ ADR-007 §3 — SSE 봉투(job/stage/report)를 그대로 소비. 봉투 포맷은 D(api.py)가 소유.

실행: chainlit run app/ui.py --port 8001   (api는 :8000 가동 전제)
"""
from __future__ import annotations
import json
import os
from typing import AsyncIterator

import httpx

API_URL = os.getenv("API_URL", "http://localhost:8000")

# 결정 → 표시 (Evidence Board 헤더)
DECISION_BADGE = {
    "go": "🟢 **GO** — 진행 권고",
    "pivot": "🟡 **PIVOT** — 방향 전환 권고",
    "kill": "🔴 **KILL** — 중단 권고",
    "more_research": "🔵 **MORE RESEARCH** — 추가 검증 필요",
}
CONFIDENCE_KO = {"high": "높음", "mid": "중간", "low": "낮음"}
STANCE_MARK = {"supports": "찬", "contradicts": "반", "neutral": "중립"}


async def stream_events(idea: str) -> AsyncIterator[dict]:
    """/analyze SSE를 구독해 파싱된 이벤트 dict를 순서대로 yield.

    ※ Chainlit 비의존 순수 함수 → 단위 테스트/Streamlit 폴백에서 재사용.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        async with client.stream(
            "POST", f"{API_URL}/analyze", json={"idea": idea}
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[len("data:"):].strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    continue


def _extract_signal(output: dict) -> str:
    """신호 칸 텍스트 — output_json(loose)에서 방어적으로 뽑는다.

    D 노드는 'signal'을 쓰지만 C(ko-agent) 노드는 'summary'/'key_findings'를 쓴다.
    키가 트랙마다 달라도 보드가 비지 않게 signal → summary → key_findings[0] 순 폴백.
    """
    text = output.get("signal") or output.get("summary")
    if not text:
        kf = output.get("key_findings")
        if isinstance(kf, list) and kf:
            text = str(kf[0])
    text = str(text or "")
    if len(text) > 80:
        text = text[:80] + "…"
    return text.replace("|", "\\|").replace("\n", " ")


def _render_board(report: dict) -> str:
    """report 이벤트 → Evidence Board 마크다운."""
    decision = report.get("decision", "more_research")
    lines = [
        "## 🗂 Evidence Board",
        "",
        DECISION_BADGE.get(decision, decision),
        "",
        f"> {report.get('summary', '')}",
        "",
    ]

    # C 계약: report.agent_runs = list[AgentRun dict]. signal/next_experiment는
    # strict 필드가 아니라 output_json(loose) 안에 있으므로 거기서 방어적으로 읽는다.
    runs = report.get("agent_runs", [])
    if runs:
        lines.append("### 가설별 근거")
        lines.append("")
        lines.append("| 에이전트 | 깊이 | 신뢰도 | 신호 | 근거 |")
        lines.append("|---|---|---|---|---|")
        for r in runs:
            agent = r.get("agent_name", "?")
            depth = r.get("depth", "")
            conf = CONFIDENCE_KO.get(r.get("confidence", ""), r.get("confidence", ""))
            output = r.get("output_json") or {}
            signal = _extract_signal(output)
            grounded = ", ".join(r.get("grounded_on", [])) or "—"
            lines.append(f"| {agent} | {depth} | {conf} | {signal} | {grounded} |")
        lines.append("")

    objs = report.get("objections", [])
    if objs:
        lines.append("### ⑦ Critic 반론")
        for o in objs:
            txt = o.get("text") if isinstance(o, dict) else str(o)
            lines.append(f"- {txt}")
        lines.append("")

    exps = report.get("next_experiments", [])
    if exps:
        lines.append("### 다음 실험")
        for e in exps:
            lines.append(f"- {e}")
        lines.append("")

    return "\n".join(lines)


# ── Chainlit 핸들러 (chainlit 미설치 환경에서도 위 함수는 import 가능하도록 가드) ──
try:
    import chainlit as cl

    @cl.on_chat_start
    async def on_start() -> None:
        await cl.Message(
            content=(
                "**VentureScout** — 창업 아이디어를 입력하면 멀티 에이전트가 "
                "가설로 분해하고 근거 기반으로 Go/Pivot/Kill/More Research를 판정합니다.\n\n"
                "예) `AI 기반 이커머스 개인화 추천 엔진`"
            )
        ).send()

    @cl.on_message
    async def on_message(msg: cl.Message) -> None:
        idea = msg.content.strip()
        if not idea:
            await cl.Message(content="아이디어를 한 줄로 입력해 주세요.").send()
            return

        steps: dict[str, "cl.Step"] = {}
        report: dict | None = None

        try:
            async for ev in stream_events(idea):
                etype = ev.get("type")

                if etype == "stage":
                    name = ev["stage"]
                    if ev.get("status") == "running":
                        step = cl.Step(name=ev.get("label", name), type="run")
                        await step.send()
                        steps[name] = step
                    elif ev.get("status") == "done":
                        step = steps.get(name)
                        if step is not None:
                            step.output = "완료"
                            await step.update()

                elif etype == "report":
                    report = ev

                elif etype == "job" and ev.get("status") == "failed":
                    await cl.Message(
                        content=f"⚠️ 분석 실패: {ev.get('error', 'unknown')}"
                    ).send()
                    return

        except httpx.HTTPError as exc:
            await cl.Message(
                content=(
                    f"⚠️ API 연결 실패 ({API_URL}). FastAPI가 떠 있는지 확인해 주세요.\n\n"
                    f"`{type(exc).__name__}: {exc}`"
                )
            ).send()
            return

        if report is not None:
            await cl.Message(content=_render_board(report)).send()
        else:
            await cl.Message(content="리포트를 받지 못했습니다.").send()

except ImportError:
    # chainlit 미설치(테스트/폴백) — stream_events·_render_board만 사용
    cl = None