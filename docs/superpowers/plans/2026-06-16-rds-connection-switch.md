# AWS RDS Connection Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Point VentureScout's config (`.env`, `docker-compose.yml`) at the existing AWS RDS PostgreSQL instance instead of the local docker-compose Postgres, with `sslmode=verify-full`, and rewrite `db/init.sql`/`db/schema.dbml` to faithfully document the RDS instance's real (already-applied) schema — without changing anything on RDS itself.

**Architecture:** No application code changes. This is pure config + documentation: env vars switch from `localhost` to the RDS endpoint, a public AWS CA bundle is added for TLS verification, the local `db` container is removed from compose, and the two schema doc files are regenerated from a read-only introspection of RDS to match reality (including its quirks).

**Tech Stack:** psycopg2-binary (already installed), python-dotenv (already installed), Docker (for a throwaway local Postgres used only to verify the rewritten `db/init.sql` is byte-for-byte equivalent to RDS), curl.

Spec: `docs/superpowers/specs/2026-06-16-rds-connection-switch-design.md`

---

## File Structure

- Modify: `.gitignore` — allow committing the public RDS CA bundle despite the blanket `*.pem` ignore rule
- Create: `db/certs/rds-global-bundle.pem` — AWS RDS public CA bundle (not a secret)
- Create: `db/introspect_schema.py` — reusable read-only schema dump tool (JSON output), used both to verify this change and for future schema-drift checks
- Modify: `.env` — RDS connection values, `sslmode=verify-full`, remove temporary `DB_*` vars
- Modify: `.env.example` — same shape, no real secret
- Modify: `docker-compose.yml` — remove local `db` service and its dependents
- Modify: `db/init.sql` — rewritten to match RDS exactly
- Modify: `db/schema.dbml` — rewritten to match RDS exactly

---

### Task 1: Allow the RDS CA bundle past `.gitignore`

**Files:**
- Modify: `.gitignore:1-7`

- [ ] **Step 1: Add a negation rule for the cert bundle**

Current top of `.gitignore`:
```gitignore
# ── 비밀/환경 (절대 커밋 금지) ──
.env
.env.*
!.env.example
*.pem
*.key
secrets/
*.json
credentials
```

Change to:
```gitignore
# ── 비밀/환경 (절대 커밋 금지) ──
.env
.env.*
!.env.example
*.pem
!db/certs/*.pem
*.key
secrets/
*.json
credentials
```

- [ ] **Step 2: Verify**

Run: `git check-ignore -v db/certs/rds-global-bundle.pem`
Expected: non-zero exit with **no output** (i.e. the file would NOT be ignored). If it prints a matching rule, the negation didn't take — check that `!db/certs/*.pem` comes after `*.pem` in the file.

(The file doesn't exist yet — `git check-ignore` works on the path regardless of whether it exists.)

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "Allow committing the public RDS CA bundle under db/certs/"
```

---

### Task 2: Download the RDS CA bundle

**Files:**
- Create: `db/certs/rds-global-bundle.pem`

- [ ] **Step 1: Create the directory and download**

```bash
mkdir -p db/certs
curl -sSL -o db/certs/rds-global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
```

- [ ] **Step 2: Verify it's a real PEM bundle**

Run: `head -c 27 db/certs/rds-global-bundle.pem; echo; wc -l db/certs/rds-global-bundle.pem`
Expected: first line is `-----BEGIN CERTIFICATE-----` and the file has several hundred lines (it bundles many regional CAs).

- [ ] **Step 3: Commit**

```bash
git add db/certs/rds-global-bundle.pem
git commit -m "Add AWS RDS global CA bundle for sslmode=verify-full"
```

---

### Task 3: Write the reusable schema introspection script

**Files:**
- Create: `db/introspect_schema.py`

- [ ] **Step 1: Write the script**

```python
"""읽기 전용 스키마 조회 — DATABASE_URL(또는 INTROSPECT_DSN)로 접속한 PostgreSQL의
public 스키마를 JSON으로 덤프한다. RDS와 로컬 DB의 스키마가 같은지 비교할 때 쓴다
(db/init.sql 동기화 검증). DDL을 실행하거나 대상 DB를 수정하지 않는다.

사용:
    python db/introspect_schema.py > dump.json                      # .env의 DATABASE_URL 사용
    INTROSPECT_DSN="postgresql://..." python db/introspect_schema.py > dump.json   # 다른 DB 지정
"""
from __future__ import annotations

import json
import os

import psycopg2
from dotenv import load_dotenv


def introspect(dsn: str) -> dict:
    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension ORDER BY extname;")
            extensions = [r[0] for r in cur.fetchall()]

            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
            )
            tables = [r[0] for r in cur.fetchall()]

            result: dict = {"extensions": extensions, "tables": {}}

            for table in tables:
                cur.execute(
                    """
                    SELECT a.attname,
                           format_type(a.atttypid, a.atttypmod) AS data_type,
                           a.attnotnull,
                           pg_get_expr(d.adbin, d.adrelid) AS default_expr
                    FROM pg_attribute a
                    JOIN pg_class c ON c.oid = a.attrelid
                    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                    WHERE c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
                    ORDER BY a.attnum
                    """,
                    (table,),
                )
                columns = [
                    {"name": r[0], "type": r[1], "not_null": r[2], "default": r[3]}
                    for r in cur.fetchall()
                ]

                cur.execute(
                    """
                    SELECT con.conname, pg_get_constraintdef(con.oid)
                    FROM pg_constraint con
                    JOIN pg_class t ON t.oid = con.conrelid
                    WHERE t.relname = %s
                    ORDER BY con.conname
                    """,
                    (table,),
                )
                constraints = [{"name": r[0], "def": r[1]} for r in cur.fetchall()]

                cur.execute(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname='public' AND tablename=%s ORDER BY indexname",
                    (table,),
                )
                indexes = [{"name": r[0], "def": r[1]} for r in cur.fetchall()]

                result["tables"][table] = {
                    "columns": columns,
                    "constraints": constraints,
                    "indexes": indexes,
                }

        return result
    finally:
        conn.close()


if __name__ == "__main__":
    load_dotenv()
    dsn = os.environ.get("INTROSPECT_DSN") or os.environ["DATABASE_URL"]
    print(json.dumps(introspect(dsn), indent=2, sort_keys=True, default=str))
```

- [ ] **Step 2: Verify it runs against the current (still-local) `.env`**

Run: `python db/introspect_schema.py | head -5`
Expected: either valid JSON starting with `{` (if local docker postgres is up) or a clear connection error — either is fine here, this step only confirms the script itself runs without a Python error (syntax/import). Don't worry about the DB target yet — `.env` still points at `localhost` until Task 5.

- [ ] **Step 3: Commit**

```bash
git add db/introspect_schema.py
git commit -m "Add read-only schema introspection script for RDS/local schema diffing"
```

---

### Task 4: Capture the RDS schema as a reference dump

This dump is used later (Task 11) to verify the rewritten `db/init.sql` reproduces RDS exactly. It is a local working file, not committed (it's covered by the existing `*.json` rule in `.gitignore`).

**Files:** none (writes a gitignored temp file)

- [ ] **Step 1: Run the introspection script against RDS directly**

The current `.env` already has the working RDS credentials under `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` (added during the earlier connection test). Build a one-off DSN from those and point `INTROSPECT_DSN` at it:

```bash
python - <<'PYEOF'
import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
load_dotenv()
dsn = (
    f"postgresql://{os.environ['DB_USER']}:{quote_plus(os.environ['DB_PASSWORD'])}"
    f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}?sslmode=require"
)
os.environ["INTROSPECT_DSN"] = dsn
import subprocess
with open("_rds_reference_dump.json", "w", encoding="utf-8") as f:
    subprocess.run(["python", "db/introspect_schema.py"], env={**os.environ}, stdout=f, check=True)
PYEOF
```

- [ ] **Step 2: Verify**

Run: `python -c "import json; d=json.load(open('_rds_reference_dump.json')); print(sorted(d['tables'].keys())); print(d['extensions'])"`
Expected:
```
['agent_runs', 'analysis_jobs', 'claim_limitations', 'documents', 'evidence_items', 'hypotheses', 'ideas', 'ip_overlap_candidates', 'patent_claims']
['pg_trgm', 'pgcrypto', 'plpgsql', 'vector']
```

No commit for this task — `_rds_reference_dump.json` is a working file used by Task 11, not part of the codebase. (It matches the existing `*.json` ignore rule, so `git status` should not show it.)

---

### Task 5: Switch `.env` to RDS, consolidate naming, add SSL params

**Files:**
- Modify: `.env`

- [ ] **Step 1: Rewrite the DB section in place**

Replace lines 7–20 of `.env` (the `## ── PostgreSQL (+pgvector) ──` block plus the temporary `## ── AWS RDS ──` block) using a script so the real password is never typed into a command line or this plan file — it's read from the existing `DB_PASSWORD` value already in `.env` and written back out:

```bash
python - <<'PYEOF'
import re
from pathlib import Path
from urllib.parse import quote_plus

env_path = Path(".env")
text = env_path.read_text(encoding="utf-8")

vals = {}
for line in text.splitlines():
    if line.startswith(("DB_HOST=", "DB_PORT=", "DB_NAME=", "DB_USER=", "DB_PASSWORD=")):
        key, _, val = line.partition("=")
        vals[key] = val.strip().strip("'").strip('"')

encoded_pw = quote_plus(vals["DB_PASSWORD"])
database_url = (
    f"postgresql://{vals['DB_USER']}:{encoded_pw}@{vals['DB_HOST']}:{vals['DB_PORT']}"
    f"/{vals['DB_NAME']}?sslmode=verify-full&sslrootcert=db/certs/rds-global-bundle.pem"
)

new_pg_block = (
    "# ── PostgreSQL (+pgvector, AWS RDS) ──\n"
    f"POSTGRES_HOST={vals['DB_HOST']}\n"
    f"POSTGRES_PORT={vals['DB_PORT']}\n"
    f"POSTGRES_DB={vals['DB_NAME']}\n"
    f"POSTGRES_USER={vals['DB_USER']}\n"
    f"POSTGRES_PASSWORD={vals['DB_PASSWORD']}\n"
    f"DATABASE_URL={database_url}\n"
)

pattern = re.compile(
    r"# ── PostgreSQL \(\+pgvector\) ──\n.*?(?=\n# ── (?:AWS RDS|임베딩))",
    re.DOTALL,
)
text, n = pattern.subn(new_pg_block, text, count=1)
assert n == 1, "PostgreSQL block not found/replaced"

text = re.sub(
    r"# ── AWS RDS \(PostgreSQL, 직접 연결 테스트용\) ──\n"
    r"DB_HOST=.*\nDB_PORT=.*\nDB_NAME=.*\nDB_USER=.*\nDB_PASSWORD=.*\n\n",
    "",
    text,
)

env_path.write_text(text, encoding="utf-8")
print("done")
PYEOF
```

- [ ] **Step 2: Verify structure (without printing the password)**

Run: `grep -E '^(POSTGRES_HOST|POSTGRES_PORT|POSTGRES_DB|POSTGRES_USER|DB_HOST|DATABASE_URL)' .env | sed -E 's/(POSTGRES_PASSWORD|sslrootcert)=.*/\1=<hidden>/'`
Expected: `POSTGRES_HOST` is the RDS endpoint, no `DB_HOST=` line exists anymore (the old block was removed), and a `DATABASE_URL=` line is present containing `sslmode=verify-full&sslrootcert=db/certs/rds-global-bundle.pem`.

Run: `grep -c '^DB_HOST=' .env`
Expected: `0`

- [ ] **Step 3: Commit**

`.env` is gitignored, so there is nothing to `git add` — confirm that explicitly instead:

Run: `git status --short .env`
Expected: no output (file is ignored, not tracked — this step has nothing to commit, that's correct).

---

### Task 6: Update `.env.example` to match

**Files:**
- Modify: `.env.example:7-13`

- [ ] **Step 1: Edit**

Replace:
```
# ── PostgreSQL (+pgvector) ──
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=venturescout
POSTGRES_USER=vs
POSTGRES_PASSWORD=changeme
DATABASE_URL=postgresql://vs:changeme@localhost:5432/venturescout
```

With:
```
# ── PostgreSQL (+pgvector, AWS RDS) ──
POSTGRES_HOST=your-db-host.ap-northeast-1.rds.amazonaws.com
POSTGRES_PORT=5432
POSTGRES_DB=venturescout
POSTGRES_USER=postgres
POSTGRES_PASSWORD=
DATABASE_URL=postgresql://postgres:<password>@your-db-host.ap-northeast-1.rds.amazonaws.com:5432/venturescout?sslmode=verify-full&sslrootcert=db/certs/rds-global-bundle.pem
```

- [ ] **Step 2: Verify**

Run: `grep -n 'POSTGRES_\|DATABASE_URL' .env.example`
Expected: 6 lines matching the block above, `POSTGRES_PASSWORD=` empty, no real secret present.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "Point .env.example at RDS with verify-full SSL params"
```

---

### Task 7: Verify the live RDS connection with `verify-full`

**Files:** none (temporary script, deleted after use)

- [ ] **Step 1: Write a throwaway verification script**

```python
import os
import sys

from dotenv import load_dotenv
import psycopg2

load_dotenv()

try:
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute("SELECT version();")
        print("CONNECTED:", cur.fetchone()[0])
    conn.close()
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
```

Save as `_verify_rds_ssl.py`.

- [ ] **Step 2: Run it**

Run: `python _verify_rds_ssl.py`
Expected: `CONNECTED: PostgreSQL 16.13 ...` (same output as the earlier `sslmode=require` test, now via `verify-full` + the bundle through `DATABASE_URL`).

If it fails with an SSL/certificate error, check that `db/certs/rds-global-bundle.pem` downloaded correctly (Task 2) and that the path in `DATABASE_URL` (`db/certs/rds-global-bundle.pem`) is relative to the directory `python` is run from (repo root).

- [ ] **Step 3: Delete the throwaway script**

```bash
rm _verify_rds_ssl.py
```

No commit (nothing tracked changed in this task).

---

### Task 8: Remove the local `db` service from `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Edit**

Current file:
```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: venturescout
      POSTGRES_USER: vs
      POSTGRES_PASSWORD: changeme
    ports:
      - "5432:5432"
    volumes:
      - ./pgdata:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U vs -d venturescout"]
      interval: 5s
      timeout: 3s
      retries: 5

  api:
    build: .
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8000:8000"
    volumes:
      - .:/app

  ui:
    build: .
    command: chainlit run app/ui.py --host 0.0.0.0 --port 8001
    environment:
      API_URL: http://api:8000
      DATABASE_URL: ""        # ← Chainlit 데이터 레이어 비활성화
    depends_on:
      - api
    ports:
      - "8001:8001"
    volumes:
      - .:/app
```

Replace with:
```yaml
services:
  api:
    build: .
    env_file: .env
    ports:
      - "8000:8000"
    volumes:
      - .:/app

  ui:
    build: .
    command: chainlit run app/ui.py --host 0.0.0.0 --port 8001
    environment:
      API_URL: http://api:8000
      DATABASE_URL: ""        # ← Chainlit 데이터 레이어 비활성화
    depends_on:
      - api
    ports:
      - "8001:8001"
    volumes:
      - .:/app
```

(The `db` service block is gone entirely; `api`'s `depends_on` is gone since it only ever pointed at `db`; `api` still gets `DATABASE_URL` etc. from `.env` via `env_file: .env`.)

- [ ] **Step 2: Verify**

Run: `docker compose config --services`
Expected:
```
api
ui
```
(no `db`)

Run: `docker compose config | grep -i postgres`
Expected: no output (no local postgres image/env referenced anymore in compose itself — DB config now comes purely from `.env`).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "Remove local postgres service from docker-compose, use RDS via .env"
```

---

### Task 9: Rewrite `db/init.sql` to match RDS exactly

**Files:**
- Modify: `db/init.sql` (full rewrite)

- [ ] **Step 1: Replace the entire file content**

```sql
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
    reliability_score numeric,
    freshness_score   numeric,
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
    relevance_score   numeric,
    reliability_score numeric,
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
    groundedness_score numeric,
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
    lexical_score          numeric,
    similarity_score       numeric,
    hybrid_score           numeric,
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
```

- [ ] **Step 2: Sanity-check it's valid SQL syntactically (no DB needed yet)**

Run: `python -c "import re; s=open('db/init.sql', encoding='utf-8').read(); print(s.count('CREATE TABLE'), s.count('CREATE INDEX'), s.count('ALTER TABLE'))"`
Expected: `9 9 13` (9 tables, 9 indexes, 13 ALTER TABLE constraint additions — 4 on agent_runs, 2 each on documents/patent_claims/claim_limitations, 1 each on analysis_jobs/hypotheses/evidence_items: 4+2+2+2+1+1+1 = 13).

Don't commit yet — verified together with `schema.dbml` in Task 11 after the round-trip test passes.

---

### Task 10: Rewrite `db/schema.dbml` to match

**Files:**
- Modify: `db/schema.dbml` (full rewrite)

- [ ] **Step 1: Replace the entire file content**

```dbml
// VentureScout — RDS 실제 스키마 반영 (your-db-host.ap-northeast-1.rds.amazonaws.com)
// dbdiagram.io 붙여넣기용. db/init.sql과 1:1 대응.

Table ideas {
  idea_id uuid [pk, default: `gen_random_uuid()`]
  raw_input text [not null]
  title varchar
  idea_type varchar
  target_customer text
  problem_statement text
  solution_summary text
  business_model_hint text
  technical_elements json [default: `[]`]
  patent_keywords json [default: `[]`]
  user_confirmed boolean [default: false]
  created_at timestamp [default: `now()`]
}
Table analysis_jobs {
  job_id uuid [pk, default: `gen_random_uuid()`]
  idea_id uuid [not null]
  status varchar [default: 'pending', note: 'CHECK: pending|running|done|failed']
  current_stage varchar
  progress_pct int [default: 0]
  decision varchar
  decision_summary text
  started_at timestamp
  finished_at timestamp
  created_at timestamp [default: `now()`]
}
Table hypotheses {
  hypothesis_id uuid [pk, default: `gen_random_uuid()`]
  job_id uuid [not null]
  idea_id uuid [not null]
  code varchar [not null]
  axis varchar
  statement text
  confidence varchar [default: 'low', note: 'CHECK: high|mid|low']
  next_validation text
  created_at timestamp [default: `now()`]
}
Table documents {
  document_id uuid [pk, default: `gen_random_uuid()`]
  source_type varchar [not null, note: 'CHECK: patent|seed_review|seed_competitor|seed_pricing|web']
  ext_id varchar [unique]
  title varchar
  canonical_url varchar
  clean_text text
  embedding vector [note: 'vector(768)']
  meta json [default: `{}`]
  reliability_score decimal
  freshness_score decimal
  is_user_provided boolean [default: false]
  created_at timestamp [default: `now()`]
}
Table evidence_items {
  evidence_id uuid [pk, default: `gen_random_uuid()`]
  job_id uuid [not null]
  hypothesis_id uuid [not null]
  document_id uuid [not null]
  source_type varchar
  evidence_text text [not null]
  stance varchar [default: 'neutral', note: 'CHECK: supports|contradicts|neutral']
  relevance_score decimal
  reliability_score decimal
  created_at timestamp [default: `now()`]
}
Table agent_runs {
  agent_run_id uuid [pk, default: `gen_random_uuid()`]
  job_id uuid [not null]
  hypothesis_id uuid
  target_run_id uuid
  agent_name varchar [not null, note: 'CHECK: market|competitor|tech|ip|bm|critic|structuring']
  model_name varchar
  depth varchar [default: 'light', note: 'CHECK: full|light']
  confidence varchar [default: 'low', note: 'CHECK: high|mid|low']
  grounded_on json [default: `[]`]
  output_json json [not null, default: `{}`]
  groundedness_score decimal
  overclaim_flag boolean [default: false]
  status varchar [default: 'pending', note: 'CHECK: pending|running|done|failed']
  created_at timestamp [default: `now()`]
}
Table patent_claims {
  claim_id uuid [pk, default: `gen_random_uuid()`]
  document_id uuid [not null]
  claim_no int [not null]
  claim_text text [not null]
  is_independent boolean [default: false]
  parent_claim_no int

  indexes {
    (document_id, claim_no) [unique, note: '중복 UNIQUE 2건이 RDS에 존재(버그 추정) — 범위 밖, 그대로 반영']
  }
}
Table claim_limitations {
  limitation_id uuid [pk, default: `gen_random_uuid()`]
  claim_id uuid [not null]
  limitation_order int [not null]
  normalized_text text [not null]
  embedding vector [note: 'vector(768)']

  indexes {
    (claim_id, limitation_order) [unique, note: '중복 UNIQUE 2건이 RDS에 존재(버그 추정) — 범위 밖, 그대로 반영']
  }
}
Table ip_overlap_candidates {
  candidate_id uuid [pk, default: `gen_random_uuid()`]
  job_id uuid [not null]
  hypothesis_id uuid [not null]
  limitation_id uuid [not null]
  evidence_id uuid [not null]
  plan_technical_element text [not null]
  lexical_score decimal
  similarity_score decimal
  hybrid_score decimal
  rank int
  created_at timestamp [default: `now()`]
}

Ref: ideas.idea_id < analysis_jobs.idea_id [delete: cascade]
Ref: ideas.idea_id < hypotheses.idea_id [delete: cascade]
Ref: analysis_jobs.job_id < hypotheses.job_id [delete: cascade]
Ref: analysis_jobs.job_id < evidence_items.job_id [delete: cascade]
Ref: hypotheses.hypothesis_id < evidence_items.hypothesis_id [delete: cascade]
Ref: documents.document_id < evidence_items.document_id [delete: cascade]
Ref: analysis_jobs.job_id < agent_runs.job_id [delete: cascade]
Ref: hypotheses.hypothesis_id < agent_runs.hypothesis_id [delete: cascade]
Ref: agent_runs.target_run_id < agent_runs.agent_run_id
Ref: documents.document_id < patent_claims.document_id [delete: cascade]
Ref: patent_claims.claim_id < claim_limitations.claim_id [delete: cascade]
Ref: analysis_jobs.job_id < ip_overlap_candidates.job_id [delete: cascade]
Ref: hypotheses.hypothesis_id < ip_overlap_candidates.hypothesis_id [delete: cascade]
Ref: claim_limitations.limitation_id < ip_overlap_candidates.limitation_id [delete: cascade]
Ref: evidence_items.evidence_id < ip_overlap_candidates.evidence_id [delete: cascade]
```

- [ ] **Step 2: Verify table count matches `init.sql`**

Run: `grep -c '^Table ' db/schema.dbml`
Expected: `9`

No commit yet — verified together with `init.sql` in Task 11.

---

### Task 11: Prove `db/init.sql` reproduces RDS exactly, then commit both files

**Files:** none tracked until Step 6 (uses a throwaway local container + throwaway script)

- [ ] **Step 1: Start a throwaway local Postgres container**

```bash
docker run --rm -d --name vs_schema_check -e POSTGRES_PASSWORD=test -p 5433:5432 pgvector/pgvector:pg16
sleep 3
```

- [ ] **Step 2: Wait for it to accept connections and apply the new `db/init.sql`**

```python
import time
import psycopg2

dsn = "postgresql://postgres:test@localhost:5433/postgres"
for _ in range(10):
    try:
        conn = psycopg2.connect(dsn, connect_timeout=2)
        break
    except psycopg2.OperationalError:
        time.sleep(1)
else:
    raise SystemExit("local container never became ready")

conn.autocommit = True
sql = open("db/init.sql", encoding="utf-8").read()
with conn.cursor() as cur:
    cur.execute(sql)
conn.close()
print("init.sql applied OK")
```

Save as `_apply_init_sql.py` and run: `python _apply_init_sql.py`
Expected: `init.sql applied OK` (any SQL error here means a typo in Task 9's DDL — fix `db/init.sql` and re-run from Step 1 with a fresh container: `docker rm -f vs_schema_check` first).

- [ ] **Step 3: Dump the local container's schema and diff against the RDS reference**

```bash
INTROSPECT_DSN="postgresql://postgres:test@localhost:5433/postgres" python db/introspect_schema.py > _local_schema_dump.json
diff _rds_reference_dump.json _local_schema_dump.json
```

Expected: **no output** (exit code 0 — the two JSON dumps are identical). The `plpgsql` extension and every table's columns/constraints/indexes must match exactly.

If `diff` shows a difference: fix `db/init.sql` (Task 9) to account for it, re-run `docker rm -f vs_schema_check` + Step 1 + Step 2 + Step 3 until the diff is empty. Common mismatches to expect on the first attempt: the `vector(768)` dimension (confirm via the `embedding` column's `type` field in the JSON — it should read `vector(768)`), or a constraint name typo.

- [ ] **Step 4: Tear down the throwaway container and scripts**

```bash
docker rm -f vs_schema_check
rm _apply_init_sql.py _rds_reference_dump.json _local_schema_dump.json
```

- [ ] **Step 5: Update `db/schema.dbml`'s table count check already done in Task 10 — no further action needed.**

- [ ] **Step 6: Commit both schema doc files together**

```bash
git add db/init.sql db/schema.dbml
git commit -m "Sync db/init.sql and db/schema.dbml with RDS's actual schema"
```

---

### Task 12: Final end-to-end sanity check

**Files:** none

- [ ] **Step 1: Confirm no local-only references remain**

Run: `grep -rn 'localhost.*5432\|pgdata' docker-compose.yml .env.example`
Expected: no output (no leftover references to the old local postgres setup in tracked files).

- [ ] **Step 2: Confirm `.env` itself still has the working RDS connection**

Run: `python -c "
import os
from dotenv import load_dotenv
load_dotenv()
print('POSTGRES_HOST' in os.environ and os.environ['POSTGRES_HOST'])
print('verify-full' in os.environ.get('DATABASE_URL', ''))
"`
Expected:
```
your-db-host.ap-northeast-1.rds.amazonaws.com
True
```

- [ ] **Step 3: Confirm `git log` shows the expected commits**

Run: `git log --oneline -8`
Expected: commits for (in some order, most recent first) — schema doc sync, docker-compose change, .env.example update, RDS CA bundle add, gitignore exception, introspection script add — on top of the earlier spec commits.

No further commit needed — this task only verifies prior commits.
