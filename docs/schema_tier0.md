# VentureScout — Tier 0 스키마 (재설계)

> 기존 22-테이블 DBML은 **목표 ERD(부록)**로 보관. 실제 Day 1 구현은 아래 **9개(기본 6 + 시그니처 3)**로 시작.
> 설계 3원칙:
> 1. **계약 필드는 단단히, output_json은 느슨하게** — cross-team 필드(evidence_id·grounded_on·confidence·depth·stance)만 strict, 분석 본문은 `output_json` 하나로 흘림.
> 2. **pgvector 단일 스토어** — 별도 벡터DB 없음. 임베딩은 Postgres 컬럼. (Chroma 동기화 문제 소거)
> 3. **입력 = `ideas.raw_input` 텍스트** — 파일 업로드·OCR·페이지·섹션 5종 테이블은 Tier 3(천장)로 분리.

---

## 1. Tier 0 코어 — DBML (dbdiagram.io 붙여넣기용)

```dbml
// VentureScout Tier 0 — 9 tables (기본 6 + 시그니처 3)
// 원칙: 계약 strict / output_json / pgvector 단일 스토어

Table ideas {                       // ① Structuring 출력 = 입력 단일 진입점
  idea_id uuid [pk]
  raw_input text                    // 사용자 계획서형 텍스트(원본)
  title varchar
  idea_type varchar
  target_customer text
  problem_statement text
  solution_summary text
  business_model_hint text
  technical_elements json           // ["STT","요약",...] — ⑤ 검색 입력
  patent_keywords json
  user_confirmed boolean            // ① 출력 사용자 확인(그라운딩 방어)
  created_at timestamp
}

Table analysis_jobs {               // FastAPI 비동기 job + 스트리밍 상태
  job_id uuid [pk]
  idea_id uuid
  status varchar                    // pending|running|done|failed
  current_stage varchar             // 진행 스트리밍 렌더용
  progress_pct int
  started_at timestamp
  finished_at timestamp
}

Table hypotheses {                  // Hypothesis Ledger (H1~H5)
  hypothesis_id uuid [pk]
  job_id uuid
  idea_id uuid
  code varchar                      // "H5"
  axis varchar                      // 고객문제|경쟁|수익|기술|IP
  statement text
  confidence varchar                // high|mid|low (계약 enum)
  next_validation text
}

Table documents {                   // 근거 출처 통합(특허+시드+웹) + 임베딩
  document_id uuid [pk]
  source_type varchar               // patent|seed_review|seed_competitor|seed_pricing|web
  ext_id varchar                    // 특허 publication_number 등(가독용), 시드는 null
  title varchar
  canonical_url varchar
  clean_text text                   // tsvector 대상
  embedding vector                  // 768d. Tier0 문서단위(긴 문서 청크는 Tier1)
  meta json                         // 출처별 자유(assignee, dates, price 등)
  reliability_score decimal         // high(특허)/mid(시드)/low(웹)
  freshness_score decimal
  is_user_provided boolean
}

Table evidence_items {              // ★ 그라운딩 원자 — 모든 주장이 이걸 인용
  evidence_id uuid [pk]
  job_id uuid
  hypothesis_id uuid
  document_id uuid
  source_type varchar
  evidence_text text
  stance varchar                    // supports|contradicts|neutral (가설별 태깅)
  relevance_score decimal
  reliability_score decimal
}

Table agent_runs {                  // ②③④⑤⑥⑦ 출력 공통 envelope
  agent_run_id uuid [pk]
  job_id uuid
  hypothesis_id uuid
  agent_name varchar                // market|competitor|tech|ip|bm|critic
  model_name varchar
  depth varchar                     // full|light (혼합 스코프, 계약)
  confidence varchar                // high|mid|low (계약)
  grounded_on json                  // evidence_id 배열 (계약, required)
  output_json json                  // ★ 분석 본문 전부(느슨) — 컬럼으로 박지 않음
  groundedness_score decimal
  overclaim_flag boolean
  status varchar
}

// ───────── 시그니처(⑤ IP) 3 테이블 ─────────

Table patent_claims {               // 특허 청구항(그룹·표시 단위)
  claim_id uuid [pk]
  document_id uuid                  // 특허 = documents 한 행
  claim_no int
  claim_text text
  is_independent boolean
  parent_claim_no int
}

Table claim_limitations {           // 청구항 구성요소 = 임베딩/검색 단위
  limitation_id uuid [pk]
  claim_id uuid
  limitation_order int
  normalized_text text              // tsvector 대상
  embedding vector                  // 768d — 시그니처 벡터 검색 단위(짧아 청크 불필요)
}

Table ip_overlap_candidates {       // 기계(B) produce → ⑤ 에이전트(C) read
  candidate_id uuid [pk]
  job_id uuid
  hypothesis_id uuid
  limitation_id uuid
  evidence_id uuid
  plan_technical_element text       // 아이디어 기술요소
  lexical_score decimal
  similarity_score decimal
  hybrid_score decimal
  rank int
}

// ───────── Relationships ─────────
Ref: ideas.idea_id < analysis_jobs.idea_id
Ref: ideas.idea_id < hypotheses.idea_id
Ref: analysis_jobs.job_id < hypotheses.job_id
Ref: analysis_jobs.job_id < evidence_items.job_id
Ref: hypotheses.hypothesis_id < evidence_items.hypothesis_id
Ref: documents.document_id < evidence_items.document_id
Ref: analysis_jobs.job_id < agent_runs.job_id
Ref: hypotheses.hypothesis_id < agent_runs.hypothesis_id
Ref: documents.document_id < patent_claims.document_id
Ref: patent_claims.claim_id < claim_limitations.claim_id
Ref: analysis_jobs.job_id < ip_overlap_candidates.job_id
Ref: hypotheses.hypothesis_id < ip_overlap_candidates.hypothesis_id
Ref: claim_limitations.limitation_id < ip_overlap_candidates.limitation_id
Ref: evidence_items.evidence_id < ip_overlap_candidates.evidence_id
```

---

## 2. 코드 계약 (C가 Day 1에 레포에 올림)

DB 테이블과 1:1로 매핑되지만, **런타임/tool이 주고받는 계약**. strict 필드만 pydantic 검증하고, 분석 본문은 `agent_runs.output_json` 안에 둠.

```python
from typing import TypedDict, Literal, Optional
from pydantic import BaseModel, Field

Confidence = Literal["high", "mid", "low"]
Stance     = Literal["supports", "contradicts", "neutral"]
Depth      = Literal["full", "light"]

# 그라운딩 원자 (→ evidence_items)
class EvidenceItem(BaseModel):
    evidence_id: str
    hypothesis_id: str
    document_id: str
    source_type: str
    evidence_text: str
    stance: Stance
    reliability_score: float

# 가설 (→ hypotheses)
class Hypothesis(BaseModel):
    hypothesis_id: str
    code: str                       # "H5"
    axis: str                       # 고객문제|경쟁|수익|기술|IP
    statement: str
    confidence: Confidence
    next_validation: str

# ★ 에이전트 출력 공통 envelope (→ agent_runs)
class AgentRun(BaseModel):
    agent_run_id: str | None = None
    job_id: str
    agent_name: str                 # market|competitor|tech|ip|bm|critic
    hypothesis_id: str
    grounded_on: list[str]          # evidence_id (required; 비면 검증 실패)
    confidence: Confidence          # 계약
    depth: Depth                    # full|light (혼합 스코프)
    output_json: dict = Field(default_factory=dict)   # 분석 본문 전부(느슨)

# 시그니처 기계 출력: B가 produce, ⑤가 read (→ ip_overlap_candidates)
class IPOverlapCandidate(BaseModel):
    candidate_id: str
    job_id: str
    hypothesis_id: str
    limitation_id: str
    evidence_id: str
    plan_technical_element: str
    lexical_score: float
    similarity_score: float
    hybrid_score: float
    rank: int

# LangGraph 런타임 컨테이너 (State, 느슨)
class VentureScoutState(TypedDict):
    idea: dict
    hypotheses: list[Hypothesis]
    evidence_items: dict[str, EvidenceItem]  # evidence_id → item
    agent_runs: list[AgentRun]               # ②③④⑤⑥⑦ 누적
    ip_overlap_candidates: list[IPOverlapCandidate]
    critic: dict                             # ⑦
    final_report: str
```

> **계약/느슨 경계**: strict = `evidence_id·grounded_on·confidence·stance·depth`(이게 통합 면). 느슨 = `agent_runs.output_json`(에이전트별 자유, 프롬프트 바뀌어도 마이그레이션 없음).

---

## 3. 단일 스토어 인덱스 (pgvector + tsvector)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- 벡터 검색 (시그니처 + 시드)
ALTER TABLE claim_limitations ALTER COLUMN embedding TYPE vector(768);
ALTER TABLE documents         ALTER COLUMN embedding TYPE vector(768);
CREATE INDEX ON claim_limitations USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON documents         USING hnsw (embedding vector_cosine_ops);

-- 키워드(lexical) 검색
CREATE INDEX ON claim_limitations USING gin (to_tsvector('simple', normalized_text));
CREATE INDEX ON documents         USING gin (to_tsvector('simple', clean_text));
```

> 임베딩 단위 = **limitation**(짧음 → 512토큰 청크/평균풀링 불필요). 긴 문서 청킹(`document_chunks`)은 Tier 1에서만.

---

## 4. Tier 승격 맵 (나머지 13 테이블은 나중에)

| 테이블 | 현재 위치 | 승격 시점 | 사유 |
|---|---|---|---|
| `document_chunks` | `documents.clean_text/embedding` 통합 | **Tier 1** | 긴 문서 등장 시 청크 분리 |
| `agent_claims` | `agent_runs.output_json` 내 배열 | **Tier 2** | claim 단위 평가 쿼리 필요해질 때 |
| `critic_objections` | critic의 `agent_runs.output_json` | **Tier 2** | Critic ON/OFF 정량화 쿼리 |
| `final_decisions` | critic의 `agent_runs.output_json` | **Tier 1** | 보드 표시는 output_json에서, 이력관리 필요 시 테이블 |
| `validation_experiments` | `final_decisions` json | **Tier 1** | 실험 추적 UI 붙일 때 |
| `ip_analysis_results` | ⑤의 `agent_runs.output_json` | **Tier 1** | IP 카드 이력 쿼리 필요 시 |
| `patent_cpc_codes` | `documents.meta` json | **Tier 1** | CPC 필터를 인덱스로 돌릴 때 |
| `business_plan_files/pages/sections/claims/entities` | — | **Tier 3** | 파일 업로드·OCR(천장 기능) |
| `uspto_import_batches/raw_files` | 수집 스크립트 로그 | **Tier 3** | 증분 적재 운영화할 때 |

> 규칙: **JSON 안에 먼저 넣고, 쿼리가 필요해지면 그때 테이블로 굳힌다.** 테이블 수 = 통합 비용이지 야망이 아님.

---

## 5. 누가 무엇을 produce/consume (역할 매핑)

| 테이블 | produce | consume |
|---|---|---|
| `ideas` | **C**(① Structuring) | 전 에이전트 |
| `hypotheses` | **C**(① Structuring) | ②~⑦ |
| `documents` | **A**(특허 적재·시드 로드) | B(임베딩·검색) |
| `patent_claims`·`claim_limitations` | **A**(파싱·limitation 분해) | B(임베딩·검색) |
| `documents.embedding`·`claim_limitations.embedding` | **B**(임베딩, 모델=PatentSBERTa/KorPatBERT) | 검색 tool |
| `ip_overlap_candidates` | **B**(하이브리드 검색) | **C**(⑤ IP 판정) |
| `evidence_items` | **B**(검색 시 stance 태깅) | C(②④⑤⑦)·B(②③)·D(⑥) |
| `agent_runs` | 에이전트 소유자(②③→B, ①④⑤⑦→C, ⑥→D) | D(보드·평가) |
| `analysis_jobs` | **D**(FastAPI) | Chainlit·평가 |

> **mock 병렬**: C가 위 계약(§2)을 Day 1에 올리면, A는 `documents`/`claim_limitations`를, B는 검색 tool이 `IPOverlapCandidate`·`EvidenceItem`을 반환하도록, D는 평가 하네스를 — 전부 실데이터 없이 mock으로 동시에 짠다. ⑤ 에이전트도 `IPOverlapCandidate` mock만 있으면 판정 프롬프트 완성.

---

## 6. Day 1 체크리스트

1. **C** — `EvidenceItem·Hypothesis·AgentRun·IPOverlapCandidate·State` 코드를 레포에 commit(§2)
2. **A** — `ideas·documents·patent_claims·claim_limitations` DDL + 적재 스텁
3. **B** — `vector_search() → list[IPOverlapCandidate]`, `retrieve() → list[EvidenceItem]` tool 시그니처 확정(mock 반환)
4. **D** — `analysis_jobs` + FastAPI 스트리밍 이벤트 포맷 + 평가 하네스 mock
5. 데이터 소스(영어/한국어) 확정 → `documents.source_type`·임베딩 모델 결정

> 이 9개 + 계약 코드가 Tier 0 E2E(②⑤ 풀 + ③④⑥ 경량 + ⑦)를 굴리는 최소 집합. 나머지 13개는 JSON에 담아두다 Tier 1~3에서 승격.
