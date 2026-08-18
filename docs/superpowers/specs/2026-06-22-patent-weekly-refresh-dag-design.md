# 특허 데이터 주간 증분 갱신 Airflow DAG — 설계

> **목적**: 최초 대량 적재(`data/collect_from_bigquery.py` + `data/load_from_s3.py` 수동 실행) 이후, 신규 특허를 주기적으로 자동 반영하는 구조를 Airflow로 연습해보기 위한 설계다. **실제 venturescout 파이프라인에 연결하지 않으며, 실 BigQuery/S3/RDS에 대해 돌리지도 않는다** — 목표는 "구조와 코드가 맞다"는 것을 보여주는 것이지, 운영 배포가 아니다.

## 1. 배경

현재 특허 수집은 3개의 독립 스크립트로 나뉘어 있다:

| 스크립트 | 역할 | 증분 단위 |
|---|---|---|
| `data/collect_from_bigquery.py` | BigQuery(CPC G06Q30) → S3 raw JSON | **연 단위** (`get_collected_years()`가 S3에 이미 있는 연도를 스킵) |
| `data/load_from_s3.py` | S3 JSON → `documents`/`patent_claims`/`claim_limitations` upsert (임베딩 컬럼은 비워둠) | 없음(이미 멱등 — `ON CONFLICT DO NOTHING` + 사전 존재 조회) |
| `pipeline/indexer.py` (`PatentIndexer`) | `embedding IS NULL`인 행만 찾아 임베딩 후 컬럼 채움. `run()`은 bulk UPDATE 전후로 HNSW 인덱스를 drop→rebuild | 없음(이미 멱등 — NULL인 것만 처리) |

연 단위 증분(`collect_from_bigquery.py`)은 "한 번 크게 적재 후 주기적으로 갱신"에 맞지 않는다. 나머지 둘은 이미 범용적이라 그대로 재사용 가능하다.

## 2. 검토한 접근과 선택

| 접근 | 설명 | 채택 여부 |
|---|---|---|
| **A. 단일 선형 DAG, 4 task** | `extract_window → load_to_db → embed_new_rows → verify_sync`. Airflow의 `data_interval_start/end`를 BigQuery 윈도우로 사용 | **채택** |
| B. Dataset 기반 2-DAG 분리 | 적재 DAG가 Dataset을 emit, 임베딩 DAG가 그걸 구독해 자동 트리거. 임베딩을 별도 워커풀(GPU 등)에 돌릴 때 유용 | 보류 — 이번엔 과한 분리 |
| C. Dynamic Task Mapping으로 임베딩 병렬화 | 신규 row를 N개 배치로 나눠 `.expand()` | 비채택 — 주간 배치가 작아 이득이 거의 없고, task마다 모델을 재로드해야 해서 오히려 손해일 수 있음 |

A를 선택한 이유: 기존 3개 모듈의 호출 순서를 그대로 Airflow task 경계로 옮기는 것이 가장 명확하고, "구조가 맞다"는 것을 보여주는 목적에 가장 잘 맞는다.

## 3. DAG 구조

```
dag_id = "patent_weekly_refresh"
schedule = "0 3 * * 1"     # 매주 월요일 03:00
catchup = False            # 과거 적재는 수동으로 끝났다고 가정, 앞으로만 돈다
max_active_runs = 1        # 같은 주에 두 실행이 겹치지 않게

extract_window() >> load_to_db() >> embed_new_rows() >> verify_sync()
```

### 3-1. `extract_window`

- Airflow 컨텍스트의 `data_interval_start`/`data_interval_end`를 그대로 BigQuery `filing_date` 범위로 사용.
- `data/collect_from_bigquery.py`에 **새 함수** `fetch_window(client, start_date, end_date) -> str` 추가 (기존 `fetch_year`/`fetch_and_backup`/`get_collected_years`는 무변경).
  - S3 키: `raw/patents/patents_{start:%Y%m%d}_{end:%Y%m%d}.json` (윈도우 단위로 고정된 이름).
  - 해당 키가 S3에 이미 있으면 BigQuery 쿼리를 스킵하고 키만 반환(재시도 안전).
  - 없으면 기존 `fetch_year`와 동일한 CPC G06Q30 필터·필드로 쿼리하되 `filing_date BETWEEN start AND end`로 윈도우 한정, 결과를 S3에 업로드(신규 0건이어도 빈 배열 `[]`을 그대로 기록).
- retries=3, retry_delay=10분 (BigQuery 네트워크/쿼터 이슈 대비).
- XCom으로 S3 키(문자열) 반환.

### 3-2. `load_to_db`

- 입력: 위 S3 키.
- `data/load_from_s3.py`의 `load_from_s3()` + `save_to_db()`를 **무변경 그대로** 호출.
- retries=2, retry_delay=5분.
- 관찰용으로 `{"rows_loaded": len(rows)}`를 XCom 반환.

### 3-3. `embed_new_rows`

- `PatentIndexer.run()`을 쓰지 않고 **`run_claim_limitations()` + `run_documents()`를 직접 호출**한다.
  - `run()`은 매번 HNSW 인덱스를 drop→rebuild하는데, 이는 대량 백필 때 의미 있는 최적화이지 주간 소규모 배치에는 과한 비용이다.
  - 인덱스를 유지한 채 증분 UPDATE하면 신규 row가 적을 때는 오히려 더 빠르다.
  - **`pipeline/indexer.py`는 코드 한 줄도 바뀌지 않는다** — 호출 방식만 다르게 쓴다.
- retries=2, retry_delay=15분 (모델 로딩이 제일 무거운 구간).

### 3-4. `verify_sync`

- `indexer.verify_sync()` 호출. 반환된 `gate_pass`가 `False`면 **예외를 raise**해서 Airflow가 해당 실행을 실패로 표시하게 한다(현재 standalone 스크립트는 `logger.error`만 찍고 넘어가는데, DAG에서는 그러면 안 됨 — 알림/재시도 트리거가 안 걸림).
- retries=1 (재시도로 못 고치는 종류의 실패 — 불일치는 코드 버그를 의미하므로 빨리 드러나야 함).

## 4. 멱등성 요약

| task | 재실행 시 안전한 이유 |
|---|---|
| extract_window | S3 키 존재 여부로 BigQuery 쿼리 자체를 스킵 |
| load_to_db | 기존 `save_to_db()`의 `ON CONFLICT DO NOTHING` + 사전 존재 조회 |
| embed_new_rows | `embedding IS NULL`인 행만 처리 |
| verify_sync | 읽기 전용 |

신규 특허가 0건인 주에도 `fetch_window`가 빈 배열을 S3에 써두므로, `save_to_db([])`가 무동작으로 끝나는 기존 동작이 그대로 적용된다(추가 분기 불필요).

## 5. 변경 범위 (정확히 이 파일들만)

- `data/collect_from_bigquery.py` — `fetch_window()` 함수 **추가**(기존 함수 무변경)
- `dags/patent_weekly_refresh.py` — **신규 파일**. 4개 TaskFlow `@task`로 위 4단계 구성
- `data/load_from_s3.py`, `pipeline/indexer.py` — **무변경**

## 6. 실 인프라 없이 구조 검증하는 법

- `airflow dags list-import-errors` 또는 `python -c "from dags.patent_weekly_refresh import patent_weekly_refresh; patent_weekly_refresh()"`로 DAG가 파싱되고 task 의존성 그래프가 의도대로 잡히는지 확인.
- `unittest.mock`으로 `bigquery.Client`/`boto3.client('s3')`/DB 커넥션을 스텁 처리한 pytest로, 다음 분기만 검증(실 네트워크 호출 없음):
  - extract: S3에 키가 이미 있으면 BigQuery 쿼리를 스킵하는가
  - verify_sync: `gate_pass=False`일 때 task가 예외를 던지는가

## 7. 범위 밖 (Out of scope)

- 최초 대량 백필(이미 수동 스크립트로 완료된 것으로 간주) — 이 DAG는 증분 갱신만 다룬다.
- 접근 B(Dataset 기반 분리), C(Dynamic Task Mapping) — 검토했으나 이번 연습 범위에서는 채택하지 않음.
- 실제 AWS Bedrock/RDS/S3/BigQuery 자격증명 연결 및 운영 배포, docker-compose/Airflow 인프라 자체 구성.
- venturescout 라이브 파이프라인(`agents/graph.py`, `retrieval/`)과의 연동 — 이 DAG는 독립된 연습 코드다.
