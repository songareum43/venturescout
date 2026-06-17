"""DB 연결용 Agent workflow skeleton.

아직 LLM 호출은 하지 않는다.
목표는 mock_data 중심 흐름을 실제 PostgreSQL 테이블에 연결하기 위한 함수 경계를 만드는 것이다.

주의:
- 컬럼명은 코드에만 기대지 않고 실행 시 information_schema에서 확인한다.
- INSERT는 실제 테이블에 존재하는 컬럼만 사용한다.
- documents 검색은 retrieval.pgvector_search를 통해 documents.embedding을 조회한다.
"""

from __future__ import annotations

import json
from typing import Any

from db.connection import db_cursor
from psycopg2.extras import Json
from retrieval.pgvector_search import search_documents_by_vector


def get_table_columns(table_name: str) -> set[str]:
    """information_schema에서 실제 컬럼명을 읽는다."""

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
            """,
            (table_name,),
        )
        return {row["column_name"] for row in cur.fetchall()}


def insert_row(
    table_name: str,
    payload: dict[str, Any],
    *,
    returning: str,
    required: set[str] | None = None,
) -> str:
    """실제 컬럼만 골라 INSERT하고 returning 값을 반환한다."""

    columns = get_table_columns(table_name)
    required = required or set()
    missing = sorted(required - columns)
    if missing:
        raise RuntimeError(f"{table_name} 테이블에 필요한 컬럼이 없습니다: {missing}")

    filtered = {
        key: value
        for key, value in payload.items()
        if key in columns and value is not None
    }
    if not filtered:
        raise RuntimeError(f"{table_name}에 INSERT할 수 있는 컬럼이 없습니다.")
    if returning not in columns:
        raise RuntimeError(f"{table_name} 테이블에 RETURNING 컬럼이 없습니다: {returning}")

    column_sql = ", ".join(f'"{key}"' for key in filtered)
    value_sql = ", ".join(["%s"] * len(filtered))
    values = [serialize_value(value) for value in filtered.values()]

    with db_cursor(commit=True) as cur:
        cur.execute(
            f'INSERT INTO public."{table_name}" ({column_sql}) '
            f"VALUES ({value_sql}) RETURNING \"{returning}\"::text AS id",
            values,
        )
        return cur.fetchone()["id"]


def serialize_value(value: Any) -> Any:
    """jsonb 컬럼에 들어갈 list/dict 값을 psycopg2 Json adapter로 감싼다."""

    if isinstance(value, (dict, list)):
        return Json(value, dumps=lambda item: json.dumps(item, ensure_ascii=False))
    return value


def create_idea(raw_input: str, structured: dict[str, Any] | None = None) -> str:
    """ideas row를 만든다. structured는 Structuring 결과가 들어올 자리다."""

    structured = structured or {}
    payload = {
        "raw_input": raw_input,
        "title": structured.get("title"),
        "idea_type": structured.get("idea_type"),
        "target_customer": structured.get("target_customer"),
        "problem_statement": structured.get("problem_statement"),
        "solution_summary": structured.get("solution_summary"),
        "business_model_hint": structured.get("business_model_hint"),
        "technical_elements": structured.get("technical_elements", []),
        "patent_keywords": structured.get("patent_keywords", []),
        "user_confirmed": structured.get("user_confirmed", False),
    }
    return insert_row("ideas", payload, returning="idea_id", required={"raw_input"})


def create_analysis_job(idea_id: str) -> str:
    """analysis_jobs row를 만든다."""

    payload = {
        "idea_id": idea_id,
        "status": "running",
        "current_stage": "created",
        "progress_pct": 0,
    }
    return insert_row(
        "analysis_jobs",
        payload,
        returning="job_id",
        required={"idea_id", "status"},
    )


def create_hypotheses(
    *,
    job_id: str,
    idea_id: str,
    hypotheses: list[dict[str, Any]],
) -> list[str]:
    """hypotheses rows를 만든다."""

    hypothesis_ids: list[str] = []
    for item in hypotheses:
        payload = {
            "job_id": job_id,
            "idea_id": idea_id,
            "code": item.get("code"),
            "axis": item.get("axis"),
            "statement": item.get("statement"),
            "confidence": item.get("confidence", "low"),
            "next_validation": item.get("next_validation"),
        }
        hypothesis_ids.append(
            insert_row(
                "hypotheses",
                payload,
                returning="hypothesis_id",
                required={"job_id", "idea_id", "code", "axis", "statement"},
            )
        )
    return hypothesis_ids


def search_evidence_for_hypothesis(
    *,
    job_id: str,
    hypothesis_id: str,
    query_text: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """documents에서 후보 문서를 검색한다. evidence_items INSERT는 다음 단계에서 확정한다."""

    results = search_documents_by_vector(query_text, top_k=top_k)
    for item in results:
        item["job_id"] = job_id
        item["hypothesis_id"] = hypothesis_id
    return results


def log_agent_run(
    *,
    job_id: str,
    agent_name: str,
    hypothesis_id: str | None,
    grounded_on: list[str],
    output_json: dict[str, Any],
    confidence: str = "low",
    depth: str = "light",
    model_name: str = "skeleton",
) -> str:
    """agent_runs row를 기록한다."""

    payload = {
        "job_id": job_id,
        "hypothesis_id": hypothesis_id,
        "agent_name": agent_name,
        "model_name": model_name,
        "depth": depth,
        "confidence": confidence,
        "grounded_on": grounded_on,
        "output_json": output_json,
        "groundedness_score": 1.0 if grounded_on else 0.0,
        "overclaim_flag": False,
        "status": "done",
    }
    return insert_row(
        "agent_runs",
        payload,
        returning="agent_run_id",
        required={"job_id", "agent_name", "grounded_on", "output_json"},
    )


def run_analysis_workflow(raw_input: str) -> dict[str, Any]:
    """LLM 없는 DB workflow skeleton을 실행한다."""

    idea_id = create_idea(raw_input)
    job_id = create_analysis_job(idea_id)

    # TODO: Structuring LLM을 붙이면 raw_input에서 이 가설들을 생성한다.
    skeleton_hypotheses = [
        {
            "code": "H1",
            "axis": "customer_problem",
            "statement": "타깃 고객이 이 문제를 반복적으로 겪는가?",
            "confidence": "low",
            "next_validation": "고객 인터뷰",
        },
        {
            "code": "H2",
            "axis": "competition",
            "statement": "기존 대안이 해결하지 못하는 차별화 지점이 있는가?",
            "confidence": "low",
            "next_validation": "경쟁 제품 비교",
        },
        {
            "code": "H3",
            "axis": "business_model",
            "statement": "타깃 고객이 이 솔루션에 반복적으로 비용을 지불할 의사가 있는가?",
            "confidence": "low",
            "next_validation": "가격 인터뷰",
        },
        {
            "code": "H4",
            "axis": "technology",
            "statement": "핵심 기능을 현재 기술로 구현할 수 있는가?",
            "confidence": "low",
            "next_validation": "PoC 벤치마크",
        },
        {
            "code": "H5",
            "axis": "ip",
            "statement": "핵심 기술요소가 기존 특허와 위험하게 겹치지 않는가?",
            "confidence": "low",
            "next_validation": "claim limitation 검색",
        },
    ]
    hypothesis_ids = create_hypotheses(
        job_id=job_id,
        idea_id=idea_id,
        hypotheses=skeleton_hypotheses,
    )

    agent_name_by_code = {
        "H1": "market",
        "H2": "competitor",
        "H3": "bm",
        "H4": "tech",
        "H5": "ip",
    }

    evidence_preview = []
    for hypothesis_id, hypothesis in zip(hypothesis_ids, skeleton_hypotheses):
        docs = search_evidence_for_hypothesis(
            job_id=job_id,
            hypothesis_id=hypothesis_id,
            query_text=hypothesis["statement"],
            top_k=3,
        )
        evidence_preview.append(
            {
                "hypothesis_id": hypothesis_id,
                "query": hypothesis["statement"],
                "documents": docs,
            }
        )
        log_agent_run(
            job_id=job_id,
            agent_name=agent_name_by_code[hypothesis["code"]],
            hypothesis_id=hypothesis_id,
            grounded_on=[],
            output_json={
                "summary": "LLM 호출 전 skeleton run입니다.",
                "skeleton": True,
                "document_candidates": docs,
            },
            confidence="low",
            depth="light" if hypothesis["code"] in {"H2", "H3", "H4"} else "full",
        )

    return {
        "idea_id": idea_id,
        "job_id": job_id,
        "hypothesis_ids": hypothesis_ids,
        "evidence_preview": evidence_preview,
    }


if __name__ == "__main__":
    result = run_analysis_workflow("AI meeting automation SaaS")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
