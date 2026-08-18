# 특허 데이터 주간 증분 갱신 DAG — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `data/collect_from_bigquery.py`(연 단위)·`data/load_from_s3.py`·`pipeline/indexer.py`를 재사용해, 주간 단위로 신규 특허를 자동 적재·임베딩하는 Airflow DAG를 연습용으로 구현한다.

**Architecture:** Airflow 의존성이 없는 순수 함수(`dags/patent_weekly_refresh_tasks.py`)에 실제 로직을 두고, Airflow `@dag`/`@task` 데코레이터(`dags/patent_weekly_refresh.py`)는 그 함수들을 감싸서 순서·재시도 정책만 정의한다. 새 함수 `fetch_window()`만 `data/collect_from_bigquery.py`에 추가하고, 나머지 기존 파일은 import로만 재사용한다.

**Tech Stack:** Apache Airflow 2.10.0(TaskFlow API), boto3, google-cloud-bigquery, psycopg2/pgvector(기존 `pipeline/indexer.py` 경유), pytest + unittest.mock.

## Global Constraints

- 이 작업은 **연습/구조 검증 목적**이다 — 실 BigQuery/S3/RDS/AWS 자격증명을 사용하지 않는다.
- `data/load_from_s3.py`, `pipeline/indexer.py`는 **한 줄도 수정하지 않는다** — import해서 쓰기만 한다.
- 메인 `requirements.txt`/`venv/`에 `apache-airflow`를 추가하지 **않는다**. 호스트 Python이 3.14인데 Airflow 2.10.0은 3.14를 지원하지 않는다(constraints 파일이 3.14용으로 존재하지 않음). 대신 이 머신에 이미 받아둔 `apache/airflow:2.10.0` 도커 이미지(Python 3.12.5 내장, 확인됨)를 Airflow가 필요한 테스트의 실행 환경으로 쓴다.
- `dags/`는 `docker-compose.yml`이나 실 운영 배포에 연결하지 않는다. 새로 추가되는 파일은 `data/collect_from_bigquery.py`(함수 추가)와 `dags/` 디렉터리뿐이다.
- 기존 코드 스타일을 따른다: 한국어 주석, plain 함수 기반 pytest 테스트(클래스 없이)
- 참고 스펙: `docs/superpowers/specs/2026-06-22-patent-weekly-refresh-dag-design.md`

---

### Task 1: `fetch_window()` — BigQuery 윈도우 단위 조회 함수

**Files:**
- Modify: `data/collect_from_bigquery.py` (파일 끝에 함수 추가, 기존 `fetch_year`/`fetch_and_backup`/`get_collected_years`는 무변경)
- Test: `tests/test_collect_from_bigquery.py` (신규)

**Interfaces:**
- Produces: `fetch_window(client, start_date, end_date) -> str` — `client`는 `bigquery.Client` 인스턴스(또는 `.query()`를 가진 mock), `start_date`/`end_date`는 `.strftime()`을 지원하는 날짜 객체(`datetime.date`, `datetime.datetime`, Airflow의 `pendulum.DateTime` 모두 호환). 반환값은 S3 키 문자열.

이 task는 `boto3`/`botocore`만 필요하다(이미 `requirements.txt`에 있음) — 메인 venv에서 바로 테스트 가능, 도커 불필요.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_collect_from_bigquery.py` 생성:

```python
"""data/collect_from_bigquery.py의 fetch_window() 단위 테스트.
실 BigQuery/S3 네트워크 호출 없이 mock으로만 검증한다.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from data.collect_from_bigquery import fetch_window


def _not_found_error():
    return ClientError(
        error_response={"Error": {"Code": "404", "Message": "Not Found"}},
        operation_name="HeadObject",
    )


@patch("data.collect_from_bigquery.connect_s3")
def test_fetch_window_skips_query_when_key_exists(mock_connect_s3):
    mock_s3 = MagicMock()
    mock_connect_s3.return_value = mock_s3
    mock_s3.head_object.return_value = {}  # 예외 없음 = 이미 존재

    mock_bq_client = MagicMock()

    result = fetch_window(mock_bq_client, date(2026, 1, 5), date(2026, 1, 11))

    assert result == "raw/patents/patents_20260105_20260111.json"
    mock_bq_client.query.assert_not_called()
    mock_s3.put_object.assert_not_called()


@patch("data.collect_from_bigquery.connect_s3")
def test_fetch_window_queries_when_key_absent(mock_connect_s3):
    mock_s3 = MagicMock()
    mock_connect_s3.return_value = mock_s3
    mock_s3.head_object.side_effect = _not_found_error()

    mock_bq_client = MagicMock()
    mock_bq_client.query.return_value.result.return_value = []

    result = fetch_window(mock_bq_client, date(2026, 1, 5), date(2026, 1, 11))

    assert result == "raw/patents/patents_20260105_20260111.json"
    mock_bq_client.query.assert_called_once()
    mock_s3.put_object.assert_called_once()
    assert mock_s3.put_object.call_args.kwargs["Key"] == "raw/patents/patents_20260105_20260111.json"
```

- [x] **Step 2: 실패 확인**

Run: `cd "C:\Users\Dell3571\Desktop\venturescout" && source venv/Scripts/activate && python -m pytest tests/test_collect_from_bigquery.py -v`
Expected: `ImportError: cannot import name 'fetch_window'` (아직 정의 안 됨)

- [x] **Step 3: `fetch_window()` 구현**

`data/collect_from_bigquery.py` 맨 위 import 블록에 한 줄 추가:

```python
from botocore.exceptions import ClientError
```

파일 끝(`fetch_and_backup()` 함수와 `if __name__ == "__main__":` 블록 사이)에 추가:

```python
def fetch_window(client, start_date, end_date):
    """주(week) 단위 윈도우를 BigQuery에서 조회해 S3에 저장한다.
    윈도우 키가 이미 S3에 있으면 쿼리를 스킵하고 그 키를 반환한다(재시도 안전).
    """
    start = start_date.strftime('%Y%m%d')
    end = end_date.strftime('%Y%m%d')
    filename = f"raw/patents/patents_{start}_{end}.json"

    s3 = connect_s3()
    try:
        s3.head_object(Bucket=os.getenv('S3_BUCKET_NAME'), Key=filename)
        print(f"[{start}~{end}] ⏭ 이미 S3에 존재 — BigQuery 쿼리 스킵: {filename}")
        return filename
    except ClientError as exc:
        if exc.response['Error']['Code'] != '404':
            raise

    query = f"""
    SELECT DISTINCT
    pub.publication_number,
    (SELECT text FROM UNNEST(pub.title_localized)
    WHERE language = 'en' LIMIT 1) AS title,
    (SELECT text FROM UNNEST(pub.abstract_localized)
    WHERE language = 'en' LIMIT 1) AS abstract,
    pub.filing_date,
    pub.grant_date,
    pub.assignee_harmonized[SAFE_OFFSET(0)].name AS assignee,
    (SELECT cpc.code FROM UNNEST(pub.cpc) AS cpc
    WHERE cpc.code LIKE 'G06Q30%' LIMIT 1) AS cpc_code,
    (SELECT claim.text FROM UNNEST(pub.claims_localized) AS claim
    WHERE claim.language = 'en' LIMIT 1) AS claim_text
    FROM `patents-public-data.patents.publications` AS pub
    WHERE EXISTS (
    SELECT 1 FROM UNNEST(pub.cpc) AS cpc
    WHERE cpc.code LIKE 'G06Q30%'
    )
    AND pub.country_code = 'US'
    AND pub.filing_date >= {start}
    AND pub.filing_date <= {end}
    """

    print(f"[{start}~{end}] BigQuery 쿼리 실행 중...")
    rows = [dict(row) for row in client.query(query).result()]
    print(f"[{start}~{end}] ✅ {len(rows)}건 가져옴")

    s3.put_object(
        Bucket=os.getenv('S3_BUCKET_NAME'),
        Key=filename,
        Body=json.dumps(rows, default=str, ensure_ascii=False).encode('utf-8'),
        ContentType='application/json'
    )
    print(f"[{start}~{end}] ✅ S3 저장 완료: {filename}")
    return filename
```

- [x] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_collect_from_bigquery.py -v`
Expected: `2 passed`

---

### Task 2: task 본체 순수 함수 — `dags/patent_weekly_refresh_tasks.py`

**Files:**
- Create: `dags/__init__.py` (빈 파일)
- Create: `dags/patent_weekly_refresh_tasks.py`
- Test: `tests/test_patent_weekly_refresh_tasks.py` (신규)

**Interfaces:**
- Consumes: `data.collect_from_bigquery.fetch_window(client, start_date, end_date) -> str` (Task 1), `data.load_from_s3.load_from_s3(filename) -> list[dict]` / `save_to_db(rows) -> None`, `pipeline.indexer.PatentIndexer().run_claim_limitations() -> dict` / `.run_documents() -> dict` / `.verify_sync() -> dict`(`gate_pass: bool` 포함)
- Produces: `extract_window_body(start_date, end_date) -> str`, `load_to_db_body(s3_key: str) -> dict`, `embed_new_rows_body(load_result: dict) -> dict`, `verify_sync_body(embed_result: dict) -> dict` — Task 3가 이 4개 함수를 그대로 가져다 `@task`로 감싼다.

이 task는 Airflow를 import하지 않는다(모든 무거운 의존성은 함수 내부 지역 import) — 메인 venv에서 테스트 가능.

- [x] **Step 1: 실패하는 테스트 작성**

`dags/__init__.py` 생성 (빈 파일).

`tests/test_patent_weekly_refresh_tasks.py` 생성:

```python
"""dags/patent_weekly_refresh_tasks.py 단위 테스트.
Airflow·실 BigQuery/S3/DB 없이, mock으로만 분기를 검증한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dags.patent_weekly_refresh_tasks import verify_sync_body


@patch("pipeline.indexer.PatentIndexer")
def test_verify_sync_body_raises_when_gate_fails(mock_indexer_cls):
    mock_indexer = MagicMock()
    mock_indexer.verify_sync.return_value = {"gate_pass": False, "missing": 3}
    mock_indexer_cls.return_value = mock_indexer

    with pytest.raises(RuntimeError, match="D3 게이트 실패"):
        verify_sync_body({"claim_limitations": {"indexed": 0}, "documents": {"indexed": 0}})


@patch("pipeline.indexer.PatentIndexer")
def test_verify_sync_body_passes_when_gate_ok(mock_indexer_cls):
    mock_indexer = MagicMock()
    mock_indexer.verify_sync.return_value = {"gate_pass": True, "missing": 0}
    mock_indexer_cls.return_value = mock_indexer

    result = verify_sync_body({"claim_limitations": {"indexed": 5}, "documents": {"indexed": 5}})

    assert result == {"gate_pass": True, "missing": 0}
```

- [x] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_patent_weekly_refresh_tasks.py -v`
Expected: `ModuleNotFoundError: No module named 'dags.patent_weekly_refresh_tasks'`

- [x] **Step 3: 구현**

`dags/patent_weekly_refresh_tasks.py` 생성:

```python
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
```

- [x] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_patent_weekly_refresh_tasks.py -v`
Expected: `2 passed`

---

### Task 3: Airflow DAG 와이어링 — `dags/patent_weekly_refresh.py`

**Files:**
- Create: `dags/patent_weekly_refresh.py`
- Test: `tests/test_patent_weekly_refresh_dag.py` (신규, **`apache/airflow:2.10.0` 컨테이너 안에서만 실행**)

**Interfaces:**
- Consumes: Task 2의 `extract_window_body`/`load_to_db_body`/`embed_new_rows_body`/`verify_sync_body`
- Produces: 모듈 레벨 `dag_object` (Airflow `DAG` 인스턴스) — task_id 4개를 가진 선형 그래프

호스트 Python은 3.14라 Airflow 2.10.0을 못 깐다(constraints 파일이 3.14를 지원 안 함). 이 머신에 이미 있는 `apache/airflow:2.10.0` 이미지(Python 3.12.5)를 검증 환경으로 쓴다 — 메인 `venv`/`requirements.txt`는 건드리지 않는다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_patent_weekly_refresh_dag.py` 생성:

```python
"""patent_weekly_refresh DAG 와이어링 테스트.
Airflow가 설치된 환경(예: apache/airflow:2.10.0 컨테이너)에서만 실행한다.
실 BigQuery/S3/DB 연결 없이 DAG 객체의 구조만 검사한다.
"""
from __future__ import annotations


def test_dag_has_expected_linear_task_order():
    from dags.patent_weekly_refresh import dag_object

    assert list(dag_object.task_dict.keys()) == [
        "extract_window", "load_to_db", "embed_new_rows", "verify_sync",
    ]

    def downstream_ids(task_id):
        return [t.task_id for t in dag_object.task_dict[task_id].downstream_list]

    assert downstream_ids("extract_window") == ["load_to_db"]
    assert downstream_ids("load_to_db") == ["embed_new_rows"]
    assert downstream_ids("embed_new_rows") == ["verify_sync"]
    assert downstream_ids("verify_sync") == []


def test_dag_schedule_and_safety_settings():
    from dags.patent_weekly_refresh import dag_object

    assert dag_object.schedule_interval == "0 3 * * 1"
    assert dag_object.catchup is False
    assert dag_object.max_active_runs == 1
```

- [x] **Step 2: 실패 확인**

Run (Git Bash에서는 `MSYS_NO_PATHCONV=1`을 붙여야 `-w` 경로가 Windows 경로로 잘못 변환되지 않음; `--user`는 이 이미지의 가상환경에서 안 먹혀서 제외):
```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd):/opt/airflow/project" -w /opt/airflow/project \
  --entrypoint bash apache/airflow:2.10.0 \
  -c "pip install --quiet pytest && python -m pytest tests/test_patent_weekly_refresh_dag.py -v"
```
Expected: `ModuleNotFoundError: No module named 'dags.patent_weekly_refresh'`

- [x] **Step 3: 구현**

`dags/patent_weekly_refresh.py` 생성:

```python
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
```

- [x] **Step 4: 통과 확인**

Run: 위 Step 2와 동일한 docker 명령
Expected: `2 passed`

---

## 완료 후 확인 (Acceptance)

- [x] `python -m pytest tests/test_collect_from_bigquery.py tests/test_patent_weekly_refresh_tasks.py -v` (메인 venv) → 4 passed
- [x] `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd):/opt/airflow/project" -w /opt/airflow/project --entrypoint bash apache/airflow:2.10.0 -c "pip install --quiet pytest && python -m pytest tests/test_patent_weekly_refresh_dag.py -v"` → 2 passed
- [x] `data/load_from_s3.py`, `pipeline/indexer.py`에 `git diff`가 없는지 확인 (`git diff --stat data/load_from_s3.py pipeline/indexer.py` → 출력 없음, 확인됨)
- [x] `docker-compose.yml`에 변경 없음 (확인됨)
