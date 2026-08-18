"""특허 데이터 주간 증분 갱신 DAG (연습용 — 실 인프라·docker-compose에 연결되지 않음).
docs/superpowers/specs/2026-06-22-patent-weekly-refresh-dag-design.md 참고.

task 본체 로직은 dags/patent_weekly_refresh_tasks.py에 있다(Airflow 비의존 순수 함수).
이 파일은 그 함수들을 @task로 감싸고 순서·재시도 정책만 정의한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

from dags.patent_weekly_refresh_tasks import (
    embed_new_rows_body,
    extract_window_body,
    load_to_db_body,
    verify_sync_body,
)


@dag(
    dag_id="patent_weekly_refresh",
    schedule="0 3 * * 1",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["patents", "embedding", "weekly", "practice"],
)
def patent_weekly_refresh():

    @task(retries=3, retry_delay=timedelta(minutes=10))
    def extract_window() -> str:
        context = get_current_context()
        return extract_window_body(
            context["data_interval_start"], context["data_interval_end"]
        )

    @task(retries=2, retry_delay=timedelta(minutes=5))
    def load_to_db(s3_key: str) -> dict:
        return load_to_db_body(s3_key)

    @task(retries=2, retry_delay=timedelta(minutes=15))
    def embed_new_rows(load_result: dict) -> dict:
        return embed_new_rows_body(load_result)

    @task(retries=1)
    def verify_sync(embed_result: dict) -> dict:
        return verify_sync_body(embed_result)

    s3_key = extract_window()
    load_result = load_to_db(s3_key)
    embed_result = embed_new_rows(load_result)
    verify_sync(embed_result)


dag_object = patent_weekly_refresh()
