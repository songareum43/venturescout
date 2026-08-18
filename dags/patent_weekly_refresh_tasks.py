"""patent_weekly_refresh DAG의 task 본체 — Airflow 의존성 없는 순수 함수.
docs/superpowers/specs/2026-06-22-patent-weekly-refresh-dag-design.md 참고.
Airflow 데코레이터는 dags/patent_weekly_refresh.py에서 이 함수들을 감싸기만 한다.
"""
from __future__ import annotations

from typing import Any


def extract_window_body(start_date, end_date) -> str:
    """주(week) 단위 윈도우를 BigQuery에서 조회해 S3에 저장하고 그 키를 반환한다."""
    from google.cloud import bigquery
    from data.collect_from_bigquery import fetch_window

    client = bigquery.Client()
    return fetch_window(client, start_date, end_date)


def load_to_db_body(s3_key: str) -> dict[str, Any]:
    """S3 윈도우 파일을 documents/patent_claims/claim_limitations에 적재한다."""
    from data.load_from_s3 import load_from_s3, save_to_db

    rows = load_from_s3(s3_key)
    save_to_db(rows)
    return {"rows_loaded": len(rows)}


def embed_new_rows_body(load_result: dict[str, Any]) -> dict[str, Any]:
    """embedding이 비어 있는 행만 임베딩한다.
    PatentIndexer.run()이 아니라 run_claim_limitations()/run_documents()를 직접 호출해
    HNSW 인덱스 drop/rebuild를 건너뛴다 — 주간 소량 배치에는 불필요한 비용이다.
    """
    from pipeline.indexer import PatentIndexer

    indexer = PatentIndexer()
    return {
        "claim_limitations": indexer.run_claim_limitations(),
        "documents": indexer.run_documents(),
    }


def verify_sync_body(embed_result: dict[str, Any]) -> dict[str, Any]:
    """claim_limitations 건수와 임베딩 적재 건수가 일치하는지 확인.
    불일치 시 예외를 던져 Airflow가 해당 실행을 실패로 표시하게 한다.
    """
    from pipeline.indexer import PatentIndexer

    indexer = PatentIndexer()
    result = indexer.verify_sync()
    if not result["gate_pass"]:
        raise RuntimeError(f"D3 게이트 실패: {result}")
    return result
