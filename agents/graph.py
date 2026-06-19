"""Tier 0 스키마 계약에 맞춘 Track C LangGraph.

이 파일은 현재 VentureScout 에이전트 실행의 중심이다.
scripts/run_mock_graph.py와 scripts/run_bedrock_graph.py는 이 파일의 build_graph()를
호출하는 실행 스크립트이고, 실제 에이전트 순서와 판단 로직은 여기서 정의된다.

전체 흐름:
1. structuring_node가 raw_input을 ideas + H1~H5 hypotheses로 구조화한다.
2. Market/Competitor/BM/Tech/IP 노드가 각자 담당 가설의 evidence를 조회한다.
3. 각 노드는 AgentRun 공통 envelope로 결과를 남긴다.
4. critic_node가 모든 AgentRun과 evidence를 검수해 최종 decision을 만든다.

조정 가능 지점:
- confidence threshold: _confidence_from_strength()
- IP risk threshold: ip_node(), critic_node()의 hybrid_score 기준
- Critic 최종 판정 규칙: critic_node()의 if/elif decision rule
- 실제 데이터 연결부: mock_data import, _document_map(), retrieval.tools.retrieve()
"""

from __future__ import annotations

import json
import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.llm import (
    current_model_name,
    invoke_claude_json,
    llm_enabled,
    model_tier_for_agent,
)
from agents.logger import (
    get_logger,
    log_completion,
    log_grounding,
    log_input,
    log_output,
    log_processing,
    log_stage,
)
# 실제 데이터 전환 지점:
# 이 mock_data import는 개발/테스트용 기본값이다.
# 운영에서는 raw_input/job_id/idea_id는 API/DB에서 받고,
# documents/evidence/IP 후보는 repository 또는 retrieval 계층에서 조회한다.
from agents.mock_data import (
    MOCK_DOCUMENTS,
    MOCK_HYPOTHESES,
    MOCK_IDEA_ID,
    MOCK_JOB_ID,
    MOCK_RAW_INPUT,
    MOCK_STRUCTURED_IDEA,
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
    DocumentRecord,
    EvidenceItem,
    Hypothesis,
    IdeaRecord,
)
from shared.state import VentureScoutState

logger = get_logger("graph")


def _mock_hypotheses(job_id: str, idea_id: str) -> list[Hypothesis]:
    # 현재는 mock_data의 H1~H5를 job_id/idea_id만 붙여 Pydantic 모델로 바꾼다.
    # 실제 데이터 전환 후에는 Structuring 결과를 hypotheses 테이블에 저장한 뒤 조회하는 쪽이 자연스럽다.
    return [
        Hypothesis(job_id=job_id, idea_id=idea_id, **item)
        for item in MOCK_HYPOTHESES
    ]


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


def _confidence_from_strength(strength: float) -> Confidence:
    # evidence_strength를 사람이 읽기 쉬운 high/mid/low로 변환한다.
    # 조정 가능 지점:
    # 현재 high는 0.75 이상, mid는 0.45 이상이다.
    # 팀 기준이 더 보수적이면 high 기준을 0.80 또는 0.85로 올릴 수 있다.
    # 반대로 mock/demo에서 high가 거의 안 나오면 0.70으로 낮출 수도 있다.
    if strength >= 0.75:
        return "high"
    if strength >= 0.45:
        return "mid"
    return "low"


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


def _document_map() -> dict[str, DocumentRecord]:
    # 실제 데이터 전환 지점:
    # MOCK_DOCUMENTS 대신 documents 테이블에서 job/idea와 관련된 출처를 조회한다.
    # 반환 형태를 dict[str, DocumentRecord]로 유지하면 뒤쪽 agent 코드는 그대로 동작한다.
    return {
        item["document_id"]: DocumentRecord(**item)
        for item in MOCK_DOCUMENTS
    }


def _hypothesis_query(
    state: VentureScoutState,
    code: str,
    fallback: str,
) -> str:
    """Use the current run's structured hypothesis as the retrieval query."""

    for hypothesis in state.get("hypotheses", []):
        if hypothesis.code == code:
            return hypothesis.statement
    return fallback


def _json_context(payload: dict[str, Any]) -> str:
    """Claude 프롬프트에 넣기 좋게 Pydantic 객체를 JSON 문자열로 바꾼다."""

    return json.dumps(payload, ensure_ascii=False, default=str, indent=2)


def _agent_output_with_llm(
    *,
    agent_name: AgentName,
    hypothesis_id: str,
    role: str,
    default_output: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """근거 계약은 코드가 지키고, 분석 문장만 Claude로 보강한다."""

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
        "아래 context와 default_output을 바탕으로 output_json을 더 전문가답게 보강해라. "
        "default_output의 모든 키를 유지하고, [MOCK] 접두 값은 context에 근거한 "
        "실제 분석 문장으로 반드시 교체해라. grounded_on은 바꾸지 말고, "
        "새 evidence_id를 만들지 마라.\n\n"
        f"CONTEXT:\n{_json_context(context)}\n\n"
        f"DEFAULT_OUTPUT:\n{_json_context(default_output)}"
    )
    # AGENT_LLM_PROVIDER=mock이면 fallback이 그대로 반환된다.
    # AGENT_LLM_PROVIDER=bedrock이면 Bedrock Claude를 호출하고,
    # 인증/권한/JSON 파싱 실패 시에도 그래프가 죽지 않도록 fallback을 반환한다.
    return invoke_claude_json(
        system=system,
        user=user,
        fallback=default_output,
        model_tier=model_tier_for_agent(agent_name),
    )


def _structured_idea_payload(
    *,
    job_id: str,
    idea_id: str,
    raw_input: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """raw_input을 ideas + hypotheses 형태로 구조화한다."""

    fallback_idea = {
        # 실제 데이터 전환 지점:
        # Bedrock/Claude 구조화가 성공하면 MOCK_STRUCTURED_IDEA는 fallback으로만 쓰인다.
        # 운영에서는 파일 파서가 만든 raw_input과 LLM 구조화 결과를 ideas 테이블에 저장한다.
        **MOCK_STRUCTURED_IDEA,
        "idea_id": idea_id,
        "raw_input": raw_input,
    }
    fallback = {
        "idea": fallback_idea,
        "hypotheses": MOCK_HYPOTHESES,
    }
    # mock 모드에서는 Claude를 부르지 않고 고정 구조화 결과를 사용한다.
    # run_mock_graph.py가 항상 AWS 없이 돌아가야 하기 때문이다.
    if not llm_enabled():
        return fallback_idea, MOCK_HYPOTHESES

    system = (
        "너는 VentureScout의 Structuring 에이전트다. 사용자의 사업계획/파일 추출 텍스트를 "
        "분석 가능한 ideas와 H1~H5 hypotheses로 구조화한다. "
        "응답은 설명 없이 JSON object 하나만 반환한다."
    )
    user = (
        "다음 raw_input을 구조화해라.\n\n"
        "반환 JSON 형식:\n"
        "{\n"
        '  "idea": {\n'
        '    "title": "...", "idea_type": "...", "target_customer": "...",\n'
        '    "problem_statement": "...", "solution_summary": "...",\n'
        '    "business_model_hint": "...",\n'
        '    "technical_elements": ["..."],\n'
        '    "patent_keywords": ["..."]\n'
        "  },\n"
        '  "hypotheses": [\n'
        '    {"hypothesis_id":"H1","code":"H1","axis":"customer_problem","statement":"...","confidence":"low","next_validation":"..."},\n'
        '    {"hypothesis_id":"H2","code":"H2","axis":"competition","statement":"...","confidence":"low","next_validation":"..."},\n'
        '    {"hypothesis_id":"H3","code":"H3","axis":"business_model","statement":"...","confidence":"low","next_validation":"..."},\n'
        '    {"hypothesis_id":"H4","code":"H4","axis":"technology","statement":"...","confidence":"low","next_validation":"..."},\n'
        '    {"hypothesis_id":"H5","code":"H5","axis":"ip","statement":"...","confidence":"low","next_validation":"..."}\n'
        "  ]\n"
        "}\n\n"
        f"job_id={job_id}\nidea_id={idea_id}\nraw_input:\n{raw_input}"
    )
    parsed = invoke_claude_json(
        system=system,
        user=user,
        fallback=fallback,
        model_tier="haiku",
    )
    # Claude가 일부 필드를 빼먹어도 fallback_idea가 기본값을 채운다.
    # 조정 가능 지점:
    # 실제 운영에서는 fallback을 조용히 쓰기보다 user_confirmed=false 상태로 돌려
    # 사용자 확인 화면을 띄우는 편이 더 안전하다.
    idea_payload = {
        **fallback_idea,
        **parsed.get("idea", {}),
        "idea_id": idea_id,
        "raw_input": raw_input,
    }
    hypotheses_payload = parsed.get("hypotheses") or MOCK_HYPOTHESES
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
    run = AgentRun(
        # 실제 데이터 전환 지점:
        # DB에 INSERT 성공하면 try_persist_agent_run()이 실제 UUID를 반환한다.
        # 지금은 fallback으로 mock ID를 유지한다.
        agent_run_id=f"run_mock_{agent_name}_{hypothesis_id}",
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

    # DB 적재 배선 — DB가 미설정(mock 모드)이면 내부에서 조용히 건너뛴다.
    # try_persist_agent_run은 절대 예외를 던지지 않으므로 여기서 try/except 불필요.
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

    job_id = state.get("job_id", MOCK_JOB_ID)
    idea_id = state.get("idea_id", MOCK_IDEA_ID)
    raw_input = state.get("raw_input", MOCK_RAW_INPUT)
    # 실제 데이터 전환 지점:
    # job_id/idea_id는 analysis_jobs, ideas insert 결과에서 온다.
    # raw_input은 업로드 파일 파싱 결과 또는 API request body에서 온다.

    log_input(logger, {
        "job_id": job_id,
        "idea_id": idea_id,
        "raw_input": raw_input,
    })

    log_processing(logger, "구조화된 아이디어 payload 생성 중...")
    # Bedrock 모드면 Claude가 raw_input을 구조화하고,
    # mock 모드 또는 Bedrock 실패 시에는 MOCK_STRUCTURED_IDEA/MOCK_HYPOTHESES를 fallback으로 쓴다.
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
        Hypothesis(job_id=job_id, idea_id=idea_id, **item)
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
        raise ValueError(f"Structuring mock data is incomplete: {structuring_quality}")

    result = {
        "idea": idea,
        "analysis_job": analysis_job,
        "hypotheses": hypotheses,
        "documents": _document_map() if not llm_enabled() else {},
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

    log_processing(logger, "H1 관련 근거 검색 중...", {"query": "meeting follow-up pain"})
    query = _hypothesis_query(state, "H1", "meeting follow-up pain")
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

    default_output = {
        "summary": "시장 수요 신호는 보이나 직접 고객 인터뷰로는 아직 검증되지 않았다.",
        "key_findings": ["근거가 시드 데이터 기반이라 실제 사용자 검증은 아직 없다."],
        "risks": ["고객의 문제 강도와 구매 시급성이 입증되지 않았다."],
        "recommendations": ["타깃 고객 10명을 인터뷰해 문제 강도를 확인한다."],
    }

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
            default_output=default_output,
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
    query = _hypothesis_query(state, "H2", "adjacent meeting tools")
    # H2(경쟁/대안) 근거는 경쟁사 시드에서 — 특허 제외
    evidence = retrieve("H2", query, job_id=job_id, source_types=["seed_competitor"])

    if not evidence:                       # graceful: 근거 0건 → run 생략
        return {"agent_runs": []}

    strength = _evidence_strength(evidence)
    confidence = _confidence_from_strength(strength)

    default_output = {
        "summary": "유사 대안 도구가 이미 존재하며 차별점이 아직 입증되지 않았다.",
        "key_findings": ["워크플로우 수준의 포지셔닝으로 경쟁을 돌파해야 한다."],
        "risks": ["범용 요약 시장은 이미 경쟁이 치열하다."],
        "recommendations": ["하나의 버티컬 워크플로우로 범위를 좁힌다."],
    }
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
                    default_output=default_output,
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

    log_processing(logger, "H4 관련 근거 검색 중...", {"query": "STT LLM summarization latency cost"})
    # 실제 데이터 전환 지점:
    # 기술 문서, PoC 결과, 벤치마크 로그를 evidence_items로 적재하면 여기서 실제 H4 근거를 받는다.
    query = _hypothesis_query(
        state,
        "H4",
        "STT LLM summarization latency cost",
    )
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
                    default_output={
                    "summary": (
                        "STT와 LLM 조합으로 프로토타입 경로는 열려 있지만, "
                        "긴 회의에서 지연시간과 단위 비용을 검증해야 한다."
                    ),
                    "feasibility_signal": feasibility_signal,
                    "evidence_strength": strength,
                    "stance_counts": stance_counts,
                    "supporting_evidence": supporting_ids,
                    "risk_evidence": risk_ids,
                    "architecture_assumption": [
                        "음성 파일은 STT API로 텍스트화한다.",
                        "요약과 액션 아이템 추출은 LLM API를 분리 호출한다.",
                        "Slack/Notion 동기화는 비동기 worker로 처리한다.",
                    ],
                    "required_models_or_apis": [
                        "STT API",
                        "LLM summarization API",
                        "LLM action-item extraction prompt",
                        "Slack/Notion integration API",
                    ],
                    "risk_register": [
                        {
                            "risk": "긴 회의 처리 지연",
                            "why_it_matters": "사용자가 회의 직후 결과를 기대하면 UX를 해칠 수 있다.",
                            "mitigation": "구간별 요약, 비동기 처리, 진행률 표시를 실험한다.",
                        },
                        {
                            "risk": "토큰/전사 비용 증가",
                            "why_it_matters": "좌석 단위 SaaS 마진을 갉아먹을 수 있다.",
                            "mitigation": "회의 길이별 원가표와 사용량 제한 정책을 만든다.",
                        },
                        {
                            "risk": "회의 데이터 보안",
                            "why_it_matters": "B2B 고객 도입의 핵심 구매 기준이다.",
                            "mitigation": "저장 최소화, 암호화, tenant 분리를 MVP 요구사항에 포함한다.",
                        },
                    ],
                    "validation_plan": [
                        "30분 회의 10건으로 STT+요약 end-to-end 지연시간 측정",
                        "회의 1시간당 전사 비용과 LLM 토큰 비용 산출",
                        "액션 아이템 precision/recall을 수동 라벨 30개로 비교",
                    ],
                    "go_no_go_metrics": {
                        # 조정 가능 지점:
                        # 이 값들은 제품/고객군에 따라 바뀌는 MVP 통과 기준이다.
                        # 예: 엔터프라이즈 비동기 리포트라면 p95_latency_minutes를 10~30분으로 완화할 수 있고,
                        # 실시간 회의 비서라면 1분 이하로 강화해야 한다.
                        "p95_latency_minutes": "<= 5",
                        "cost_per_meeting_usd": "<= 0.50",
                        "action_item_precision": ">= 0.80",
                    },
                    "recommendations": [
                        "먼저 회의 요약보다 액션 아이템 정확도를 제품 차별화 기준으로 잡는다.",
                        "비용 검증 전에는 무제한 요금제를 가정하지 않는다.",
                    ],
                    },
                    context={
                        "idea": state.get("idea"),
                        "hypothesis_id": "H4",
                        "evidence": evidence,
                        "stance_counts": stance_counts,
                        "evidence_strength": strength,
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

    log_processing(logger, "H5 관련 근거 검색 중...", {"query": "meeting summarization patent limitations"})
    # 실제 데이터 전환 지점:
    # 특허 documents/claim_limitations가 적재되면 retrieve()가 실제 H5 근거를 반환한다.
    query = _hypothesis_query(
        state,
        "H5",
        "meeting summarization patent limitations",
    )
    evidence = retrieve("H5", query, job_id=job_id)
    log_processing(logger, "근거 수집 완료", {"evidence_count": len(evidence)})

    if not evidence:                       # graceful: 근거 0건 → run 생략
        log_processing(logger, "H5 근거 0건 — ip run 생략(graceful)")
        return {"agent_runs": []}

    log_processing(logger, "IP 특허 후보 벡터 검색 중...", {"elements": idea.technical_elements})
    # 실제 데이터 전환 지점:
    # vector_search()가 실제 claim_limitations 벡터/키워드 검색을 수행하고 후보를 반환해야 한다.
    candidates = vector_search(
        idea.technical_elements,
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
                    default_output={
                    "summary": (
                        "시그니처 검색 후보에서 일부 claim limitation 중첩 신호가 보인다. "
                        "이는 법적 침해 판단이 아니라, 수동 검토와 회피 설계를 위한 우선순위 신호다."
                    ),
                    "overlap_signal": overlap_signal,
                    "evidence_strength": strength,
                    "stance_counts": stance_counts,
                    "high_overlap_elements": high_overlap,
                    "design_around_options": [
                        "범용 회의 요약 대신 특정 직무/산업 workflow 후속 조치로 범위를 좁힌다.",
                        "요약 생성 자체보다 action item 상태 추적, 담당자 배정, 완료 검증을 핵심 차별점으로 둔다.",
                        "claim chart에서 speech-to-text, summary generation, task extraction 구성요소를 분리해 검토한다.",
                    ],
                    "claim_review_queue": [
                        {
                            "candidate_id": row["candidate_id"],
                            "element": row["plan_technical_element"],
                            "hybrid_score": row["hybrid_score"],
                            "risk_band": row["risk_band"],
                            "evidence_id": row["evidence_id"],
                        }
                        for row in candidate_rows
                    ],
                    "legal_guardrail_note": (
                        "특허 침해 여부를 단정하지 않는다. 현재 출력은 claim limitation 유사도와 "
                        "evidence_id에 기반한 사전 리스크 신호다."
                    ),
                    "manual_review_questions": [
                        "독립항 기준으로 필수 구성요소가 모두 제품 구현에 들어가는가?",
                        "요약 생성과 action item 추출이 같은 claim family에 묶이는가?",
                        "workflow-specific 후속 조치 중심으로 claim 요소를 회피할 수 있는가?",
                    ],
                    "candidates": candidate_rows,
                    },
                    context={
                        "idea": idea,
                        "hypothesis_id": "H5",
                        "evidence": evidence,
                        "ip_overlap_candidates": candidates,
                        "candidate_rows": candidate_rows,
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

    query = _hypothesis_query(
        state,
        "H3",
        "per-seat SaaS pricing willingness",
    )
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

    default_output = {
        "summary": "Per-seat SaaS is plausible but unvalidated.",
        "key_findings": ["Pricing evidence is only a placeholder."],
        "risks": ["Buyer willingness and budget owner are unknown."],
        "recommendations": ["Run pricing interviews."],
        # ── D 작업분(ADR-028/032): BM 도메인 5필드 + signal/next_experiment ──
        # C의 _agent_output_with_llm 프레임워크 안으로 합침. mock이면 아래 값이,
        # AGENT_LLM_PROVIDER=bedrock이면 Claude가 이 키들을 채워 반환한다(loose, ADR-016).
        "signal": "[MOCK] 거래 수수료+구독 혼합 수익모델, 단위경제 미검증",
        "next_experiment": "[MOCK] 가격 민감도 테스트(랜딩 A/B)로 과금단위·전환율 검증",
        "revenue_model": "[MOCK] 거래 수수료 + 프리미엄 구독 혼합",
        "pricing_hypothesis": "[MOCK] 거래액 3% 수수료 / 팀 단위 월 $29 구독",
        "market_size_signal": "[MOCK] 추천 커머스 SaaS TAM 확대 추세(우상향)",
        "unit_economics": "[MOCK] LTV > CAC 추정(구독 리텐션 가정) — 미검증",
        "key_risk": "[MOCK] 무료 대체재·플랫폼 자체 추천기능에 마진 잠식",
    }
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
                    default_output=default_output,
                    context={
                        "idea": state.get("idea"),
                        "evidence": evidence,
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
) -> tuple[Decision, str, Confidence]:
    """최종 판정 규칙. 우선순위: 커버리지 공백 > 치명적 문제 > 근거 약함 > IP 리스크 > go > 기타 pivot.

    KILL의 두 경로(치명적 문제 / 근거 약함)는 "근거가 약하거나 치명적 문제로 현재
    형태 추진 부적합"이라는 합의된 정의를 따른다. MORE_RESEARCH는 근거·가설
    커버리지 자체가 비어있는 경우로 한정해 KILL과 구분한다.
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
    )

    log_processing(logger, f"🎯 최종 판단: {decision.upper()} (신뢰도: {confidence})")

    # 반론은 한국어 서술형으로 — UUID 나열 대신 '무엇이/왜 문제인지'를 담는다.
    # (live에서는 아래 기본 반론을 critic LLM이 더 구체적으로 보강한다.)
    _agent_ko = {"market": "시장", "competitor": "경쟁", "tech": "기술",
                 "ip": "IP(특허)", "bm": "비즈니스모델"}
    objections = []
    if low_confidence:
        names = ", ".join(_agent_ko.get(a, a) for a in low_confidence)
        objections.append(
            f"{names} 분석의 근거가 약해 신뢰도가 낮음 — 해당 가설은 현재 결론을 확신하기 어렵고 "
            f"직접 데이터(인터뷰·실측)로 보강이 필요하다."
        )
    if contradicting_evidence:
        objections.append(
            f"가설을 반박하는 근거가 {len(contradicting_evidence)}건 발견됨 — 낙관적 결론을 그대로 "
            f"받아들이기 전에 이 반대 근거의 타당성과 치명도를 먼저 검토해야 한다."
        )
    if high_ip_candidates:
        objections.append(
            f"특허 청구항 중첩 위험 신호가 {len(high_ip_candidates)}건 — 법적 침해 단정은 아니나 "
            f"수동 claim chart 검토와 회피 설계가 선행되어야 한다."
        )

    critic = CriticResult(
        decision=decision,
        confidence=confidence,
        summary=summary,
        grounded_on=grounded_on,
        objections=objections,
        missing_evidence=missing_evidence
        + [
            f"{item['agent_name']} cites unknown evidence ids: {item['invalid_evidence_ids']}"
            for item in invalid_grounding
        ]
        + [
            f"No agent run covered hypothesis {hypothesis_id}"
            for hypothesis_id in uncovered_hypotheses
        ],
        next_experiments=[
            "H1: 타깃 고객 10명에게 회의 후속 업무 pain intensity를 인터뷰한다.",
            "H3: 구매 담당자 기준 좌석당 지불 의사와 예산 출처를 확인한다.",
            "H4: 30분 회의 10건으로 지연시간, 전사 비용, LLM 비용을 측정한다.",
            "H5: high_watch IP 후보에 대해 claim chart를 수동 작성한다.",
        ],
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
        default_output={
            **critic.model_dump(),
            "scorecard": scorecard,
            "decision_rule": decision_rule,
        },
        context={
            "agent_runs": agent_runs,
            "evidence_items": evidence_items,
            "ip_overlap_candidates": candidates,
            "scorecard": scorecard,
            "fixed_decision": decision,
            "fixed_confidence": confidence,
        },
    )
    # Claude가 summary/objections/next_experiments를 더 좋은 문장으로 보강할 수는 있지만,
    # decision/confidence 자체는 위 코드 규칙으로 계산한 값을 유지한다.
    # 조정 가능 지점:
    # 장기적으로는 Critic LLM에게 대안 decision을 제안하게 하고,
    # 코드 decision과 다를 때 human review queue로 보내는 구조도 가능하다.
    critic = critic.model_copy(
        update={
            "summary": critic_output_json.get("summary", critic.summary),
            "objections": critic_output_json.get("objections", critic.objections),
            "missing_evidence": critic_output_json.get(
                "missing_evidence",
                critic.missing_evidence,
            ),
            "next_experiments": critic_output_json.get(
                "next_experiments",
                critic.next_experiments,
            ),
        }
    )
    critic_output_json.update(critic.model_dump())
    critic_output_json["scorecard"] = scorecard
    critic_output_json["decision_rule"] = decision_rule

    critic_run = AgentRun(
        agent_run_id="run_mock_critic",
        job_id=job_id,
        hypothesis_id=None,
        agent_name="critic",
        model_name=current_model_name("critic"),
        depth="full",
        confidence=critic.confidence,
        grounded_on=grounded_on or ["ev_mock_handoff"],
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
    ]:
        graph.add_node(name, fn)

    graph.add_edge(START, "structuring")
    # Structuring이 idea/hypotheses/documents를 만든 뒤, 각 가설별 agent가 같은 state를 읽는다.
    for node in ["market", "competitor", "tech", "ip", "bm"]:
        graph.add_edge("structuring", node)
        # 각 분석 노드가 agent_runs/evidence_items를 state에 누적하면 Critic이 마지막에 전체를 검수한다.
        graph.add_edge(node, "critic")
    graph.add_edge("critic", END)
    return graph.compile()


if __name__ == "__main__":
    app = build_graph()
    print(app.invoke({"raw_input": "AI meeting automation SaaS"}))
