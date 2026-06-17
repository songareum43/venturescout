-- VentureScout — PostgreSQL 스키마 (9 tables)
-- 원칙: 계약 strict / payload JSONB / pgvector 단일 스토어
-- 임베딩 차원: 768 (PatentSBERTa·KorPatBERT). e5 폴백은 1024로 변경.
--
-- 이 파일은 AWS RDS(your-db-host.ap-northeast-1.rds.amazonaws.com)에
-- 실제로 적용되어 있는 스키마를 읽기 전용으로 조회해 그대로 옮긴 것이다(db/introspect_schema.py).
-- RDS는 이 파일로 재생성된 적이 없다 — 둘이 다시 어긋나면 RDS 쪽을 기준으로 이 파일을 고친다.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid() — RDS는 uuid-ossp 대신 이걸 사용
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- RDS에 설치돼 있으나 사용하는 인덱스는 아직 없음(향후 유사검색용으로 추정)

-- ① 구조화된 입력
CREATE TABLE ideas (
    idea_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_input           text NOT NULL,
    title               varchar,
    idea_type           varchar,
    target_customer     text,
    problem_statement   text,
    solution_summary    text,
    business_model_hint text,
    technical_elements  jsonb DEFAULT '[]'::jsonb,
    patent_keywords     jsonb DEFAULT '[]'::jsonb,
    user_confirmed      boolean DEFAULT false,
    created_at          timestamptz DEFAULT now()
);

-- ② 분석 job (FastAPI 비동기 + 스트리밍)
CREATE TABLE analysis_jobs (
    job_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    idea_id          uuid NOT NULL REFERENCES ideas(idea_id) ON DELETE CASCADE,
    status           varchar DEFAULT 'pending',
    current_stage    varchar,
    progress_pct     int DEFAULT 0,
    decision         varchar,
    decision_summary text,
    started_at       timestamptz,
    finished_at      timestamptz,
    created_at       timestamptz DEFAULT now()
);
ALTER TABLE analysis_jobs ADD CONSTRAINT analysis_jobs_status_check
    CHECK (status IN ('pending', 'running', 'done', 'failed'));

-- ③ Hypothesis Ledger
CREATE TABLE hypotheses (
    hypothesis_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          uuid NOT NULL REFERENCES analysis_jobs(job_id) ON DELETE CASCADE,
    idea_id         uuid NOT NULL REFERENCES ideas(idea_id) ON DELETE CASCADE,
    code            varchar NOT NULL,
    axis            varchar,
    statement       text,
    confidence      varchar DEFAULT 'low',
    next_validation text,
    created_at      timestamptz DEFAULT now()
);
ALTER TABLE hypotheses ADD CONSTRAINT hypotheses_confidence_check
    CHECK (confidence IN ('high', 'mid', 'low'));

-- ④ 근거 출처 통합 + 임베딩
CREATE TABLE documents (
    document_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type       varchar NOT NULL,
    ext_id            varchar,
    title             varchar,
    canonical_url     varchar,
    clean_text        text,
    embedding         vector(768),
    meta              jsonb DEFAULT '{}'::jsonb,
    reliability_score numeric(3,2),
    freshness_score   numeric(3,2),
    is_user_provided  boolean DEFAULT false,
    created_at        timestamptz DEFAULT now()
);
ALTER TABLE documents ADD CONSTRAINT documents_source_type_check
    CHECK (source_type IN ('patent', 'seed_review', 'seed_competitor', 'seed_pricing', 'web'));
ALTER TABLE documents ADD CONSTRAINT uq_documents_ext_id UNIQUE (ext_id);

-- ⑤ ★ 그라운딩 원자
CREATE TABLE evidence_items (
    evidence_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id            uuid NOT NULL REFERENCES analysis_jobs(job_id) ON DELETE CASCADE,
    hypothesis_id     uuid NOT NULL REFERENCES hypotheses(hypothesis_id) ON DELETE CASCADE,
    document_id       uuid NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    source_type       varchar,
    evidence_text     text NOT NULL,
    stance            varchar DEFAULT 'neutral',
    relevance_score   numeric(5,4),
    reliability_score numeric(3,2),
    created_at        timestamptz DEFAULT now()
);
ALTER TABLE evidence_items ADD CONSTRAINT evidence_items_stance_check
    CHECK (stance IN ('supports', 'contradicts', 'neutral'));

-- ⑥ 에이전트 출력 공통 envelope
CREATE TABLE agent_runs (
    agent_run_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id             uuid NOT NULL REFERENCES analysis_jobs(job_id) ON DELETE CASCADE,
    hypothesis_id      uuid REFERENCES hypotheses(hypothesis_id) ON DELETE CASCADE,
    target_run_id      uuid REFERENCES agent_runs(agent_run_id),
    agent_name         varchar NOT NULL,
    model_name         varchar,
    depth              varchar DEFAULT 'light',
    confidence         varchar DEFAULT 'low',
    grounded_on        jsonb DEFAULT '[]'::jsonb,
    output_json        jsonb NOT NULL DEFAULT '{}'::jsonb,
    groundedness_score numeric(5,4),
    overclaim_flag     boolean DEFAULT false,
    status             varchar DEFAULT 'pending',
    created_at         timestamptz DEFAULT now()
);
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_agent_name_check
    CHECK (agent_name IN ('market', 'competitor', 'tech', 'ip', 'bm', 'critic', 'structuring'));
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_depth_check
    CHECK (depth IN ('full', 'light'));
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_confidence_check
    CHECK (confidence IN ('high', 'mid', 'low'));
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_status_check
    CHECK (status IN ('pending', 'running', 'done', 'failed'));

-- ───────── 시그니처(⑤ IP) 3 ─────────

CREATE TABLE patent_claims (
    claim_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    claim_no        int NOT NULL,
    claim_text      text NOT NULL,
    is_independent  boolean DEFAULT false,
    parent_claim_no int
);
-- 중복 UNIQUE 2건이 RDS에 실제로 존재함(버그로 추정, 마이그레이션이 두 번 적용된 것으로 보임).
-- RDS 자체를 고치는 건 범위 밖이라 문서에는 있는 그대로 반영한다.
ALTER TABLE patent_claims ADD CONSTRAINT patent_claims_document_id_claim_no_key UNIQUE (document_id, claim_no);
ALTER TABLE patent_claims ADD CONSTRAINT uq_claims_doc_no UNIQUE (document_id, claim_no);

CREATE TABLE claim_limitations (
    limitation_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id         uuid NOT NULL REFERENCES patent_claims(claim_id) ON DELETE CASCADE,
    limitation_order int NOT NULL,
    normalized_text  text NOT NULL,
    embedding        vector(768)            -- 짧아 청크 불필요
);
-- 중복 UNIQUE 2건, patent_claims와 동일한 사유 — 그대로 반영
ALTER TABLE claim_limitations ADD CONSTRAINT claim_limitations_claim_id_limitation_order_key UNIQUE (claim_id, limitation_order);
ALTER TABLE claim_limitations ADD CONSTRAINT uq_limitations_claim_order UNIQUE (claim_id, limitation_order);

CREATE TABLE ip_overlap_candidates (
    candidate_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id                 uuid NOT NULL REFERENCES analysis_jobs(job_id) ON DELETE CASCADE,
    hypothesis_id          uuid NOT NULL REFERENCES hypotheses(hypothesis_id) ON DELETE CASCADE,
    limitation_id          uuid NOT NULL REFERENCES claim_limitations(limitation_id) ON DELETE CASCADE,
    evidence_id            uuid NOT NULL REFERENCES evidence_items(evidence_id) ON DELETE CASCADE,
    plan_technical_element text NOT NULL,
    lexical_score          numeric(5,4),
    similarity_score       numeric(5,4),
    hybrid_score           numeric(5,4),
    rank                   int,
    created_at             timestamptz DEFAULT now()
);

-- ───────── 인덱스 ─────────
CREATE INDEX idx_documents_embedding        ON documents         USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64');
CREATE INDEX idx_claim_limitations_embedding ON claim_limitations USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64');
-- FTS는 'english' 설정(stemming/stopword 적용) — 한국어 특허 텍스트엔 부적합할 수 있음.
-- RDS 변경은 범위 밖이라 그대로 반영(원래는 'simple'이었음).
CREATE INDEX idx_documents_fts              ON documents          USING gin (to_tsvector('english', clean_text));
CREATE INDEX idx_claim_limitations_fts      ON claim_limitations  USING gin (to_tsvector('english', normalized_text));
CREATE INDEX idx_documents_source_type      ON documents (source_type);
CREATE INDEX idx_evidence_items_job_hypothesis ON evidence_items (job_id, hypothesis_id);
CREATE INDEX idx_agent_runs_job             ON agent_runs (job_id, hypothesis_id);
CREATE INDEX idx_patent_claims_independent  ON patent_claims (document_id, is_independent);
CREATE INDEX idx_ip_overlap_job_hypothesis  ON ip_overlap_candidates (job_id, hypothesis_id, rank);
