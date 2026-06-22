"""Tier 0 스키마 계약에 맞춘 live Track C LangGraph.

이 파일은 현재 VentureScout 에이전트 실행의 중심이다.
실제 에이전트 순서와 판단 로직은 여기서 정의된다.

전체 흐름:
1. structuring_node가 raw_input을 ideas + H1~H5 hypotheses로 구조화한다.
2. Market/Competitor/BM/Tech/IP 노드가 각자 담당 가설의 evidence를 조회한다.
3. 각 노드는 AgentRun 공통 envelope로 결과를 남긴다.
4. critic_node가 모든 AgentRun과 evidence를 검수해 최종 decision을 만든다.

조정 가능 지점:
- confidence threshold: _confidence_from_strength()
- IP risk threshold: ip_node(), critic_node()의 hybrid_score 기준
- Critic 최종 판정 규칙: critic_node()의 if/elif decision rule
- 실제 데이터 연결부: retrieval.tools.retrieve()
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from agents.llm import (
    current_model_name,
    invoke_claude_json,
    model_tier_for_agent,
)
from agents.input_validation import InsufficientInputError, validate_input_detail
from agents.logger import (
    get_logger,
    log_completion,
    log_grounding,
    log_input,
    log_output,
    log_processing,
    log_stage,
)
from retrieval.tools import retrieve, vector_search
from shared.contracts import (
    AgentName,
    AgentRun,
    AnalysisJob,
    Confidence,
    CriticResult,
    Decision,
    Depth,
    EvidenceItem,
    Hypothesis,
    IdeaRecord,
    IPOverlapCandidate,
)
from shared.state import VentureScoutState

logger = get_logger("graph")


def _evidence_map(items: list[EvidenceItem]) -> dict[str, EvidenceItem]:
    # LangGraph state에서는 여러 노드가 evidence_items를 합친다.
    # list보다 dict[evidence_id] 형태가 중복 제거와 grounded_on 검증에 편하다.
    return {item.evidence_id: item for item in items}


def _stance_counts(evidence: list[EvidenceItem]) -> dict[str, int]:
    # evidence가 가설을 지지하는지, 반박하는지, 중립인지 세서
    # Tech/IP/Critic이 같은 방식으로 리스크 신호를 읽게 한다.
    return {
        "supports": sum(item.stance == "supports" for item in evidence),
        "contradicts": sum(item.stance == "contradicts" for item in evidence),
        "neutral": sum(item.stance == "neutral" for item in evidence),
    }


def _evidence_strength(evidence: list[EvidenceItem]) -> float:
    if not evidence:
        return 0.0
    # relevance_score: 이 가설/질문과 얼마나 관련 있는가.
    # reliability_score: 출처가 얼마나 믿을 만한가.
    # 둘을 곱해 평균내면 "관련도는 높지만 출처가 약함" 또는 "출처는 강하지만 덜 관련됨"을 완화할 수 있다.
    # 조정 가능 지점:
    # 나중에는 source_type별 가중치, 최신성(freshness_score), 근거 개수를 함께 반영할 수 있다.
    scores = [
        item.relevance_score * item.reliability_score
        for item in evidence
    ]
    return round(sum(scores) / len(scores), 3)


# strength → confidence 임계값. 0~1 연속값을 high/mid/low 이산 범주로 가른다.
CONF_HIGH_THRESHOLD = 0.60
CONF_MID_THRESHOLD = 0.40
# mid 경계(0.40) ±δ는 "판단 보류" 데드밴드 — 이 구간 strength는 검색 쿼리의 미세한
# 변동만으로 confidence(low↔mid)가 뒤집혀 판정을 흔드는 불안정 영역이다.
BORDERLINE_DELTA = 0.05
# borderline agent가 이 수 이상이면 판정이 불안정하다고 보고 more_research로 돌린다.
BORDERLINE_MAJORITY = 2


def _confidence_from_strength(strength: float) -> Confidence:
    # evidence_strength(= relevance × reliability 평균)를 high/mid/low로 변환한다.
    #
    # 임계값 재보정 (2026-06-22): high 0.75→0.60, mid 0.45→0.40.
    # 근거 — 기존 0.75/0.45는 strength가 0~1 전구간에 퍼진다고 가정했으나,
    # 실제 분포는 출처 reliability에 막혀 훨씬 낮은 구간에 몰려 있다:
    #   * 시드 출처(seed_review/competitor/pricing) reliability=0.6 → strength ≤ 0.6×hybrid,
    #     hybrid가 잘 나와도 ~0.78이라 시드 strength는 사실상 0.47을 못 넘음.
    #   * 즉 0.75(high)는 시드로는 도달 불가, 0.45(mid)도 거의 항상 미달 →
    #     market/competitor/bm이 구조적으로 늘 low → _decide 규칙3(low≥3)으로
    #     입력 무관 KILL이 기본값이 됨(검증: 실 RDS+Bedrock E2E 3건 전부 KILL).
    # 실측 strength 분포(실 structuring LLM 쿼리 기준):
    #   off-domain(축산): market 0.16 / competitor 0.17 / bm 0.33  (전부 low)
    #   on-domain(SaaS):  market 0.20 / competitor 0.42 / tech 0.45 / bm 0.45
    # mid=0.40은 on-domain 근거(0.42~0.45)를 mid로, off-domain(0.16~0.33)을 low로
    # 가르는 자연 경계 → GO/PIVOT/KILL 네 판정이 모두 도달 가능해짐
    # (실측: 축산→KILL, 결제→PIVOT, SaaS→GO로 분리). high=0.60은 reliability 0.9인
    # 특허 근거(tech/ip)가 강한 매칭일 때 high에 도달할 수 있게 함.
    # ※ 근본 원인은 reliability 가중(시드 0.6)과 hybrid 상한이므로, 추후 시드
    #   reliability 상향 또는 source_type별 정규화로 대체할 수 있음(ADR 예정).
    if strength >= CONF_HIGH_THRESHOLD:
        return "high"
    if strength >= CONF_MID_THRESHOLD:
        return "mid"
    return "low"


def _is_borderline_strength(strength: float) -> bool:
    """strength가 low/mid 경계(±δ)에 걸쳐 있으면 True — 작은 변동이 판정을 뒤집는 불안정 구간."""
    return CONF_MID_THRESHOLD - BORDERLINE_DELTA <= strength < CONF_MID_THRESHOLD + BORDERLINE_DELTA


_CONFIDENCE_ALIASES: dict[str, str] = {
    "high": "high", "h": "high",
    "medium": "mid", "moderate": "mid", "med": "mid", "mid": "mid", "m": "mid",
    "low": "low", "l": "low",
}


def _normalize_confidence(value: Any, default: Confidence = "low") -> Confidence:
    return _CONFIDENCE_ALIASES.get(str(value).strip().lower(), default)  # type: ignore[return-value]


def _to_str_list(value: Any) -> list[str]:
    """LLM이 list[str] 대신 list[dict]를 반환할 때 문자열 리스트로 정규화한다."""
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            result.append(" ".join(str(v) for v in item.values() if v))
        else:
            result.append(str(item))
    return result


def _validate_structured_idea(idea: IdeaRecord, hypotheses: list[Hypothesis]) -> dict[str, Any]:
    # Structuring 결과가 뒤쪽 agent들이 분석할 만큼 충분한지 확인한다.
    # 여기서 실패하면 downstream agent가 빈 고객/문제/기술요소를 보고 엉뚱한 분석을 할 수 있다.
    # 조정 가능 지점:
    # MVP에서는 business_model_hint까지 필수로 보지만, 초기 아이디어 수집 단계에서는 선택값으로 완화할 수 있다.
    required_fields = [
        "title",
        "target_customer",
        "problem_statement",
        "solution_summary",
        "business_model_hint",
    ]
    missing_fields = [
        field
        for field in required_fields
        if not getattr(idea, field)
    ]
    hypothesis_axes = {hypothesis.axis for hypothesis in hypotheses}
    # H1~H5 전체 축이 있어야 Evidence Board가 가설별로 비어 있지 않다.
    # 조정 가능 지점:
    # Track C만 단독 실행하는 모드라면 technology/ip만 요구하도록 줄일 수도 있다.
    expected_axes = {
        "customer_problem",
        "competition",
        "business_model",
        "technology",
        "ip",
    }
    missing_axes = sorted(expected_axes - hypothesis_axes)

    return {
        "missing_fields": missing_fields,
        "missing_axes": missing_axes,
        "technical_element_count": len(idea.technical_elements),
        "patent_keyword_count": len(idea.patent_keywords),
        "ready_for_analysis": not missing_fields and not missing_axes,
    }


def _hypothesis_query(
    state: VentureScoutState,
    code: str,
) -> str:
    """Use the current run's structured hypothesis as the retrieval query."""

    for hypothesis in state.get("hypotheses", []):
        if hypothesis.code == code:
            return hypothesis.statement
    raise RuntimeError(f"Structured hypothesis {code} is missing.")


def _json_context(payload: dict[str, Any]) -> str:
    """Claude 프롬프트에 넣기 좋게 Pydantic 객체를 JSON 문자열로 바꾼다."""

    return json.dumps(payload, ensure_ascii=False, default=str, indent=2)


def _agent_output_with_llm(
    *,
    agent_name: AgentName,
    hypothesis_id: str,
    role: str,
    required_fields: list[str],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Generate live analysis output and reject incomplete model responses."""

    # 중요한 설계 포인트:
    # LLM에게 "결정권"을 전부 주지 않고, evidence/context 안에서 output_json만 보강하게 한다.
    # grounded_on, confidence, decision 같은 핵심 계약은 코드에서 계산하거나 검증한다.
    # 이렇게 해야 모델이 과장하더라도 Critic과 AgentRun 계약으로 추적할 수 있다.
    system = (
        "너는 VentureScout의 근거 기반 스타트업 검증 에이전트다. "
        "반드시 제공된 evidence_id 안에서만 주장하고, 법률/투자 확정 판단처럼 "
        "근거를 넘어서는 표현은 피한다. "
        "모든 문자열 값(summary·signal·key_findings·objections·next_experiments 등)은 "
        "반드시 한국어로 작성한다(영어 혼용 금지). "
        "응답은 설명 없이 JSON object 하나만 반환한다."
    )
    user = (
        f"에이전트: {agent_name}\n"
        f"담당 가설: {hypothesis_id}\n"
        f"역할: {role}\n\n"
        f"반드시 다음 키를 모두 포함하라: {required_fields}. "
        "아래 실제 context에 있는 정보만 사용하고 새 evidence_id를 만들지 마라. "
        "근거가 부족하면 그 사실을 명시하되 임의의 사업·시장·기술 내용을 만들지 마라.\n\n"
        f"CONTEXT:\n{_json_context(context)}"
    )
    output = invoke_claude_json(
        system=system,
        user=user,
        model_tier=model_tier_for_agent(agent_name),
    )
    missing = [field for field in required_fields if field not in output]
    if missing:
        raise RuntimeError(
            f"{agent_name} response is missing required fields: {missing}"
        )
    return output


def _structured_idea_payload(
    *,
    job_id: str,
    idea_id: str,
    raw_input: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """raw_input을 ideas + hypotheses 형태로 구조화한다."""

    system = (
        "너는 VentureScout의 Structuring 에이전트다. 사용자의 사업계획/파일 추출 텍스트를 "
        "분석 가능한 ideas와 H1~H5 hypotheses로 구조화한다. "
        "사용자가 명시하지 않은 고객, 문제, 해결책, 수익모델을 추측해서 채우지 마라. "
        "응답은 설명 없이 JSON object 하나만 반환한다."
    )
    user = (
        "다음 raw_input을 구조화해라.\n\n"
        "반환 JSON 형식:\n"
        "{\n"
        '  "input_sufficient": true,\n'
        '  "missing_details": [],\n'
        '  "idea": {\n'
        '    "title": "...", "idea_type": "...", "target_customer": "...",\n'
        '    "problem_statement": "...", "solution_summary": "...",\n'
        '    "business_model_hint": "...",\n'
        '    "technical_elements": ["..."],\n'
        '    "patent_keywords": ["..."]\n'
        "  },\n"
        '  "hypotheses": [\n'
        '    {"hypothesis_id":"H1","code":"H1","axis":"customer_problem","statement":"<ENGLISH: one-sentence testable hypothesis>","confidence":"low","next_validation":"..."},\n'
        '    {"hypothesis_id":"H2","code":"H2","axis":"competition","statement":"<ENGLISH: one-sentence testable hypothesis>","confidence":"low","next_validation":"..."},\n'
        '    {"hypothesis_id":"H3","code":"H3","axis":"business_model","statement":"<ENGLISH: one-sentence testable hypothesis>","confidence":"low","next_validation":"..."},\n'
        '    {"hypothesis_id":"H4","code":"H4","axis":"technology","statement":"<ENGLISH: one-sentence testable hypothesis>","confidence":"low","next_validation":"..."},\n'
        '    {"hypothesis_id":"H5","code":"H5","axis":"ip","statement":"<ENGLISH: one-sentence testable hypothesis>","confidence":"low","next_validation":"..."}\n'
        "  ]\n"
        "중요: hypotheses[].statement 는 반드시 영어로 작성한다. 이 값이 영문 문서 검색 쿼리로 사용된다.\n"
        "중요: idea.patent_keywords 도 반드시 영어로 작성한다(2~5개). 영문 특허 임베딩"
        "(PatentSBERTa)에 IP 후보 검색 쿼리로 사용된다. technical_elements 는 사람이 읽는 "
        "한국어 기술 분해로 그대로 둔다.\n"
        "}\n\n"
        f"job_id={job_id}\nidea_id={idea_id}\nraw_input:\n{raw_input}"
    )
    # 구조화는 결정성이 중요하다 — 여기서 만든 가설 문장/patent_keywords가 그대로 검색
    # 쿼리가 되므로, temp 0으로 호출 간 쿼리 변동(→ strength·판정 흔들림)을 최소화한다.
    parsed = invoke_claude_json(
        system=system,
        user=user,
        model_tier="sonnet",
        temperature=0.0,
    )
    if parsed.get("input_sufficient") is not True:
        missing = parsed.get("missing_details")
        if not isinstance(missing, list) or not missing:
            missing = ["대상 고객", "해결하려는 문제", "제공할 제품 또는 서비스"]
        raise InsufficientInputError([str(item) for item in missing])

    idea_from_model = parsed.get("idea")
    hypotheses_payload = parsed.get("hypotheses")
    if not isinstance(idea_from_model, dict):
        raise RuntimeError("Structuring response is missing the idea object.")
    if not isinstance(hypotheses_payload, list):
        raise RuntimeError("Structuring response is missing the hypotheses list.")

    idea_payload = {
        **idea_from_model,
        "idea_id": idea_id,
        "raw_input": raw_input,
    }
    return idea_payload, hypotheses_payload


def _agent_run(
    *,
    job_id: str,
    agent_name: AgentName,
    hypothesis_id: str,
    depth: Depth,
    confidence: Confidence,
    evidence: list[EvidenceItem],
    output_json: dict[str, Any],
) -> AgentRun:
    """AgentRun Pydantic 객체를 만들고, DB가 설정된 환경이면 즉시 적재한다.

    인메모리 state 반환과 DB 적재를 여기서 함께 처리하는 이유:
    - 모든 노드(market/competitor/tech/ip/bm/critic)가 이 함수를 공유하므로
      각 노드에 개별적으로 적재 코드를 넣을 필요가 없다.
    - 적재 실패 시에도 AgentRun을 반환하므로 그래프 흐름이 끊기지 않는다.
    """
    # critic이 borderline(판정 경계) 여부를 판단할 수 있게 evidence strength를 실어 보낸다.
    output_json = {**output_json, "_evidence_strength": _evidence_strength(evidence)}
    run = AgentRun(
        agent_run_id=str(uuid.uuid4()),
        job_id=job_id,
        hypothesis_id=hypothesis_id,
        agent_name=agent_name,
        model_name=current_model_name(agent_name),
        depth=depth,
        confidence=confidence,
        grounded_on=[item.evidence_id for item in evidence],
        output_json=output_json,
        groundedness_score=1.0 if evidence else 0.0,
        overclaim_flag=False,
        status="done",
    )

    from pipeline.persistence import try_persist_agent_run  # 순환 import 방지용 지연 import
    db_run_id = try_persist_agent_run(run, evidence)
    if db_run_id:
        # DB가 발급한 실제 UUID로 agent_run_id를 교체한다.
        # 이후 state에서 참조할 때 항상 실제 DB 행의 ID를 가리키게 된다.
        run = run.model_copy(update={"agent_run_id": db_run_id})

    return run


def structuring_node(state: VentureScoutState) -> dict:
    """raw_input에서 ideas, analysis_jobs, hypotheses 행을 만든다."""

    # Structuring은 전체 그래프의 출발점이다.
    # 여기서 만든 idea와 hypotheses가 뒤쪽 모든 agent의 공통 입력이 된다.
    start_time = time.time()
    log_stage(logger, "1️⃣", "Structuring (구조화)")

    try:
        job_id = state["job_id"]
        idea_id = state["idea_id"]
        raw_input = state["raw_input"]
    except KeyError as exc:
        raise RuntimeError(f"Required graph input is missing: {exc.args[0]}") from exc

    validate_input_detail(raw_input)
    # 실제 데이터 전환 지점:
    # job_id/idea_id는 analysis_jobs, ideas insert 결과에서 온다.
    # raw_input은 업로드 파일 파싱 결과 또는 API request body에서 온다.

    log_input(logger, {
        "job_id": job_id,
        "idea_id": idea_id,
        "raw_input": raw_input,
    })

    log_processing(logger, "구조화된 아이디어 payload 생성 중...")
    idea_payload, hypotheses_payload = _structured_idea_payload(
        job_id=job_id,
        idea_id=idea_id,
        raw_input=raw_input,
    )

    log_processing(logger, "IdeaRecord 객체 생성", {
        "title": idea_payload.get("title"),
        "idea_type": idea_payload.get("idea_type"),
        "technical_elements": len(idea_payload.get("technical_elements", [])),
    })

    idea = IdeaRecord(**idea_payload)

    log_processing(logger, "H1~H5 hypotheses 생성", {
        "count": len(hypotheses_payload),
    })
    hypotheses = [
        Hypothesis(
            job_id=job_id,
            idea_id=idea_id,
            **{**item, "confidence": _normalize_confidence(item.get("confidence"))},
        )
        for item in hypotheses_payload
    ]

    # 실제 데이터 전환 지점: hypotheses를 DB에 저장해야 뒤쪽 노드의 evidence/agent_run
    # 적재 시 코드('H1')를 hypotheses.hypothesis_id(uuid)로 변환할 수 있다.
    # mock/미설정 환경(job_id가 uuid 아님)에서는 내부에서 조용히 건너뛴다.
    from pipeline.persistence import persist_hypotheses  # 순환 import 방지용 지연 import
    persist_hypotheses(job_id, idea_id, hypotheses)

    log_processing(logger, "구조화 품질 검증 중...")
    structuring_quality = _validate_structured_idea(idea, hypotheses)

    if not structuring_quality["ready_for_analysis"]:
        logger.warning(f"구조화 검증 실패: {structuring_quality}")
    else:
        logger.info(f"✓ 검증 통과: {structuring_quality}")

    analysis_job = AnalysisJob(
        job_id=job_id,
        idea_id=idea_id,
        status="running" if structuring_quality["ready_for_analysis"] else "failed",
        current_stage="structuring",
        # 조정 가능 지점:
        # 지금은 structuring 완료를 20%로 본다.
        # UI 진행률을 더 정교하게 하려면 stage별 가중치를 별도 상수로 빼는 것이 좋다.
        progress_pct=20 if structuring_quality["ready_for_analysis"] else 0,
    )

    if not structuring_quality["ready_for_analysis"]:
        missing = [
            *structuring_quality["missing_fields"],
            *structuring_quality["missing_axes"],
        ]
        raise InsufficientInputError(missing)

    result = {
        "idea": idea,
        "analysis_job": analysis_job,
        "hypotheses": hypotheses,
        "documents": {},
    }

    log_output(logger, {
        "idea": idea,
        "analysis_job": analysis_job,
        "hypotheses": hypotheses,
        "documents": result["documents"],
    })

    duration_ms = (time.time() - start_time) * 1000
    log_completion(logger, "Structuring", duration_ms)

    return result


def market_node(state: VentureScoutState) -> dict:
    # Market은 H1, 즉 "고객 문제가 실제로 반복되는가"를 검증한다.
    # confidence는 retrieve한 근거 강도로 산정(Tech/IP/BM과 동일). H1 근거는 고객 리뷰 시드.
    start_time = time.time()
    log_stage(logger, "2️⃣", "Market (시장/고객 검증)")

    job_id = state["analysis_job"].job_id
    log_input(logger, {"job_id": job_id, "hypothesis": "H1"})

    query = _hypothesis_query(state, "H1")
    log_processing(logger, "H1 관련 근거 검색 중...", {"query": query})
    # H1(고객 문제/수요) 근거는 고객 리뷰 시드에서 — 특허 제외
    evidence = retrieve("H1", query, job_id=job_id, source_types=["seed_review"])
    log_processing(logger, "근거 수집 완료", {"evidence_count": len(evidence)})

    # graceful: 근거 0건이면 run을 생략(grounded_on 빈 AgentRun 금지·ADR-014).
    # 한 에이전트의 빈 근거가 잡 전체를 죽이지 않게 한다 → critic이 미검증 가설로 처리.
    if not evidence:
        log_processing(logger, "H1 근거 0건 — market run 생략(graceful)")
        return {"agent_runs": []}

    strength = _evidence_strength(evidence)
    confidence = _confidence_from_strength(strength)

    agent_run = _agent_run(
        job_id=job_id,
        agent_name="market",
        hypothesis_id="H1",
        depth="full",
        confidence=confidence,
        evidence=evidence,
        output_json=_agent_output_with_llm(
            agent_name="market",
            hypothesis_id="H1",
            role="고객 문제와 시장 수요 신호를 검토한다.",
            required_fields=[
                "summary", "signal", "key_findings", "risks",
                "recommendations", "next_experiment",
            ],
            context={
                "idea": state.get("idea"),
                "evidence": evidence,
            },
        ),
    )

    log_grounding(logger, "market", agent_run.grounded_on, agent_run.confidence)

    result = {
        "evidence_items": _evidence_map(evidence),
        "agent_runs": [agent_run],
    }

    log_output(logger, {
        "evidence_items": result["evidence_items"],
        "agent_runs": result["agent_runs"],
    })

    duration_ms = (time.time() - start_time) * 1000
    log_completion(logger, "Market", duration_ms)

    return result


def competitor_node(state: VentureScoutState) -> dict:
    # Competitor는 H2, 즉 "기존 대안 대비 차별화 여지가 있는가"를 검증한다.
    # 지금은 상세 로깅 없이 단순 노드로 남겨두었다.
    # 조정 가능 지점:
    # 나중에는 Market/Tech/IP처럼 start_time/log_input/log_output 패턴을 맞추면 추적성이 좋아진다.
    job_id = state["analysis_job"].job_id
    query = _hypothesis_query(state, "H2")
    # H2(경쟁/대안) 근거는 경쟁사 시드에서 — 특허 제외
    evidence = retrieve("H2", query, job_id=job_id, source_types=["seed_competitor"])

    if not evidence:                       # graceful: 근거 0건 → run 생략
        return {"agent_runs": []}

    strength = _evidence_strength(evidence)
    confidence = _confidence_from_strength(strength)

    return {
        "evidence_items": _evidence_map(evidence),
        "agent_runs": [
            _agent_run(
                job_id=job_id,
                agent_name="competitor",
                hypothesis_id="H2",
                depth="light",
                confidence=confidence,
                evidence=evidence,
                output_json=_agent_output_with_llm(
                    agent_name="competitor",
                    hypothesis_id="H2",
                    role="경쟁 대안과 차별화 가능성을 검토한다.",
                    required_fields=[
                        "summary", "signal", "key_findings", "risks",
                        "recommendations", "next_experiment",
                    ],
                    context={
                        "idea": state.get("idea"),
                        "evidence": evidence,
                    },
                ),
            )
        ],
    }


def tech_node(state: VentureScoutState) -> dict:
    # Tech는 H4, 즉 "현재 기술로 프로토타입 구현이 가능한가"를 light depth로 검증한다.
    # 법/시장보다 빠르게 판단할 수 있는 비용, 지연시간, 보안, API 의존성을 중심으로 본다.
    start_time = time.time()
    log_stage(logger, "4️⃣", "Tech (기술 가능성 - Light)")

    job_id = state["analysis_job"].job_id
    log_input(logger, {"job_id": job_id, "hypothesis": "H4"})

    query = _hypothesis_query(state, "H4")
    log_processing(logger, "H4 관련 근거 검색 중...", {"query": query})
    evidence = retrieve("H4", query, job_id=job_id)
    log_processing(logger, "근거 수집 완료", {"evidence_count": len(evidence)})

    if not evidence:                       # graceful: 근거 0건 → run 생략
        log_processing(logger, "H4 근거 0건 — tech run 생략(graceful)")
        return {"agent_runs": []}

    log_processing(logger, "증거 분석 중...")
    stance_counts = _stance_counts(evidence)
    strength = _evidence_strength(evidence)
    confidence = _confidence_from_strength(strength)
    log_processing(logger, "증거 분석 완료", {
        "strength": strength,
        "confidence": confidence,
        "support": stance_counts["supports"],
        "contradict": stance_counts["contradicts"],
        "neutral": stance_counts["neutral"],
    })
    # 반박 근거가 하나라도 있으면 feasibility_signal을 high로 올리지 않는다.
    # 조정 가능 지점:
    # 반박 근거 1개만으로 mid로 낮추는 것이 너무 보수적이면,
    # contradicts 비율이나 reliability_score가 높은 반박만 반영하도록 바꿀 수 있다.
    feasibility_signal = "mid" if stance_counts["contradicts"] else confidence
    supporting_ids = [
        item.evidence_id
        for item in evidence
        if item.stance == "supports"
    ]
    risk_ids = [
        item.evidence_id
        for item in evidence
        if item.stance == "contradicts"
    ]

    return {
        "evidence_items": _evidence_map(evidence),
        "agent_runs": [
            _agent_run(
                job_id=job_id,
                agent_name="tech",
                hypothesis_id="H4",
                depth="light",
                confidence=confidence,
                evidence=evidence,
                output_json=_agent_output_with_llm(
                    agent_name="tech",
                    hypothesis_id="H4",
                    role="기술 구현 가능성, 비용, 지연시간, 보안 리스크를 light depth로 검토한다.",
                    required_fields=[
                        "summary", "signal", "feasibility_signal",
                        "architecture_assumption", "required_models_or_apis",
                        "risk_register", "validation_plan", "go_no_go_metrics",
                        "recommendations", "next_experiment",
                    ],
                    context={
                        "idea": state.get("idea"),
                        "hypothesis_id": "H4",
                        "evidence": evidence,
                        "stance_counts": stance_counts,
                        "evidence_strength": strength,
                        "feasibility_signal": feasibility_signal,
                        "supporting_evidence": supporting_ids,
                        "risk_evidence": risk_ids,
                    },
                ),
            )
        ],
    }


def ip_node(state: VentureScoutState) -> dict:
    # IP는 H5, 즉 "핵심 기술요소가 기존 특허 claim limitation과 위험하게 겹치지 않는가"를 검증한다.
    # 이 노드는 법적 침해 여부를 판단하지 않는다.
    # 역할은 claim chart 수동 검토가 필요한 후보를 우선순위화하는 것이다.
    start_time = time.time()
    log_stage(logger, "5️⃣", "IP (특허 중첩 분석 - Full)")

    job_id = state["analysis_job"].job_id
    idea = state["idea"]

    log_input(logger, {"job_id": job_id, "hypothesis": "H5", "technical_elements": len(idea.technical_elements)})

    query = _hypothesis_query(state, "H5")
    log_processing(logger, "H5 관련 근거 검색 중...", {"query": query})
    evidence = retrieve("H5", query, job_id=job_id)
    log_processing(logger, "근거 수집 완료", {"evidence_count": len(evidence)})

    if not evidence:                       # graceful: 근거 0건 → run 생략
        log_processing(logger, "H5 근거 0건 — ip run 생략(graceful)")
        return {"agent_runs": []}

    # IP 후보 검색은 영문 특허 코퍼스(PatentSBERTa, 영문 전용)를 친다 → 쿼리도 영어여야
    # relevance가 산다(Fix B와 동일 이유). patent_keywords(영어)를 쓰고, 비면 technical_elements 폴백.
    ip_search_terms = idea.patent_keywords or idea.technical_elements
    log_processing(logger, "IP 특허 후보 벡터 검색 중...", {"elements": ip_search_terms})
    # 실제 데이터 전환 지점:
    # vector_search()가 실제 claim_limitations 벡터/키워드 검색을 수행하고 후보를 반환해야 한다.
    candidates = vector_search(
        ip_search_terms,
        job_id=job_id,
        hypothesis_id="H5",
    )
    log_processing(logger, "특허 후보 수집 완료", {"candidate_count": len(candidates)})

    log_processing(logger, "특허 위험도 분석 중...")
    stance_counts = _stance_counts(evidence)
    strength = _evidence_strength(evidence)
    log_processing(logger, "분석 완료", {
        "strength": strength,
        "high_watch_threshold": "0.78",
        "watch_threshold": "0.70",
    })
    candidate_rows = []
    for candidate in candidates:
        # hybrid_score는 lexical_score와 similarity_score를 합친 "검색 후보 점수"다.
        # 조정 가능 지점:
        # 현재 0.78 이상은 high_watch, 0.70 이상은 watch다.
        # 특허 리스크를 더 보수적으로 보려면 high_watch를 0.75로 낮춘다.
        # 반대로 후보가 너무 많이 잡히면 high_watch를 0.82~0.85로 올릴 수 있다.
        if candidate.hybrid_score >= 0.78:
            risk_band = "high_watch"
        elif candidate.hybrid_score >= 0.70:
            risk_band = "watch"
        else:
            risk_band = "low_watch"

        candidate_rows.append(
            {
                **candidate.model_dump(),
                "risk_band": risk_band,
                "agent_interpretation": (
                    "수동 claim chart 검토 우선순위가 높다."
                    if risk_band == "high_watch"
                    else "보조 후보로 보되 직접 중첩 단정은 금물이다."
                ),
            }
        )

    high_overlap = [
        row["plan_technical_element"]
        for row in candidate_rows
        if row["risk_band"] == "high_watch"
    ]
    # high_watch가 하나라도 있으면 overlap_signal을 mid로 둔다.
    # 조정 가능 지점:
    # high_watch 개수, 독립항 여부, evidence reliability를 함께 봐서 high까지 올리는 규칙을 추가할 수 있다.
    overlap_signal = "mid" if high_overlap else "low"
    # IP confidence는 "후보와 근거가 둘 다 있으면 mid"로 단순화했다.
    # 조정 가능 지점:
    # 실제 특허 claim 데이터가 충분해지면 candidate score, 독립항 여부, claim family 수로 confidence를 계산한다.
    confidence = "mid" if candidates and evidence else "low"

    return {
        "evidence_items": _evidence_map(evidence),
        "ip_overlap_candidates": candidates,
        "agent_runs": [
            _agent_run(
                job_id=job_id,
                agent_name="ip",
                hypothesis_id="H5",
                depth="full",
                confidence=confidence,
                evidence=evidence,
                output_json=_agent_output_with_llm(
                    agent_name="ip",
                    hypothesis_id="H5",
                    role="특허 claim limitation 중첩 후보를 full depth로 검토하되 법적 침해 판단은 하지 않는다.",
                    required_fields=[
                        "summary", "signal", "overlap_signal",
                        "high_overlap_elements", "design_around_options",
                        "claim_review_queue", "legal_guardrail_note",
                        "manual_review_questions", "next_experiment",
                    ],
                    context={
                        "idea": idea,
                        "hypothesis_id": "H5",
                        "evidence": evidence,
                        "ip_overlap_candidates": candidates,
                        "candidate_rows": candidate_rows,
                        "overlap_signal": overlap_signal,
                        "evidence_strength": strength,
                        "stance_counts": stance_counts,
                        "high_overlap_elements": high_overlap,
                    },
                ),
            )
        ],
    }


def bm_node(state: VentureScoutState) -> dict:
    # BM은 H3, 즉 "좌석 단위 SaaS 구독 모델이 성립할 수 있는가"를 검증한다.
    # 승격(D, B 게이트 통과): confidence를 하드코딩 low → evidence_strength 기반으로 산정
    # (Tech/IP와 동일 방식). BM 근거는 특허가 아니라 가격/리뷰/경쟁 시드 문서이므로
    # source_types로 검색 범위를 좁힌다.
    start_time = time.time()
    log_stage(logger, "6️⃣", "BM (수익모델·가격 검증 - Light)")

    job_id = state["analysis_job"].job_id
    log_input(logger, {"job_id": job_id, "hypothesis": "H3"})

    query = _hypothesis_query(state, "H3")
    # BM 관련 근거만 — seed_pricing/seed_review/seed_competitor (특허 제외)
    evidence = retrieve(
        "H3", query, job_id=job_id,
        source_types=["seed_pricing", "seed_review", "seed_competitor"],
    )
    log_processing(logger, "근거 수집 완료", {"evidence_count": len(evidence)})

    if not evidence:                       # graceful: 근거 0건 → run 생략
        log_processing(logger, "H3 근거 0건 — bm run 생략(graceful)")
        return {"agent_runs": []}

    stance_counts = _stance_counts(evidence)
    strength = _evidence_strength(evidence)
    confidence = _confidence_from_strength(strength)
    log_processing(logger, "증거 분석 완료", {
        "strength": strength,
        "confidence": confidence,
        "support": stance_counts["supports"],
        "contradict": stance_counts["contradicts"],
        "neutral": stance_counts["neutral"],
    })

    return {
        "evidence_items": _evidence_map(evidence),
        "agent_runs": [
            _agent_run(
                job_id=job_id,
                agent_name="bm",
                hypothesis_id="H3",
                depth="light",
                confidence=confidence,
                evidence=evidence,
                output_json=_agent_output_with_llm(
                    agent_name="bm",
                    hypothesis_id="H3",
                    role="수익모델과 가격 검증 필요성을 검토한다.",
                    required_fields=[
                        "summary", "signal", "key_findings", "risks",
                        "recommendations", "next_experiment", "revenue_model",
                        "pricing_hypothesis", "market_size_signal",
                        "unit_economics", "key_risk",
                    ],
                    context={
                        "idea": state.get("idea"),
                        "evidence": evidence,
                        "evidence_strength": strength,
                        "stance_counts": stance_counts,
                    },
                ),
            )
        ],
    }


def _decide(
    *,
    missing_evidence: list[str],
    invalid_grounding: list[dict],
    uncovered_hypotheses: list[str],
    low_confidence: list[str],
    high_ip_candidates: list[str],
    contradicting_evidence: list[str],
    borderline_agents: list[str] | None = None,
) -> tuple[Decision, str, Confidence]:
    """최종 판정 규칙. 우선순위: 커버리지 공백 > 치명적 문제 > 판정 불안정 > 근거 약함 > IP 리스크 > go > 기타 pivot.

    KILL의 두 경로(치명적 문제 / 근거 약함)는 "근거가 약하거나 치명적 문제로 현재
    형태 추진 부적합"이라는 합의된 정의를 따른다. MORE_RESEARCH는 근거·가설
    커버리지 자체가 비어있거나(규칙1), 근거 강도가 경계에 몰려 판정이 불안정한
    경우(규칙2.5)로 한정해 KILL과 구분한다.
    """
    if missing_evidence or invalid_grounding or uncovered_hypotheses:
        logger.info("→ 규칙 1: 근거 미연결 또는 가설 미검증 → more_research")
        return (
            "more_research",
            "근거 연결 또는 가설 커버리지에 빈틈이 있어 추가 검증이 필요하다.",
            "low",
        )
    if high_ip_candidates and contradicting_evidence:
        logger.info("→ 규칙 2: IP 고위험 후보 + 반박 근거 동시 존재(치명적 문제) → kill")
        return (
            "kill",
            "IP 고위험 후보와 반박 근거가 동시에 있어 치명적 문제로 본다. 현재 형태로는 추진이 부적합하다.",
            "mid",
        )
    # 규칙 2.5(데드밴드): 치명적 IP 문제는 아니지만, 여러 가설의 근거 강도가 판정 경계(0.40±δ)에
    # 몰려 있으면 go/kill을 확신할 수 없다. 작은 검색 변동에 판정이 뒤집히는 불안정 상태이므로
    # 거짓 확신 대신 추가 검증으로 돌린다(go·kill 양쪽을 보수적으로 보류).
    if borderline_agents and len(borderline_agents) >= BORDERLINE_MAJORITY:
        logger.info(
            f"→ 규칙 2.5: 근거 강도 경계 agent {len(borderline_agents)}개(판정 불안정) → more_research"
        )
        return (
            "more_research",
            "여러 가설의 근거 강도가 판정 경계에 몰려 있어 결론이 불안정하다. 근거를 보강해 재검증이 필요하다.",
            "low",
        )
    # 조정 가능 지점:
    # 현재는 low confidence agent가 3개 이상이면 근거가 약하다고 보고 kill이다.
    # 팀이 더 공격적으로 MVP를 밀고 싶으면 4개 이상으로 완화할 수 있고,
    # 더 보수적으로 보려면 2개 이상으로 강화할 수 있다.
    if len(low_confidence) >= 3:
        logger.info("→ 규칙 3: 신뢰도 낮은 에이전트 ≥3개(근거 약함) → kill")
        return (
            "kill",
            "대부분의 핵심 가설이 low confidence라 근거가 약해 현재 형태로는 추진이 부적합하다.",
            "low",
        )
    # high IP candidate는 법적 결론이 아니라 "회피 설계나 수동 claim chart가 먼저"라는 신호다.
    if high_ip_candidates:
        logger.info("→ 규칙 4: 고위험 IP 후보 있음 → pivot")
        return (
            "pivot",
            "IP 시그니처 후보가 있어 범용 회의 요약보다 vertical workflow 중심으로 좁혀 검증하는 편이 낫다.",
            "mid",
        )
    # 조정 가능 지점:
    # go 조건은 현재 꽤 보수적이다.
    # 반박 근거가 없고 low confidence가 1개 이하일 때만 제한적 go를 허용한다.
    # 실제 운영에서는 "치명도 낮은 contradicts는 허용" 같은 severity 필드를 추가할 수 있다.
    if not contradicting_evidence and len(low_confidence) <= 1:
        logger.info("→ 규칙 5: 반박 근거 없음, 신뢰도 높음 → go")
        return (
            "go",
            "현재 근거 기준으로 치명적 반박이 적어 제한된 MVP 진행이 가능하다.",
            "mid",
        )
    logger.info("→ 규칙 6: 반박 신호 있음 → pivot")
    return (
        "pivot",
        "근거는 있으나 반박 신호가 있어 포지셔닝과 검증 범위를 좁혀야 한다.",
        "mid",
    )


def _kill_reason(scorecard: dict[str, Any]) -> Literal["ip_conflict", "weak_evidence"]:
    """kill이 _decide()의 어느 경로(치명적 문제/근거 약함)에서 나왔는지 scorecard로 되짚는다."""
    if scorecard.get("high_ip_candidates") and scorecard.get("contradicting_evidence"):
        return "ip_conflict"
    return "weak_evidence"


def _alternatives_evidence_ids(
    kill_reason: str,
    scorecard: dict[str, Any],
    agent_runs: list[AgentRun],
    candidates: list[IPOverlapCandidate],
) -> list[str]:
    """대안 제안이 인용할 수 있는 evidence_id를 kill 원인별로 코드가 직접 고른다.

    LLM이 임의로 근거를 인용하지 못하게, 어떤 evidence_id를 써도 되는지 여기서 먼저 정한다.
    """
    if kill_reason == "ip_conflict":
        ip_evidence_ids = {
            candidate.evidence_id
            for candidate in candidates
            if candidate.candidate_id in scorecard.get("high_ip_candidates", [])
        }
        return sorted(set(scorecard.get("contradicting_evidence", [])) | ip_evidence_ids)
    low_confidence_names = set(scorecard.get("low_confidence_agents", []))
    return sorted({
        evidence_id
        for run in agent_runs
        if run.agent_name in low_confidence_names
        for evidence_id in run.grounded_on
    })


def critic_node(state: VentureScoutState) -> dict:
    """agent_runs를 모아 grounding을 확인하고 최종 결정을 기록한다."""

    # Critic은 새로운 시장/기술 판단을 만드는 노드라기보다,
    # 다른 agent들이 남긴 AgentRun이 evidence_id에 제대로 grounded 되었는지 검수하는 최종 감사자다.
    # 그래서 decision은 LLM이 마음대로 바꾸지 않고, 아래 코드 규칙으로 먼저 고정한다.
    start_time = time.time()
    log_stage(logger, "7️⃣", "Critic (최종 종합 판단)")

    job_id = state["analysis_job"].job_id
    agent_runs = state.get("agent_runs", [])
    evidence_items = state.get("evidence_items", {})
    candidates = state.get("ip_overlap_candidates", [])

    log_input(logger, {
        "job_id": job_id,
        "agent_runs_count": len(agent_runs),
        "evidence_items_count": len(evidence_items),
        "ip_candidates_count": len(candidates),
    })

    log_processing(logger, "전체 agent 실행 결과 수집 중...")
    evidence_ids = set(evidence_items)
    grounded_on = sorted({eid for run in agent_runs for eid in run.grounded_on})

    log_processing(logger, "근거 연결 검증 중...")
    missing_evidence = [
        # grounded_on이 비어 있으면 "근거 없는 주장"으로 본다.
        # AgentRun 계약상 원칙적으로 비어 있으면 안 되지만, 안전하게 한 번 더 검사한다.
        f"{run.agent_name} has no grounded_on evidence"
        for run in agent_runs
        if not run.grounded_on
    ]
    invalid_grounding = [
        # agent가 존재하지 않는 evidence_id를 인용했는지 확인한다.
        # 실제 DB 전환 후에는 FK/JSON 검증으로도 한 번 더 막는 것이 좋다.
        {
            "agent_name": run.agent_name,
            "invalid_evidence_ids": sorted(set(run.grounded_on) - evidence_ids),
        }
        for run in agent_runs
        if set(run.grounded_on) - evidence_ids
    ]

    if missing_evidence:
        logger.warning(f"⚠️  근거 미연결: {missing_evidence}")
    if invalid_grounding:
        logger.warning(f"⚠️  잘못된 근거 참조: {invalid_grounding}")

    log_processing(logger, "신뢰도 분석 중...")
    low_confidence = [
        # critic 자신은 최종 검수 결과라서 low confidence 목록에서 제외한다.
        # 여기서는 pre-critic agent들의 분석 신뢰도만 본다.
        run.agent_name
        for run in agent_runs
        if run.agent_name != "critic" and run.confidence == "low"
    ]
    logger.info(f"Low confidence agents: {low_confidence}")

    # borderline: 근거 강도가 판정 경계(0.40±δ)에 걸친 agent. 많으면 판정이 불안정해
    # _decide가 more_research로 돌린다. strength는 _agent_run이 output_json에 실어둔다.
    borderline_agents = [
        run.agent_name
        for run in agent_runs
        if run.agent_name != "critic"
        and _is_borderline_strength(float(run.output_json.get("_evidence_strength") or 0.0))
    ]
    if borderline_agents:
        logger.info(f"Borderline(경계) agents: {borderline_agents}")

    log_processing(logger, "가설 커버리지 확인 중...")
    agent_hypotheses = {
        run.hypothesis_id
        for run in agent_runs
        if run.hypothesis_id
    }
    expected_hypotheses = {
        hypothesis.hypothesis_id
        for hypothesis in state.get("hypotheses", [])
    }
    uncovered_hypotheses = sorted(expected_hypotheses - agent_hypotheses)
    # uncovered_hypotheses가 있으면 어떤 가설은 아무 agent도 검증하지 않은 상태다.
    # 이 경우 최종 결론을 내리기보다 more_research로 돌리는 것이 안전하다.
    if uncovered_hypotheses:
        logger.warning(f"⚠️  미검증 가설: {uncovered_hypotheses}")

    log_processing(logger, "반박 근거 및 IP 위험 확인 중...")
    contradicting_evidence = [
        evidence.evidence_id
        for evidence in evidence_items.values()
        if evidence.stance == "contradicts"
    ]
    high_ip_candidates = [
        candidate.candidate_id
        for candidate in candidates
        # 조정 가능 지점:
        # ip_node의 high_watch 기준과 같은 0.78을 사용한다.
        # 두 곳의 기준이 어긋나지 않도록 나중에는 상수로 분리하는 것이 좋다.
        if candidate.hybrid_score >= 0.78
    ]

    if contradicting_evidence:
        logger.warning(f"⚠️  반박 근거: {contradicting_evidence[:3]}...")
    if high_ip_candidates:
        logger.warning(f"⚠️  IP 위험 신호: {high_ip_candidates}")

    scorecard = {
        "agent_run_count": len(agent_runs),
        "evidence_count": len(evidence_items),
        "grounded_claim_count": len(grounded_on),
        "low_confidence_agents": low_confidence,
        "borderline_agents": borderline_agents,
        "uncovered_hypotheses": uncovered_hypotheses,
        "contradicting_evidence": contradicting_evidence,
        "high_ip_candidates": high_ip_candidates,
        "invalid_grounding": invalid_grounding,
    }

    log_processing(logger, "최종 판정 규칙 적용 중...")

    # 판정 규칙은 _decide()에 모아 단위 테스트로 검증한다(tests/test_critic_decision.py).
    decision, summary, confidence = _decide(
        missing_evidence=missing_evidence,
        invalid_grounding=invalid_grounding,
        uncovered_hypotheses=uncovered_hypotheses,
        low_confidence=low_confidence,
        high_ip_candidates=high_ip_candidates,
        contradicting_evidence=contradicting_evidence,
        borderline_agents=borderline_agents,
    )

    log_processing(logger, f"🎯 최종 판단: {decision.upper()} (신뢰도: {confidence})")

    if not grounded_on:
        raise RuntimeError(
            "Live retrieval returned no usable evidence. Analysis was stopped."
        )

    # 사람이 README 없이도 규칙을 이해할 수 있게 output_json에도 decision_rule을 남긴다.
    # 나중에 dashboard/Evidence Board에서 "왜 more_research가 나왔는지" 보여주는 데 쓸 수 있다.
    decision_rule = (
        "missing/invalid grounding 또는 uncovered hypothesis가 있으면 more_research; "
        "low confidence가 3개 이상이면 more_research; "
        "high IP candidate가 있으면 pivot; "
        "반박 근거가 적고 low confidence가 1개 이하이면 go."
    )
    critic_output_json = _agent_output_with_llm(
        agent_name="critic",
        hypothesis_id="all",
        role=(
            "모든 AgentRun의 근거 연결, 가설 커버리지, confidence, 반박 근거, IP 리스크를 종합 검수한다. "
            "objections는 각 항목마다 '무엇이 왜 문제인지 + 어떤 근거/신호에 기반하는지'를 1~2문장으로 "
            "구체적으로 한국어로 쓴다(단순 나열·UUID 금지). "
            "next_experiments는 비전문가도 바로 실행할 수 있게 '무엇을·누구에게·어떻게·무엇을 보면 검증되는지'를 "
            "각 항목 한 문장으로 한국어로 쓴다."
        ),
        required_fields=[
            "summary", "objections", "missing_evidence", "next_experiments",
        ],
        context={
            "agent_runs": agent_runs,
            "evidence_items": evidence_items,
            "ip_overlap_candidates": candidates,
            "scorecard": scorecard,
            "fixed_decision": decision,
            "fixed_confidence": confidence,
            "rule_summary": summary,
            "decision_rule": decision_rule,
            "missing_evidence": missing_evidence,
            "invalid_grounding": invalid_grounding,
            "uncovered_hypotheses": uncovered_hypotheses,
        },
    )
    critic = CriticResult(
        decision=decision,
        confidence=confidence,
        summary=str(critic_output_json["summary"]),
        grounded_on=grounded_on,
        objections=_to_str_list(critic_output_json["objections"]),
        missing_evidence=_to_str_list(critic_output_json["missing_evidence"]),
        next_experiments=_to_str_list(critic_output_json["next_experiments"]),
    )
    critic_output_json.update(critic.model_dump())
    critic_output_json["scorecard"] = scorecard
    critic_output_json["decision_rule"] = decision_rule

    critic_run = AgentRun(
        agent_run_id=str(uuid.uuid4()),
        job_id=job_id,
        hypothesis_id=None,
        agent_name="critic",
        model_name=current_model_name("critic"),
        depth="full",
        confidence=critic.confidence,
        grounded_on=grounded_on,
        output_json=critic_output_json,
        groundedness_score=1.0 if grounded_on else 0.0,
        overclaim_flag=False,
        status="done",
    )

    analysis_job = state["analysis_job"].model_copy(
        update={
            "status": "done",
            "current_stage": "completed",
            "progress_pct": 100,
            "decision": critic.decision,
            "decision_summary": critic.summary,
        }
    )

    result = {
        "critic": critic,
        "agent_runs": [critic_run],
        "analysis_job": analysis_job,
        "decision": critic.decision,
        "final_report": critic.model_dump(),
        "critic_scorecard": scorecard,
    }

    # 최종 리포트 로깅
    log_processing(logger, "최종 리포트 생성 완료", {
        "decision": critic.decision,
        "confidence": critic.confidence,
        "objection_count": len(critic.objections),
        "next_experiments": len(critic.next_experiments),
    })

    log_output(logger, {
        "critic": critic,
        "agent_runs": result["agent_runs"],
        "analysis_job": analysis_job,
        "decision": critic.decision,
    })

    duration_ms = (time.time() - start_time) * 1000
    log_completion(logger, "Critic", duration_ms)

    return result


def alternatives_node(state: VentureScoutState) -> dict:
    """decision == 'kill'일 때만 critic 다음에 실행되어, kill 원인별로 대안을 제안한다."""
    start_time = time.time()
    log_stage(logger, "8️⃣", "Alternatives (kill 대안 제안)")

    job_id = state["analysis_job"].job_id
    scorecard = state.get("critic_scorecard", {})
    agent_runs = state.get("agent_runs", [])
    evidence_items = state.get("evidence_items", {})
    candidates = state.get("ip_overlap_candidates", [])

    log_input(logger, {"job_id": job_id, "scorecard": scorecard})

    kill_reason = _kill_reason(scorecard)
    evidence_ids = _alternatives_evidence_ids(kill_reason, scorecard, agent_runs, candidates)
    evidence = [evidence_items[eid] for eid in evidence_ids if eid in evidence_items]

    log_processing(logger, "대안 근거 선택 완료", {
        "kill_reason": kill_reason,
        "evidence_count": len(evidence),
    })

    if not evidence:                       # graceful: 인용 가능 근거 0건 -> run 생략
        log_processing(logger, "대안 근거 0건 — alternatives run 생략(graceful)")
        return {"agent_runs": []}

    role = (
        "IP 시그니처 후보와 반박 근거가 동시에 있어 kill로 판정됐다. "
        "특허 회피 설계 또는 vertical 범위 축소 중심으로, 기존 아이디어의 핵심은 유지한 채 "
        "조정 가능한 대안 2~3개를 제안한다."
        if kill_reason == "ip_conflict" else
        "대부분의 핵심 가설이 low confidence라 근거가 약해 kill로 판정됐다. "
        "더 강한 근거가 있는 타겟/포지셔닝/가격정책으로 전환하는 대안 2~3개를 제안한다."
    )

    try:
        output_json = _agent_output_with_llm(
            agent_name="alternatives",
            hypothesis_id="all",
            role=role,
            required_fields=["kill_reason", "alternatives"],
            context={
                "idea": state.get("idea"),
                "kill_reason": kill_reason,
                "critic_objections": state["critic"].objections,
                "evidence": evidence,
            },
        )
    except Exception as exc:  # noqa: BLE001
        # alternatives는 kill 리포트에 곁들이는 보조 제안이다.
        # 여기서 실패해도 이미 완성된 critic의 kill 판정/리포트 자체는 살려야 하므로
        # (api.py가 전체 astream_events를 broad except로 감싸 job을 failed로 덮어쓴다)
        # 다른 노드처럼 예외를 던지지 않고 evidence 0건과 같은 방식으로 graceful skip한다.
        log_processing(logger, f"⚠️  alternatives LLM 호출 실패 — run 생략(graceful): {exc}")
        return {"agent_runs": []}

    output_json["kill_reason"] = kill_reason

    agent_run = _agent_run(
        job_id=job_id,
        agent_name="alternatives",
        hypothesis_id="all",
        depth="light",
        confidence="low",
        evidence=evidence,
        output_json=output_json,
    )

    duration_ms = (time.time() - start_time) * 1000
    log_completion(logger, "Alternatives", duration_ms)

    return {"agent_runs": [agent_run]}


def _route_after_critic(state: VentureScoutState) -> str:
    """critic 직후 라우팅: kill이면 alternatives로, 그 외엔 그래프를 끝낸다."""
    return "alternatives" if state.get("decision") == "kill" else END


def build_graph():
    # LangGraph에 노드를 등록하고 실행 순서를 연결한다.
    # 현재 구조는 Structuring 이후 5개 분석 노드가 병렬로 실행되고, 마지막에 Critic이 합친다.
    # 조정 가능 지점:
    # IP가 Tech 결과를 반드시 읽어야 한다면 tech -> ip 순서로 바꿀 수 있다.
    # Market 결과에 따라 Competitor/BM을 생략하는 조건부 edge도 추가할 수 있다.
    graph = StateGraph(VentureScoutState)
    for name, fn in [
        ("structuring", structuring_node),
        ("market", market_node),
        ("competitor", competitor_node),
        ("tech", tech_node),
        ("ip", ip_node),
        ("bm", bm_node),
        ("critic", critic_node),
        ("alternatives", alternatives_node),
    ]:
        graph.add_node(name, fn)

    graph.add_edge(START, "structuring")
    # Structuring이 idea/hypotheses/documents를 만든 뒤, 각 가설별 agent가 같은 state를 읽는다.
    for node in ["market", "competitor", "tech", "ip", "bm"]:
        graph.add_edge("structuring", node)
        # 각 분석 노드가 agent_runs/evidence_items를 state에 누적하면 Critic이 마지막에 전체를 검수한다.
        graph.add_edge(node, "critic")
    # decision == "kill"일 때만 alternatives_node로 이어진다(그 외엔 그래프 종료).
    graph.add_conditional_edges(
        "critic", _route_after_critic, {"alternatives": "alternatives", END: END}
    )
    graph.add_edge("alternatives", END)
    return graph.compile()


if __name__ == "__main__":
    app = build_graph()
    print(app.invoke({"raw_input": "AI meeting automation SaaS"}))
