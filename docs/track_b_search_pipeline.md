# Track B — 검색·임베딩 파이프라인 + 에이전트 ②③ (구현 노트)

> Day 1 계약(`shared/contracts.py`, `shared/state.py`, `db/init.sql`, `docs/schema_tier0.md`)에
> 맞춰 구현한 B 트랙의 설계 근거와 동작 방식을 정리한다.
> 계약 자체는 이 문서가 아니라 `docs/schema_tier0.md` §2를 따른다.

---

## 1. 구현 위치

```
config.py                 ← DATA_SOURCE(USPTO/KIPRIS) 기반 임베딩 모델·DSN 분기
pipeline/
├── chunker.py            ← 512토큰 분할 (긴 문서용)
├── embedder.py           ← PatentSBERTa / KorPatBERT 래퍼
├── indexer.py            ← pgvector 적재 + D3 게이트(verify_sync)
└── persistence.py        ← evidence_items / agent_runs / ip_overlap_candidates 적재
search/
├── hybrid.py             ← pgvector + tsvector 하이브리드 검색
└── reranker.py           ← 4축 rerank
retrieval/
├── tools.py              ← retrieve() / vector_search() — shared.contracts 반환
└── agents.py             ← run_market_agent() / run_competitor_agent()
tests/test_track_b.py     ← chunker / reranker / D3 게이트 / persistence 단위 테스트
```

`agents/graph.py`의 `market_node`/`competitor_node`가 `retrieval/agents.py`를 호출한다.

---

## 2. 검색 설계

### 2-1. 하이브리드 점수

```
hybrid_score = 0.6 × vector_score + 0.4 × keyword_score
```

- `vector_score`: `1 - cosine_distance(query_vec, embedding)` (pgvector, HNSW)
- `keyword_score`: PostgreSQL `ts_rank()` (tsvector)

특허 청구항·시드 문서에 자주 등장하는 기술 용어(예: "음성 인식", "STT")는
의미 공간상 멀어도 키워드로는 정확히 매칭되는 경우가 있어 두 점수를 합산한다.

### 2-2. Rerank 4축

```
rerank_score = 0.4 × relevance
             + 0.3 × reliability
             + 0.1 × freshness
             + 0.2 × contradiction_value
```

| 축 | 의미 | 계산 방법 |
|---|---|---|
| `relevance` | 하이브리드 검색 점수 | `hybrid_score` 그대로 |
| `reliability` | 소스 신뢰도 | 특허 0.9 · 시드 0.6 · 뉴스 0.4 (`config.source_reliability`) |
| `freshness` | 최신성 | 출원/게시일 기준 10년 선형 감쇠 |
| `contradiction_value` | 반박 근거 가중치 | contradicting=1.0, supporting=0.2 |

`contradiction_value`가 핵심이다. 반박 근거를 의도적으로 상위에 올려야
Evidence Board의 "찬반 충돌" 신호가 살아나고, ⑦ Critic의 낙관 편향 제거 먹잇감이 만들어진다.
`retrieve()`는 `prefer_contradicting=True`로 호출한다.

### 2-3. D3 게이트: 동기화 검증

```python
from pipeline.indexer import PatentIndexer

result = PatentIndexer().verify_sync()
# {"total_independent_claims": N, "total_embedded": N, "missing": 0,
#  "sync_rate": 1.0, "gate_pass": True}
```

`gate_pass: False`면 ⑤ IP 에이전트(`vector_search` 소비자)의 결과가 조용히 망가진다.
임베딩 파이프라인(`PatentIndexer().run()`) 완료 직후 반드시 실행한다.

---

## 3. retrieval/tools.py — Tier0 단순화

`shared/contracts.py`의 `EvidenceItem.evidence_id`는 `evidence_items` 테이블의 PK다.
하지만 Tier0에는 `job_id`/`hypothesis_id` 오케스트레이션이 아직 없어
`evidence_items` 행을 실제로 적재하지 않는다.

대신 `retrieve()`/`vector_search()`는 **`evidence_id = documents.document_id`**로
반환한다. `pipeline/persistence.py`(`create_evidence_item` 등)는 포팅되어 있고
`tests/test_track_b.py::TestPersistence`로 단위 테스트되어 있으나, `agents/graph.py`에서는
아직 호출되지 않는다 — Tier1에서 `job_id`/`hypothesis_id`가 `VentureScoutState`에
실제로 채워지면 `retrieval/agents.py`에서 `persist_agent_output()`을 호출해
`evidence_items`/`agent_runs`를 적재하도록 연결할 것.

---

## 4. 주요 설계 결정 (Why)

**왜 Chroma가 아니라 pgvector인가**

별도 벡터 DB는 PostgreSQL과의 동기화 스크립트가 추가로 필요하고,
`document_id`/`claim_id` 매핑이 깨지면 디버깅이 복잡해진다.
pgvector는 PostgreSQL 안에서 동작하므로 항상 동기화 상태다.

**왜 독립항(independent claim)만 임베딩하는가**

특허 침해 판단 실무에서 핵심은 독립항이다. 종속항은 독립항을 한정하는 역할이라
독립항을 포함한다. 전체 청구항을 임베딩하면 토큰 비용·저장 공간이 늘지만
검색 품질은 크게 달라지지 않는다.

**왜 IVFFlat이 아니라 HNSW 인덱스인가**

데이터 규모가 수만 건 이하라면 HNSW가 IVFFlat보다 recall이 높다.
IVFFlat은 수백만 건 이상에서 속도 이점이 생기는 인덱스다.

---

## 5. 환경 변수 (이 레포 기준)

`.env.example` 참고. 이전 `proj/` 버전과 변수명이 다르다.

```bash
DATA_SOURCE=USPTO              # USPTO -> PatentSBERTa / KIPRIS -> KorPatBERT (config.py)

POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=venturescout
POSTGRES_USER=vs
POSTGRES_PASSWORD=...
# 또는 DATABASE_URL=postgresql://... 하나로 대체 가능 (config.db_dsn)

AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20240620-v1:0
EMBEDDING_DIM=768
```

---

## 6. 빠른 시작

```bash
pip install -r requirements.txt

# 스키마 적용
psql $DATABASE_URL -f db/init.sql

# 임베딩 파이프라인 실행 (A의 patent_claims/documents 적재 후)
python -c "from pipeline.indexer import PatentIndexer; PatentIndexer().run()"

# D3 게이트 확인
python -c "
from pipeline.indexer import PatentIndexer
r = PatentIndexer().verify_sync()
print('GATE PASS' if r['gate_pass'] else f'FAIL: {r[\"missing\"]}건 누락')
"

# 테스트
pytest tests/ -v
```
