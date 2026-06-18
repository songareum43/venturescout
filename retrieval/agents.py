"""
Track B 소유 에이전트 — ② Market & Customer(Tier0 full), ③ Competitor & Substitute(Tier0 light).

retrieval.tools.retrieve()로 evidence_pool용 EvidenceItem을 받아 LLM 분석 후
shared.contracts.AgentFinding으로 반환 (agents/graph.py의 market_node/competitor_node가 호출).
"""
from __future__ import annotations

import json
import logging

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import SystemMessage, HumanMessage

from config import config
from shared.contracts import EvidenceItem
from shared.state import AgentFinding, VentureScoutState
from retrieval.tools import retrieve

logger = logging.getLogger(__name__)

_llm: ChatBedrockConverse | None = None


def _get_llm() -> ChatBedrockConverse:
    """ChatBedrockConverse는 생성 시 AWS 자격증명을 즉시 검증하므로 lazy하게 초기화."""
    global _llm
    if _llm is None:
        _llm = ChatBedrockConverse(
            model_id=config.bedrock_model_id,
            region_name=config.bedrock_region,
        )
    return _llm


def _pick_hypothesis_id(state: VentureScoutState, axes: set[str]) -> str:
    """hypotheses 중 axis가 axes에 속하는 첫 항목의 id. 없으면 'H0'(척추 mock과 동일)."""
    for h in state.get("hypotheses", []):
        axis = h.axis if hasattr(h, "axis") else h.get("axis")
        if axis in axes:
            return h.hypothesis_id if hasattr(h, "hypothesis_id") else h["hypothesis_id"]
    return "H0"


def _idea_query(state: VentureScoutState) -> str:
    idea = state.get("idea", {}) or {}
    return idea.get("solution_summary") or idea.get("raw_input", "")


def _evidence_context(items: list[EvidenceItem], text_limit: int = 300) -> str:
    return "\n".join(
        f"[{e.evidence_id}] (source={e.source_type}, rel={e.reliability_score:.1f}) "
        f"{e.evidence_text[:text_limit]}"
        for e in items
    )


# ── ② Market & Customer (Tier0 full) ─────────────────────────────────────────

_MARKET_SYSTEM = """당신은 창업 아이디어의 시장·고객 가설을 분석하는 에이전트입니다.

[필수 규칙]
1. 모든 주장에 evidence_id를 반드시 인용하세요. evidence_id는 아래 "검색된 근거"의
   각 항목 맨 앞 [대괄호] 안에 있는 값을 그대로 복사해서 사용하세요.
   새로운 ID를 만들어내지 마세요. 근거 없는 주장은 Critic이 제거합니다.
2. 불만(pain) ≠ 지불 의향(WTP). 절대 혼동 금지.
3. "시장 규모 X억" 같은 출처 없는 수치 금지.
4. confidence는 근거 품질에 따라 정직하게 설정하세요.
5. JSON 외 어떤 텍스트도 출력하지 마세요.

[출력 형식]
{
  "pain_signal": {
    "summary": "고객 문제 1~2줄 요약",
    "evidence_ids": ["<검색된 근거의 evidence_id 그대로>"],
    "confidence": "High|Medium|Low"
  },
  "demand_signal": {
    "summary": "수요 신호 요약",
    "evidence_ids": ["<검색된 근거의 evidence_id 그대로>"],
    "confidence": "High|Medium|Low"
  },
  "willingness_to_pay": {
    "summary": "지불 의향 요약. 근거 없으면 '추가 검증 필요'로 명시",
    "evidence_ids": [],
    "confidence": "High|Medium|Low"
  },
  "next_validation_action": "다음 검증 액션 (인터뷰, 가격 테스트 등)"
}"""

# market_context 내 confidence 표기("High"/"Medium"/"Low") -> 계약 Confidence("high"/"mid"/"low")
_CONF_MAP = {"high": "high", "medium": "mid", "low": "low"}
_CONF_LEVEL = {"high": 3, "mid": 2, "low": 1}


def _overall_confidence(result: dict) -> str:
    """pain/demand/wtp 중 가장 낮은 confidence를 최종 confidence로 사용 (보수적 집계)."""
    confidences = [
        _CONF_MAP.get(str(result.get(key, {}).get("confidence", "low")).lower(), "low")
        for key in ("pain_signal", "demand_signal", "willingness_to_pay")
    ]
    return min(confidences, key=lambda c: _CONF_LEVEL[c])


def _grounded_on(result: dict, evidence_items: list[EvidenceItem], keys: list[str]) -> list[str]:
    """결과의 evidence_ids 합집합. LLM이 인용 안 했으면 검색된 evidence 전체로 폴백."""
    cited: set[str] = set()
    for key in keys:
        cited.update(result.get(key, {}).get("evidence_ids", []) or [])
    if cited:
        return sorted(cited)
    return [e.evidence_id for e in evidence_items]


def run_market_agent(state: VentureScoutState) -> AgentFinding:
    """② Market & Customer 노드 본체. agents/graph.py의 market_node가 호출."""
    hypothesis_id = _pick_hypothesis_id(state, axes={"고객문제", "수익"})
    query = _idea_query(state)

    evidence_items = retrieve(hypothesis_id, query, k=10)

    if not evidence_items:
        logger.warning("[② Market] 근거 없음 — 시드 데이터 적재 확인 필요")
        result = {
            "pain_signal":          {"summary": "근거 없음", "evidence_ids": [], "confidence": "Low"},
            "demand_signal":        {"summary": "근거 없음", "evidence_ids": [], "confidence": "Low"},
            "willingness_to_pay":   {"summary": "추가 검증 필요", "evidence_ids": [], "confidence": "Low"},
            "next_validation_action": "시드 데이터 적재 후 재실행",
        }
        return AgentFinding(
            agent="market", hypothesis_id=hypothesis_id,
            signal=result["pain_signal"]["summary"],
            grounded_on=[], confidence="low", depth="full",
            next_experiment=result["next_validation_action"],
            payload=result,
        )

    messages = [
        SystemMessage(content=_MARKET_SYSTEM),
        HumanMessage(content=f"아이디어: {query}\n\n검색된 근거:\n{_evidence_context(evidence_items)}"),
    ]

    response = _get_llm().invoke(messages)
    raw = response.content.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"[② Market] JSON 파싱 실패: {raw[:200]}")
        result = {
            "pain_signal":          {"summary": raw[:200], "evidence_ids": [], "confidence": "Low"},
            "demand_signal":        {"summary": "파싱 실패", "evidence_ids": [], "confidence": "Low"},
            "willingness_to_pay":   {"summary": "파싱 실패", "evidence_ids": [], "confidence": "Low"},
            "next_validation_action": "재실행 필요",
        }

    grounded_on = _grounded_on(result, evidence_items, ["pain_signal", "demand_signal", "willingness_to_pay"])

    return AgentFinding(
        agent="market", hypothesis_id=hypothesis_id,
        signal=result.get("demand_signal", {}).get("summary", result.get("pain_signal", {}).get("summary", "")),
        grounded_on=grounded_on,
        confidence=_overall_confidence(result),
        depth="full",
        next_experiment=result.get("next_validation_action"),
        payload=result,
    )


# ── ③ Competitor & Substitute (Tier0 light) ──────────────────────────────────

_COMPETITOR_SYSTEM = """당신은 경쟁사·대체재를 분석하는 에이전트입니다 (경량 구현).

[경량의 의미]
- seed 3건 매칭으로 갭 신호 도출. 상세 분석은 Tier 1에서 합니다.
- evidence_id 인용 필수. evidence_id는 아래 "경쟁사 근거"의 각 항목 맨 앞 [대괄호] 안에
  있는 값을 그대로 복사해서 사용하세요. 새로운 ID를 만들어내지 마세요.
- confidence는 항상 "Low"로 설정 (seed 한정 근거임을 정직하게 표시).
- "경쟁사 없음"은 절대 금지. 대체재(indirect substitute)를 반드시 포함.

[출력 형식]
{
  "competitor_matrix": [
    {
      "name": "경쟁사 또는 대체재명",
      "type": "direct|indirect",
      "gap_signal": "차별화 가능 갭 설명",
      "evidence_id": "<경쟁사 근거의 evidence_id 그대로>",
      "threat_level": "High|Medium|Low"
    }
  ],
  "substitute_map": ["대체재1", "대체재2"],
  "differentiation_gap": "전반적인 차별화 기회 요약",
  "confidence": "Low",
  "next_experiment": "vertical 밀도 조사 등 후속 액션"
}

JSON 외 출력 금지."""


def run_competitor_agent(state: VentureScoutState) -> AgentFinding:
    """③ Competitor & Substitute 노드 본체 (경량). agents/graph.py의 competitor_node가 호출."""
    hypothesis_id = _pick_hypothesis_id(state, axes={"경쟁"})
    query = f"competitor alternative substitute {_idea_query(state)}"

    evidence_items = retrieve(hypothesis_id, query, k=6)
    top3 = evidence_items[:3]

    if not top3:
        logger.warning("[③ Competitor] 시드 경쟁사 데이터 없음")
        result = {
            "competitor_matrix":    [],
            "substitute_map":       ["직접 조사 필요"],
            "differentiation_gap":  "근거 없음",
            "confidence":           "Low",
            "next_experiment":      "경쟁사 시드 데이터 추가 후 재실행",
        }
        return AgentFinding(
            agent="competitor", hypothesis_id=hypothesis_id,
            signal=result["differentiation_gap"],
            grounded_on=[], confidence="low", depth="light",
            next_experiment=result["next_experiment"],
            payload=result,
        )

    messages = [
        SystemMessage(content=_COMPETITOR_SYSTEM),
        HumanMessage(content=f"아이디어: {_idea_query(state)}\n\n경쟁사 근거 (3건):\n{_evidence_context(top3, text_limit=200)}"),
    ]

    response = _get_llm().invoke(messages)
    raw = response.content.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"[③ Competitor] JSON 파싱 실패: {raw[:200]}")
        result = {
            "competitor_matrix":   [],
            "substitute_map":      [],
            "differentiation_gap": "파싱 실패",
            "confidence":          "Low",
            "next_experiment":     "재실행 필요",
        }

    cited = {c.get("evidence_id") for c in result.get("competitor_matrix", []) if c.get("evidence_id")}
    grounded_on = sorted(cited) if cited else [e.evidence_id for e in top3]

    return AgentFinding(
        agent="competitor", hypothesis_id=hypothesis_id,
        signal=result.get("differentiation_gap", ""),
        grounded_on=grounded_on,
        confidence="low",
        depth="light",
        next_experiment=result.get("next_experiment"),
        payload=result,
    )
