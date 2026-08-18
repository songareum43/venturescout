# VentureScout

> **포트폴리오 사본입니다.** 4인 팀 프로젝트이며, 저는 **Track A — 데이터 수집·적재**를 담당했습니다.
> 담당 범위는 아래 [내 담당 범위](#내-담당-범위--track-a-데이터-수집적재)에 정리했고, 나머지 트랙은 팀원들의 작업입니다.
> 원본 팀 레포 · 전체 커밋 히스토리와 팀원 기여는 그대로 보존되어 있습니다 → [de-ai-AIAgentPJ-team4/venturescout](https://github.com/de-ai-AIAgentPJ-team4/venturescout)

Evidence 기반 창업 실사 멀티 에이전트. 창업 아이디어를 가설로 분해하고, 상충하는 근거를 Evidence Board에 드러낸 뒤, Critic이 낙관 편향을 제거해 Go/Pivot/Kill/More Research 신호와 다음 검증 실험을 제안한다.

- **시그니처** — 특허 청구항 중첩 신호 분석 (clean 공식 데이터)
- **멀티에이전트 정당성** — Critic ON/OFF로 overclaim·ungrounded 감소량 정량화
- **스코프** — 혼합: 5가설 보드는 넓게, 깊이는 ⑤ IP에 집중 (②⑤ full / ③④⑥ light)

## 스택

LangGraph · AWS Bedrock(ChatBedrockConverse) · PostgreSQL + pgvector + tsvector · FastAPI(스트리밍) · Chainlit · Docker Compose

> 데이터 소스(영어 USPTO·BigQuery / 한국어 KIPRIS)는 **Day 1 결정**. 임베딩 모델(PatentSBERTa / KorPatBERT)이 여기 종속.

## 구조 (책임 경계 = 디렉터리)

```
shared/         계약(C, Day 1 확정) — contracts.py / state.py
db/             스키마 — init.sql(9 tables) / schema.dbml
data/           Track A — 수집·파싱·적재
dags/           Track A — 주간 증분 갱신 Airflow DAG (연습용)
retrieval/      Track B — 임베딩·하이브리드 검색·tool  (②③ 에이전트)
agents/         Track C — LangGraph ①④⑤⑦ + 척추
app/            Track D — FastAPI·Chainlit·평가        (⑥ 에이전트)
docs/           plan_v3.md / schema_tier0.md
tests/          계약 검증
```

## 역할 (4인)

| 트랙 | 담당 | 에이전트 |
|---|---|---|
| **A** 👈 *본인* | **데이터 수집·저장(파싱·적재)** | — |
| B | 검색·임베딩 | ② Market(full), ③ Competitor(light) |
| C | 에이전트 플랫폼(척추·계약) | ① Structuring, ④ Tech(light), ⑤ IP(full·시그니처), ⑦ Critic |
| D | 백엔드·UI·평가 | ⑥ Business Model(light) |

핵심 경계: **시그니처 기계(파싱=A / 검색·임베딩=B)는 DB·tool에, ⑤·④ 에이전트는 읽어와 LLM 판정만.** 척추(State·그래프·⑦·그라운딩)는 C 단독.

## 내 담당 범위 — Track A (데이터 수집·적재)

에이전트들이 근거로 삼는 **모든 데이터의 수집·저장 파이프라인 전 구간**을 맡았습니다.
`BigQuery(공개 특허) → S3 → PostgreSQL` 경로와, 경쟁사 분석용 시드 데이터셋 구축입니다.

| 범위 | 내용 | 코드 |
|---|---|---|
| **수집** | Google BigQuery 공개 특허 데이터셋(`patents-public-data.patents.publications`)에서 2021–2024년 특허를 **연도별로 분할 수집** → S3 적재. 실행 시 S3 객체 키를 파싱해 **이미 수집된 연도를 건너뛰는 중복 방지** 로직 포함 | [data/collect_from_bigquery.py](data/collect_from_bigquery.py) |
| **적재** | S3의 **최신 파일 자동 탐색** 후 PostgreSQL 배치 적재. 대량 삽입 최적화 | [data/load_from_s3.py](data/load_from_s3.py) |
| **오케스트레이션**<br>*(연습용)* | 주간 증분 갱신 Airflow DAG. `extract → load → embed → verify` 4단계. **Airflow 비의존 순수 함수와 `@task` 데코레이터를 분리**해 테스트 가능하게 구성, 단계별 재시도 정책 차등 적용, S3 `head_object` 기반 **멱등 추출**(재실행 안전), 주간 소량 배치엔 HNSW 인덱스 재빌드를 건너뛰도록 처리, 마지막 `verify_sync`에서 건수 불일치 시 실행 실패 처리 | [dags/](dags/) · [설계 문서](docs/superpowers/specs/2026-06-22-patent-weekly-refresh-dag-design.md) · [구현 계획](docs/superpowers/plans/2026-06-22-patent-weekly-refresh-dag.md) |
| **시드 데이터** | B2B SaaS · Fintech · HR Tech **90개사**를 `competitors` / `pricing` / `reviews` 3종 스키마로 정규화 → **JSON 270개** 직접 구축. 통화 단위 통일 및 DB 스키마 정합 작업 포함. 90개 파일을 직접 대조해 **스키마 정의 문서**로 정리 (필드 규약, jsonb 검증 책임 경계, `ext_id` 전역 UNIQUE 제약) | [data/SEED_DATA_FORMAT.md](data/SEED_DATA_FORMAT.md) · [data/competitors/](data/competitors/) · [data/pricing/](data/pricing/) · [data/reviews/](data/reviews/) · [data/load_seed.py](data/load_seed.py) |
| **인프라** | 로컬 DB → **AWS RDS 연결 방식 전환** (SSL 검증 포함) | [data/](data/) · [db/](db/) |
| **에이전트 기여** | Solution Advisor 에이전트 설계 문서 작성 | [docs/superpowers/specs/2026-06-19-solution-advisor-agent-design.md](docs/superpowers/specs/2026-06-19-solution-advisor-agent-design.md) |
| | Critic 판정 규칙에 **Kill 조건** 추가 | [agents/graph.py](agents/graph.py) |

<details>
<summary>내 커밋만 보기</summary>

```bash
git log --author="songareum" --oneline --no-merges
```
</details>

## Day 1 셋업

```bash
cp .env.example .env          # 값 채우기 (.env 는 절대 커밋 금지)
docker compose up -d db       # pgvector + init.sql 자동 적재
docker compose up api ui      # API :8000 / Chainlit :8001
pytest                        # 계약 green 확인
```

## 개발 규율

- **계약 우선** — `shared/contracts.py`가 cross-team 면. strict 필드(evidence_id·grounded_on·confidence·stance·depth)는 안 바꿈, 분석 본문은 `payload`/`output_json`로 느슨하게.
- **live 실행** — `retrieval/tools.py`가 PostgreSQL/pgvector 실제 검색 결과만 반환.
- **Tier 0 하드게이트** — ②⑤ full + ③④⑥ light + ⑦ + Evidence Board E2E 전까지 Tier 1+ 금지.
- `.env`·데이터·모델은 `.gitignore`로 보호. 커밋 전 `git status` 확인.
