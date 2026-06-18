"""
DB 쓰기 레이어 — B가 소유한 3개 테이블 (Schema_explains.md ⑤⑥⑨).

  evidence_items        — B가 stance 태깅하며 채움
  agent_runs            — ②③(B 소유 에이전트) 출력 envelope
  ip_overlap_candidates — B(기계) produce → ⑤ 에이전트(C) read

job_id/hypothesis_id는 그래프 오케스트레이션이 LangGraph RunnableConfig로
전달한다고 가정 — PatentScoutState(Day 1 계약, models/state.py)는 변경하지 않는다.
"""
from __future__ import annotations

import json as _json
import logging as _logging
import os as _os
import uuid as _uuid

import psycopg2
from pgvector.psycopg2 import register_vector

from config import config

_logger = _logging.getLogger("persistence")


def _is_real_uuid(value: str) -> bool:
    """문자열이 RFC 4122 UUID인지 확인한다.

    DB의 job_id/hypothesis_id 컬럼은 uuid 타입이므로
    mock ID("job_mock_001", "H1" 등)를 그대로 넘기면 INSERT가 실패한다.
    이 함수로 먼저 걸러 DB 오류를 방지한다.
    """
    try:
        _uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def get_connection() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(
        config.db_dsn,
        connect_timeout=config.db_connect_timeout,
    )
    register_vector(conn)
    return conn


# ── evidence_items ────────────────────────────────────────────────────────

def create_evidence_item(
    conn,
    *,
    job_id: str,
    hypothesis_id: str,
    document_id: str,
    source_type: str,
    evidence_text: str,
    stance: str = "neutral",  # 'supports' | 'contradicts' | 'neutral'
    relevance_score: float | None = None,
    reliability_score: float | None = None,
) -> str:
    """evidence_items 1행 생성, evidence_id(uuid str) 반환."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO evidence_items
                (job_id, hypothesis_id, document_id, source_type, evidence_text,
                 stance, relevance_score, reliability_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING evidence_id
            """,
            (job_id, hypothesis_id, document_id, source_type, evidence_text,
             stance, relevance_score, reliability_score),
        )
        evidence_id = cur.fetchone()[0]
    conn.commit()
    return str(evidence_id)


def create_evidence_items_for_documents(
    conn,
    *,
    job_id: str,
    hypothesis_id: str,
    documents: list[dict],
    stance: str = "neutral",
    text_limit: int = 2000,
) -> dict[str, str]:
    """
    evidence_search()/search_documents() 결과(document_id, source_type, clean_text,
    reliability_score, hybrid_score 포함)를 받아 evidence_items를 일괄 생성.

    Returns: {document_id: evidence_id} 매핑
    """
    mapping: dict[str, str] = {}
    for d in documents:
        evidence_id = create_evidence_item(
            conn,
            job_id=job_id,
            hypothesis_id=hypothesis_id,
            document_id=d["document_id"],
            source_type=d.get("source_type", "web"),
            evidence_text=str(d.get("clean_text", ""))[:text_limit],
            stance=stance,
            relevance_score=d.get("hybrid_score"),
            reliability_score=d.get("reliability_score"),
        )
        mapping[d["document_id"]] = evidence_id
    return mapping


# ── agent_runs ───────────────────────────────────────────────────────────

def create_agent_run(
    conn,
    *,
    job_id: str,
    hypothesis_id: str | None,
    agent_name: str,         # 'market' | 'competitor' | 'tech' | 'ip' | 'bm' | 'critic' | 'structuring'
    model_name: str,
    depth: str,               # 'full' | 'light'
    confidence: str,          # 'high' | 'mid' | 'low'
    grounded_on: list[str],   # evidence_id 배열 (required)
    output_json: dict,
    groundedness_score: float | None = None,
    overclaim_flag: bool = False,
    status: str = "done",
    target_run_id: str | None = None,
) -> str:
    """agent_runs 1행 생성, agent_run_id(uuid str) 반환."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_runs
                (job_id, hypothesis_id, target_run_id, agent_name, model_name,
                 depth, confidence, grounded_on, output_json,
                 groundedness_score, overclaim_flag, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING agent_run_id
            """,
            (job_id, hypothesis_id, target_run_id, agent_name, model_name,
             depth, confidence, _json.dumps(grounded_on), _json.dumps(output_json),
             groundedness_score, overclaim_flag, status),
        )
        agent_run_id = cur.fetchone()[0]
    conn.commit()
    return str(agent_run_id)


# ── ip_overlap_candidates ────────────────────────────────────────────────

def create_ip_overlap_candidates(
    conn,
    *,
    job_id: str,
    hypothesis_id: str,
    plan_technical_element: str,
    candidates: list[dict],
) -> list[str]:
    """
    search_claim_limitations() 결과(limitation_id, document_id, normalized_text,
    lexical_score, similarity_score, hybrid_score 포함)를 받아
    evidence_items(stance='neutral') + ip_overlap_candidates 행을 생성.

    중첩 여부(supports/contradicts) 판단은 ⑤ IP 에이전트(C) 몫이라
    여기서 만드는 evidence_items는 stance='neutral'로 둔다.

    Returns: candidate_id 리스트 (rank 순)
    """
    candidate_ids: list[str] = []
    for rank, c in enumerate(candidates, start=1):
        evidence_id = create_evidence_item(
            conn,
            job_id=job_id,
            hypothesis_id=hypothesis_id,
            document_id=c["document_id"],
            source_type="patent",
            evidence_text=c["normalized_text"],
            stance="neutral",
            relevance_score=c.get("hybrid_score"),
            reliability_score=config.source_reliability.get("patent"),
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ip_overlap_candidates
                    (job_id, hypothesis_id, limitation_id, evidence_id,
                     plan_technical_element, lexical_score, similarity_score,
                     hybrid_score, rank)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING candidate_id
                """,
                (job_id, hypothesis_id, c["limitation_id"], evidence_id,
                 plan_technical_element, c.get("lexical_score"), c.get("similarity_score"),
                 c.get("hybrid_score"), rank),
            )
            candidate_ids.append(str(cur.fetchone()[0]))

    conn.commit()
    return candidate_ids


# ── LangGraph 연동 헬퍼 ───────────────────────────────────────────────────

def _job_context(run_config: dict | None) -> tuple[str | None, str | None]:
    """RunnableConfig에서 job_id/hypothesis_id 추출. 없으면 (None, None)."""
    cfg = (run_config or {}).get("configurable", {})
    return cfg.get("job_id"), cfg.get("hypothesis_id")


def persist_agent_output(
    run_config: dict | None,
    *,
    agent_name: str,
    depth: str,
    confidence: str,
    output_json: dict,
    evidence_documents: list[dict] | None = None,
    evidence_stance: str = "neutral",
) -> str | None:
    """
    config에 job_id가 있을 때만 evidence_items + agent_runs를 기록.
    job_id가 없으면 아무것도 하지 않고 None 반환 (state 기반 흐름은 그대로 동작).

    Returns: agent_run_id (기록 안 했으면 None)
    """
    job_id, hypothesis_id = _job_context(run_config)
    if not job_id:
        return None

    conn = get_connection()
    try:
        grounded_on: list[str] = []
        if evidence_documents:
            mapping = create_evidence_items_for_documents(
                conn,
                job_id=job_id,
                hypothesis_id=hypothesis_id,
                documents=evidence_documents,
                stance=evidence_stance,
            )
            grounded_on = list(mapping.values())

        return create_agent_run(
            conn,
            job_id=job_id,
            hypothesis_id=hypothesis_id,
            agent_name=agent_name,
            model_name=config.bedrock_model_id,
            depth=depth,
            confidence=confidence,
            grounded_on=grounded_on,
            output_json=output_json,
        )
    finally:
        conn.close()


# ── Track C 노드용 고수준 적재 헬퍼 ─────────────────────────────────────────

def _db_configured() -> bool:
    """DB 연결 정보가 최소한 하나라도 설정되어 있으면 True.

    DATABASE_URL → RDS_SECRET_ARN → POSTGRES_PASSWORD 순으로 확인한다.
    셋 다 없으면 mock/로컬 환경으로 보고 DB 쓰기를 건너뛴다.
    """
    return bool(
        _os.getenv("DATABASE_URL")
        or _os.getenv("RDS_SECRET_ARN")
        or config.db_password  # POSTGRES_PASSWORD가 설정된 경우
    )


def try_persist_agent_run(agent_run, evidence) -> str | None:
    """AgentRun Pydantic 모델을 DB에 저장 시도한다. (Track C 노드 전용)

    설계 원칙:
    - DB가 설정되지 않은 환경(AGENT_LLM_PROVIDER=mock, 로컬 테스트)에서는
      아무것도 하지 않고 None을 반환한다. 그래프 흐름에 영향이 없다.
    - DB 연결이나 INSERT가 실패해도 예외를 던지지 않는다.
      적재 실패는 경고 로그로 남기고, 그래프는 계속 실행된다.
    - evidence_items 먼저 INSERT → agent_runs INSERT 순서를 지킨다.
      이 순서를 어기면 agent_runs.grounded_on에 아직 존재하지 않는
      evidence_id가 담겨 조회 시 불일치가 생긴다.

    mock evidence 처리:
    - retrieve() mock 모드가 반환하는 evidence_id는 'ev_mock_' 접두사를 가진다.
    - 이 ID는 documents 테이블에 실제 행이 없으므로 evidence_items INSERT 시
      document_id FK 위반이 발생한다.
    - 따라서 mock evidence는 evidence_items INSERT를 건너뛰고,
      기존 ID를 grounded_on에 그대로 사용한다.
    - 실제 retrieve()로 교체되면 prefix가 바뀌어 자동으로 정상 경로를 탄다.

    Args:
        agent_run: AgentRun Pydantic 모델 (shared.contracts.AgentRun)
        evidence:  EvidenceItem 리스트 (shared.contracts.EvidenceItem)

    Returns:
        DB에서 발급된 agent_run_id(str), 건너뛰었으면 None
    """
    # DB가 설정되지 않은 환경 → 조용히 종료 (mock 모드 호환)
    if not _db_configured():
        return None

    # job_id가 유효한 UUID가 아니면 mock run이다.
    # DB의 job_id 컬럼은 uuid 타입이므로 "job_mock_001" 같은 값을 넘기면
    # INSERT가 type 오류로 실패한다. 여기서 미리 걸러 불필요한 DB 왕복을 막는다.
    # 실제 job_id(UUID)가 들어오기 시작하면 이 분기는 자동으로 통과된다.
    if not _is_real_uuid(agent_run.job_id):
        _logger.debug(
            "[PERSIST SKIP] mock job_id 감지 — agent=%s job_id=%s",
            agent_run.agent_name, agent_run.job_id,
        )
        return None

    # DB 연결 시도. 연결 실패 시 그래프는 죽이지 않고 경고만 남긴다.
    try:
        conn = get_connection()
    except Exception as exc:
        _logger.warning(
            "[PERSIST SKIP] DB 연결 실패 — agent=%s hypothesis=%s error=%s",
            agent_run.agent_name, agent_run.hypothesis_id, exc,
        )
        return None

    try:
        # ── 1단계: evidence_items INSERT ──────────────────────────────────
        # agent_runs.grounded_on이 실제 evidence_id를 참조해야 하므로
        # 반드시 agent_runs INSERT 전에 실행한다.
        persisted_ids: list[str] = []
        for ev in evidence:
            if ev.evidence_id.startswith("ev_mock_"):
                # mock evidence → documents 테이블에 없으므로 FK 위반 방지.
                # 실제 검색으로 교체되면 이 분기는 자동으로 사라진다.
                persisted_ids.append(ev.evidence_id)
                continue

            try:
                real_id = create_evidence_item(
                    conn,
                    job_id=agent_run.job_id,
                    hypothesis_id=agent_run.hypothesis_id or "",
                    document_id=ev.document_id,
                    source_type=ev.source_type,
                    evidence_text=ev.evidence_text,
                    stance=ev.stance,
                    relevance_score=ev.relevance_score,
                    reliability_score=ev.reliability_score,
                )
                persisted_ids.append(real_id)
            except Exception as exc:
                # 개별 evidence INSERT 실패 → 해당 항목만 건너뜀.
                # 나머지 evidence와 agent_run은 계속 저장한다.
                _logger.warning(
                    "[PERSIST SKIP] evidence_item INSERT 실패 — id=%s error=%s",
                    ev.evidence_id, exc,
                )
                persisted_ids.append(ev.evidence_id)  # 원본 ID 보존

        # ── 2단계: agent_runs INSERT ──────────────────────────────────────
        # grounded_on은 위에서 확정된 evidence_id 목록을 사용한다.
        # mock ID가 섞여 있어도 grounded_on은 jsonb/text[]라 FK 제약이 없다.
        effective_grounded = persisted_ids or agent_run.grounded_on
        run_id = create_agent_run(
            conn,
            job_id=agent_run.job_id,
            hypothesis_id=agent_run.hypothesis_id,
            agent_name=agent_run.agent_name,
            model_name=agent_run.model_name or config.bedrock_model_id,
            depth=agent_run.depth,
            confidence=agent_run.confidence,
            grounded_on=effective_grounded,
            output_json=agent_run.output_json,
            groundedness_score=agent_run.groundedness_score,
            overclaim_flag=agent_run.overclaim_flag,
            status=agent_run.status,
        )
        _logger.info(
            "[PERSIST OK] agent=%s hypothesis=%s → agent_run_id=%s",
            agent_run.agent_name, agent_run.hypothesis_id, run_id,
        )
        return run_id

    except Exception as exc:
        # agent_runs INSERT 자체가 실패한 경우. 그래프는 계속 실행된다.
        _logger.error(
            "[PERSIST ERROR] agent_run INSERT 실패 — agent=%s error=%s",
            agent_run.agent_name, exc,
        )
        return None

    finally:
        # 연결은 항상 닫는다. connection pool 없이 매번 새 연결을 쓰기 때문.
        conn.close()
