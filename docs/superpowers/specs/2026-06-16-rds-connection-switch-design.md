# AWS RDS 연결 전환 — 설계

## 배경

VentureScout는 docker-compose의 로컬 PostgreSQL(`pgvector/pgvector:pg16`)을 DB로 가정하고
스키마(`db/init.sql`)만 정의해둔 상태였다. 애플리케이션 코드(`agents/`, `app/`, `retrieval/tools.py`)는
아직 어떤 DB에도 실제로 연결하지 않는다(전부 mock 반환) — DB 액세스 레이어는 `track-b` 브랜치의
`config.py` + `pipeline/persistence.py`에 이미 구현되어 있으나 이 브랜치(D)에는 머지되지 않았다.

별도로, 팀이 이미 AWS RDS PostgreSQL 인스턴스(`your-db-host.ap-northeast-1.rds.amazonaws.com`)를
프로비저닝해두었고, 스키마(9개 테이블 + pgvector 확장)도 이미 적용되어 있는 것을 확인했다. 다만 이 RDS의
실제 스키마는 `db/init.sql`과 세부사항(UUID 생성 함수, VARCHAR 길이, NOT NULL, CHECK 제약, 인덱스 등)이
다르다 — 이 차이는 의도적으로 범위에서 제외한다(아래 "범위 제외" 참조).

## 목표

애플리케이션(및 로컬 개발 환경)이 로컬 docker-compose postgres 대신 이 AWS RDS 인스턴스를
"진짜 DB"로 바라보게 만든다. **연결 설정 전환만** 다루며, DB 액세스 코드(retrieve/vector_search
구현, persistence 레이어)나 스키마 정합성 문제는 다루지 않는다.

## 범위 제외 (명시적으로 하지 않는 것)

- `db/init.sql`과 RDS 실제 스키마의 차이를 맞추는 작업 (UUID 함수, VARCHAR 길이, NOT NULL/CHECK
  제약, 중복 UNIQUE 제약, FTS 언어 설정(`simple` vs `english`) 등 11가지 차이 발견됨 — 별도 이슈로 다룸)
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

## 알려진 한계

- RDS 보안그룹이 현재 연결 테스트가 성공한 이 PC의 공인 IP만 허용하고 있을 가능성이 높다.
  팀원 PC나 클라우드 배포 환경에서는 보안그룹 인바운드 규칙 추가가 별도로 필요하며, 이 작업은
  AWS 콘솔/CLI 권한이 필요해 현재 이 환경(AWS CLI 미설치, 자격증명 없음)에서는 수행할 수 없다.
- `db/init.sql`은 RDS 실제 스키마와 어긋난 채로 남는다 (의도적, 위 "범위 제외" 참조).
