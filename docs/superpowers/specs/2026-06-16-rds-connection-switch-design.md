# AWS RDS 연결 전환 — 설계

## 배경

VentureScout는 docker-compose의 로컬 PostgreSQL(`pgvector/pgvector:pg16`)을 DB로 가정하고
스키마(`db/init.sql`)만 정의해둔 상태였다. 애플리케이션 코드(`agents/`, `app/`, `retrieval/tools.py`)는
아직 어떤 DB에도 실제로 연결하지 않는다(전부 mock 반환) — DB 액세스 레이어는 `track-b` 브랜치의
`config.py` + `pipeline/persistence.py`에 이미 구현되어 있으나 이 브랜치(D)에는 머지되지 않았다.

별도로, 팀이 이미 AWS RDS PostgreSQL 인스턴스(`your-db-host.ap-northeast-1.rds.amazonaws.com`)를
프로비저닝해두었고, 스키마(9개 테이블 + pgvector 확장)도 이미 적용되어 있는 것을 확인했다. 다만 이 RDS의
실제 스키마는 `db/init.sql`과 세부사항(UUID 생성 함수, VARCHAR 길이, NOT NULL, CHECK 제약, 인덱스 등)이
다르다. **RDS 자체는 건드리지 않되, 문서(`db/init.sql`, `db/schema.dbml`)를 RDS 실제 상태에 맞춰
다시 쓴다** (아래 "5. 스키마 문서 동기화" 참조) — 이렇게 하면 다음에 로컬 docker postgres를
새로 띄울 일이 생겨도(또는 다른 사람이 스키마를 읽을 때) 실제와 일치하는 정의를 보게 된다.

## 목표

애플리케이션(및 로컬 개발 환경)이 로컬 docker-compose postgres 대신 이 AWS RDS 인스턴스를
"진짜 DB"로 바라보게 만든다. **연결 설정 전환 + 스키마 문서 동기화**를 다루며, DB 액세스 코드
(retrieve/vector_search 구현, persistence 레이어)는 다루지 않는다.

## 범위 제외 (명시적으로 하지 않는 것)

- RDS 실제 스키마 자체를 변경하는 작업 (중복 UNIQUE 제약 정리, FTS 언어를 `simple`로 되돌리는 등) —
  운영 중인 DB라 건드리면 복잡해진다는 판단. 문서만 RDS에 맞춰 동기화하고 RDS는 그대로 둔다.
- `retrieval/tools.py`의 mock을 실제 pgvector 쿼리로 교체 (Track B 범위)
- IAM DB 인증으로 전환 (정적 비밀번호 유지)
- RDS 보안그룹 인바운드 규칙 관리 (AWS CLI/콘솔 작업 — 이 환경에는 AWS 자격증명이 없어 수행 불가)

## 설계

### 1. 환경변수 (`.env`, `.env.example`)

- 임시로 추가했던 `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` 제거
- `POSTGRES_HOST/PORT/DB/USER/PASSWORD`를 RDS 값으로 교체
- `DATABASE_URL`을 RDS 기준으로 재구성하고 `sslmode=verify-full&sslrootcert=<번들경로>` 쿼리 파라미터 추가
- `.env.example`에는 RDS 호스트만 예시로 남기고 비밀번호는 빈 값 유지 (기존 패턴과 동일)

이렇게 맞추는 이유: 아직 머지되지 않은 `track-b`의 `config.py`(`db_dsn` 속성)가 정확히 이
이름들(`POSTGRES_*` 또는 `DATABASE_URL`)을 읽도록 이미 작성되어 있다. 나중에 그 브랜치가
머지되어도 추가 수정 없이 RDS에 바로 연결되게 하기 위함이다.

### 2. SSL 인증서 처리

- AWS RDS 글로벌 인증서 번들(`global-bundle.pem`, 공개 파일·비밀 아님)을
  `https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`에서 받아
  `db/certs/rds-global-bundle.pem`으로 저장소에 커밋
- `sslmode=verify-full`로 서버 인증서까지 검증 (AWS 권장 방식; `require`는 암호화만 하고
  인증서 미검증이라 MITM에 취약해서 채택 안 함)
- 코드 변경 없음 — psycopg2/SQLAlchemy 모두 libpq URI의 `sslmode`/`sslrootcert` 파라미터를 그대로 인식

### 3. docker-compose 변경

- `db` 서비스(로컬 postgres 컨테이너) 전체 제거
- `api` 서비스의 `depends_on: db` 제거
- `./pgdata` 볼륨 마운트, `db/init.sql` 마운트 라인 제거 (RDS는 이미 자체 스키마로 운영 중)
- `ui` 서비스는 변경 없음 (이미 `DATABASE_URL: ""`로 Chainlit 데이터 레이어 비활성화 상태)

### 4. 검증 계획

- `sslmode=verify-full` + 인증서 번들로 연결 테스트 재실행 → `SELECT version()` 성공 확인
  (이전엔 `sslmode=require`만 테스트했음)
- `docker compose config`로 정적 검증 + 가능하면 `docker compose up api`로 컨테이너 내부에서도
  동일하게 연결되는지 확인

### 5. 스키마 문서 동기화 (`db/init.sql`, `db/schema.dbml`)

- RDS는 **읽기 전용으로만 조회**한다 (information_schema, pg_constraint, pg_indexes 등) — DDL을
  실행하거나 RDS의 어떤 것도 바꾸지 않는다.
- 조회 결과를 바탕으로 `db/init.sql`을 9개 테이블 전부 다시 작성한다. RDS 실제 상태를 **있는 그대로**
  반영한다 — 즉 다음 항목도 "고치지" 않고 그대로 포함하되, 발견된 사실은 주석으로 짧게 남긴다:
  - `gen_random_uuid()`(pgcrypto) 사용, `uuid-ossp` 대신 `pgcrypto`/`pg_trgm` 확장 선언
  - VARCHAR 길이 무제한, 추가된 NOT NULL/CHECK/DEFAULT, 모든 FK `ON DELETE CASCADE`
  - 6개 테이블에 추가된 `created_at` 컬럼, `agent_runs.target_run_id` 자기참조 FK
  - `idx_documents_source_type`/`idx_patent_claims_independent`/`idx_ip_overlap_job_hypothesis` 등
    추가 인덱스, `idx_evidence_items_job_hypothesis`/`idx_agent_runs_job`의 복합 컬럼 확장
  - `patent_claims`/`claim_limitations`의 중복 UNIQUE 제약 2건 — `-- 중복으로 보임(버그 추정), RDS 변경은
    범위 밖이라 그대로 반영` 주석 추가
  - FTS가 `to_tsvector('english', ...)`인 점 — `-- 한국어 텍스트엔 부적합할 수 있음, RDS 변경은 범위 밖`
    주석 추가
- `db/schema.dbml`도 동일한 컬럼/제약/관계로 다시 그린다 (dbdiagram.io 포맷).
- 파일 상단 주석(현재 "원칙: 계약 strict / payload JSONB / pgvector 단일 스토어")은 유지하고, 새 줄에
  "이 파일은 RDS(`venturescout-db...`)의 실제 스키마를 반영한 것" 한 줄을 추가한다.

## 알려진 한계

- RDS 보안그룹이 현재 연결 테스트가 성공한 이 PC의 공인 IP만 허용하고 있을 가능성이 높다.
  팀원 PC나 클라우드 배포 환경에서는 보안그룹 인바운드 규칙 추가가 별도로 필요하며, 이 작업은
  AWS 콘솔/CLI 권한이 필요해 현재 이 환경(AWS CLI 미설치, 자격증명 없음)에서는 수행할 수 없다.
- 중복 UNIQUE 제약, FTS 언어 설정(`english`) 등 RDS 자체의 잠재적 결함은 문서화만 하고 고치지 않는다 —
  운영 중인 공유 DB라 수정은 팀 논의 후 별도로 진행해야 한다.
