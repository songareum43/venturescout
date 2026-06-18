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

import psycopg2
from pgvector.psycopg2 import register_vector

from config import config


def get_connection() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(config.db_dsn)
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
