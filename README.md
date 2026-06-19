# VentureScout

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
retrieval/      Track B — 임베딩·하이브리드 검색·tool  (②③ 에이전트)
agents/         Track C — LangGraph ①④⑤⑦ + 척추
app/            Track D — FastAPI·Chainlit·평가        (⑥ 에이전트)
docs/           plan_v3.md / schema_tier0.md
tests/          계약 검증
```

## 역할 (4인)

| 트랙 | 담당 | 에이전트 |
|---|---|---|
| A | 데이터 수집·저장(파싱·적재) | — |
| B | 검색·임베딩 | ② Market(full), ③ Competitor(light) |
| C | 에이전트 플랫폼(척추·계약) | ① Structuring, ④ Tech(light), ⑤ IP(full·시그니처), ⑦ Critic |
| D | 백엔드·UI·평가 | ⑥ Business Model(light) |

핵심 경계: **시그니처 기계(파싱=A / 검색·임베딩=B)는 DB·tool에, ⑤·④ 에이전트는 읽어와 LLM 판정만.** 척추(State·그래프·⑦·그라운딩)는 C 단독.

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
