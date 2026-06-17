-- VentureScout Tier 0 — PostgreSQL 초기화 (9 tables)
-- 원칙: 계약 strict / output_json JSONB / pgvector 단일 스토어
-- 임베딩 차원: 768 (PatentSBERTa·KorPatBERT). e5 폴백은 1024로 변경.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ① 구조화된 입력
-- 실제 데이터 전환 지점:
-- 업로드 파일/폼 입력을 파싱한 raw_input과 Structuring 에이전트 결과를 여기에 저장한다.
CREATE TABLE ideas (
    idea_id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_input          text,
    title              varchar(300),
    idea_type          varchar(100),
    target_customer    text,
    problem_statement  text,
    solution_summary   text,
    business_model_hint text,
    technical_elements jsonb,
    patent_keywords    jsonb,
    user_confirmed     boolean DEFAULT false,
    created_at         timestamptz DEFAULT now()
);

-- ② 분석 job (FastAPI 비동기 + 스트리밍)
CREATE TABLE analysis_jobs (
    job_id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    idea_id          uuid REFERENCES ideas(idea_id),
    status           varchar(20),           -- pending|running|done|failed
    current_stage    varchar(50),
    progress_pct     int DEFAULT 0,
    decision         varchar(20),           -- (권장) go|pivot|kill|more_research — 보드 헤드라인
    decision_summary text,                  -- (권장)
    started_at       timestamptz,
    finished_at      timestamptz
);

-- ③ Hypothesis Ledger
CREATE TABLE hypotheses (
    hypothesis_id   uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id          uuid REFERENCES analysis_jobs(job_id),
    idea_id         uuid REFERENCES ideas(idea_id),
    code            varchar(10),            -- "H5"
    axis            varchar(30),            -- 고객문제|경쟁|수익|기술|IP
    statement       text,
    confidence      varchar(10),            -- high|mid|low
    next_validation text
);

-- ④ 근거 출처 통합 + 임베딩
-- 실제 데이터 전환 지점:
-- 특허, 웹 문서, 인터뷰 메모, 가격 자료, 기술 벤치마크 문서를 여기에 적재한다.
-- clean_text는 검색 대상이고 embedding은 pgvector 검색 대상이다.
CREATE TABLE documents (
    document_id       uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type       varchar(30),          -- patent|seed_review|seed_competitor|seed_pricing|web
    ext_id            varchar(100),         -- 특허 publication_number 등
    title             varchar(500),
    canonical_url     text,
    clean_text        text,
    embedding         vector(768),
    meta              jsonb,
    reliability_score numeric,
    freshness_score   numeric,
    is_user_provided  boolean DEFAULT false
);

-- ⑤ ★ 그라운딩 원자
-- 실제 데이터 전환 지점:
-- documents에서 특정 hypothesis에 대해 뽑은 근거 조각을 저장한다.
-- 모든 AgentRun.grounded_on은 이 evidence_id만 인용해야 한다.
CREATE TABLE evidence_items (
    evidence_id       uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id            uuid REFERENCES analysis_jobs(job_id),
    hypothesis_id     uuid REFERENCES hypotheses(hypothesis_id),
    document_id       uuid REFERENCES documents(document_id),
    source_type       varchar(30),
    evidence_text     text,
    stance            varchar(12),          -- supports|contradicts|neutral
    relevance_score   numeric,
    reliability_score numeric
);

-- ⑥ 에이전트 출력 공통 envelope
CREATE TABLE agent_runs (
    agent_run_id      uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id            uuid REFERENCES analysis_jobs(job_id),
    hypothesis_id     uuid REFERENCES hypotheses(hypothesis_id),
    agent_name        varchar(20),          -- market|competitor|tech|ip|bm|critic
    model_name        varchar(100),
    depth             varchar(10),          -- full|light
    confidence        varchar(10),
    grounded_on       jsonb,                -- evidence_id 배열 (required)
    output_json       jsonb,                -- ★ 분석 본문(느슨)
    target_run_id     uuid,                 -- (권장) critic이 가리키는 대상 run — ON/OFF 추적
    groundedness_score numeric,
    overclaim_flag    boolean,
    status            varchar(20)
);

-- ───────── 시그니처(⑤ IP) 3 ─────────

CREATE TABLE patent_claims (
    claim_id       uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id    uuid REFERENCES documents(document_id),
    claim_no       int,
    claim_text     text,
    is_independent boolean,
    parent_claim_no int
);

CREATE TABLE claim_limitations (
    limitation_id    uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_id         uuid REFERENCES patent_claims(claim_id),
    limitation_order int,
    normalized_text  text,
    embedding        vector(768)            -- 짧아 청크 불필요
);

-- 실제 데이터 전환 지점:
-- Track B 검색기가 plan_technical_element와 claim_limitations를 매칭한 후보를 저장한다.
-- 이 테이블은 법적 판단이 아니라 IP 에이전트가 읽을 후보 목록이다.
CREATE TABLE ip_overlap_candidates (
    candidate_id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id                uuid REFERENCES analysis_jobs(job_id),
    hypothesis_id         uuid REFERENCES hypotheses(hypothesis_id),
    limitation_id         uuid REFERENCES claim_limitations(limitation_id),
    evidence_id           uuid REFERENCES evidence_items(evidence_id),
    plan_technical_element text,
    lexical_score         numeric,
    similarity_score      numeric,
    hybrid_score          numeric,
    rank                  int
);

-- ───────── 인덱스 (단일 스토어) ─────────
CREATE INDEX idx_doc_embed  ON documents         USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_lim_embed  ON claim_limitations USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_doc_text   ON documents         USING gin (to_tsvector('simple', coalesce(clean_text, '')));
CREATE INDEX idx_lim_text   ON claim_limitations USING gin (to_tsvector('simple', coalesce(normalized_text, '')));
CREATE INDEX idx_evi_hyp    ON evidence_items (hypothesis_id);
CREATE INDEX idx_run_job    ON agent_runs (job_id);
