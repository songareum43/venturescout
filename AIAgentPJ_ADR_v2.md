# VentureScout — Architecture Decision Record (ADR)

> **목적**: 이 문서 하나만 보고 작업을 이어갈 수 있도록 모든 결정·구현·레포 상태·다음 작업을 기록.
> **최종 갱신**: Day 0+ (D3 게이트 브라우저 검증 완료 — 로컬 3.14 배제·Docker 실행 확정, ui DATABASE_URL 정리, ⑥ BM 설계 확정)
> **레포**: https://github.com/de-ai-AIAgentPJ-team4/venturescout

---

## 0. 한눈에 보는 현재 상태

**프로젝트** — VentureScout: Evidence 기반 창업 실사 멀티 에이전트. 창업 아이디어를 가설로 분해 → 상충 근거를 Evidence Board에 노출 → Critic이 낙관 편향 제거 → Go/Pivot/Kill/More Research + 다음 실험 제안. 시그니처 = 특허 청구항 중첩 신호.

**확정된 큰 결정**
- 데이터 소스: **BigQuery (Google Patents, 영어)** / 도메인: **이커머스·콘텐츠 추천 알고리즘**
- 임베딩: **PatentSBERTa 768d** / 분류: **CPC** / 벡터: **PostgreSQL 단일(pgvector+tsvector)**
- 에이전트: **7개**(①~⑦), supervisor=⑦ Critic / LLM: **Bedrock ChatBedrockConverse**
- 스코프: **혼합**(②⑤ full / ③④⑥ light) / 프론트: **Chainlit 스트리밍**
- 실행: **Docker Compose(컨테이너 3.11) 표준** — 로컬 Windows/3.14 미지원(ADR-025)
- 분담: **A 데이터 / B 검색·임베딩+②③ / C 플랫폼+①④⑤⑦ / D 백엔드·UI·평가+⑥**

**완료**
- 기획 v3 + Tier 0 스키마(9테이블) 확정
- 레포 스캐폴딩 + GitHub org push (계약 코드·DDL·트랙 stub·docker-compose)
- **D: `/analyze` SSE 실 스트리밍 완성** — `astream_events`로 7노드 진행 중계, §3 봉투(job/stage/report) 준수 (ADR-023)
- **D: State `findings`/`evidence_pool` reducer 적용** (ADR-023 — 레포엔 빠져있던 걸 실제 랜딩) ★C 공유 필요
- **D: Chainlit `app/ui.py` SSE 구독 → 단계 cl.Step 렌더 → Evidence Board 완성** (ADR-024)
- **D: 로컬(3.14) → Docker(3.11) 실행 전환** — 3.14 비호환으로 로컬 검증 실패, Docker 표준 확정 (ADR-025)
- **D: ui 컨테이너 `DATABASE_URL` 차단** — Chainlit 데이터레이어 off로 8001 흰 화면(500) 해결 (ADR-026)
- **D: 브라우저 E2E 검증** — `localhost:8001`에서 Evidence Board 실렌더 확인(mock, decision/요약/가설별 근거 표) → **D3 게이트 진짜 통과**
- **D: `D` 브랜치 푸시 완료** — api/ui/state/requirements/compose/.env.example
- 검증: 서버 기동 → `/analyze` E2E(mock 그래프) → 단계 14개 + report 정상 / `pytest` green

**⚠️ 레포 vs ADR 격차 (해소됨)** — 이전 ADR은 "api.py 스트리밍·reducer 완료"로 기록됐으나 **푸시된 코드엔 미반영**(api.py는 그래프 미호출 stub, state.py reducer 없음, 병렬노드 `INVALID_CONCURRENT_GRAPH_UPDATE` 재현). 이번에 실제 랜딩하여 ADR과 코드 일치.

**다음 할 일(우선순위)**
1. A: BigQuery dry run으로 추천 도메인 특허 건수 확인 → 수집·적재
2. B: PatentSBERTa 임베딩 파이프라인 + 하이브리드 검색 tool
3. C: ①④⑤⑦ 노드 실제 LLM 연결(현재 mock·sync) + **state.py reducer 변경 리뷰**
4. D: ⑥ BM 설계 확정(ADR-027) → **BM 노드 본문 구현** → 평가 하네스(Critic ON/OFF). `D` 브랜치 PR 머지.

---

## 1. 결정 로그 (ADR)

각 항목: 상태 / 맥락 / 결정 / 결과 / (기각안).

### ADR-001 — 프로젝트 베이스: VentureScout 채택
- **상태**: accepted
- **맥락**: 후보 둘 — v2(IP 검증 단독 본체) vs VentureScout(창업 실사, IP는 시그니처 한 축). 팀원 VentureScout 문서가 멀티에이전트 정당화·process 평가·Postgres 단일스토어·시드 전략에서 더 성숙.
- **결정**: VentureScout를 **뼈대**로, v2의 세부 3개(임베딩 모델·데이터소스 분기·Chainlit)를 주입. v2의 Chroma·"IP 단독 본체"는 폐기.
- **결과**: v2 전체 ⊂ VentureScout의 ⑤ IP 한 축. 넓이 확보 + IP 깊이 유지.
- **기각**: v2 단독(넓이 부족), 두 안 기능 병합(제품이 달라 불가).

### ADR-002 — 데이터 소스: BigQuery (Google Patents, 영어)
- **상태**: accepted
- **맥락**: 영어(USPTO/BigQuery) vs 한국어(KIPRIS). 트레이드오프 — BigQuery는 데이터 확보 쉬움·승인 도박 없음 / KIPRIS는 한국 FTO 본질 적합하나 월 1,000회 한도.
- **결정**: **BigQuery**. 10일 내 데이터 확보 안정성 우선.
- **결과**: 임베딩=PatentSBERTa(영어), 분류=CPC, 언어=영어(번역 불필요), 한국 FTO는 못 봄(한계 명시).
- **기각**: KIPRIS(수집 난이도·KorPatBERT 승인 리스크), USPTO 벌크(BigQuery가 SQL로 더 쉬움).

### ADR-003 — 도메인: 이커머스·콘텐츠 추천 알고리즘
- **상태**: accepted
- **맥락**: 시그니처(청구항 중첩)가 잘 드러나려면 특허 밀집 + 청구항이 기능 단위로 분해되는 분야 필요. 화학·바이오(수치/조성)·하드웨어(물리구조)는 부적합.
- **결정**: **추천 시스템**(이커머스·콘텐츠). CPC `G06F 16/9535`(추천)·`G06Q 30`(이커머스)·`G06N`(학습).
- **결과**: 특허 풍부(Netflix·Amazon·Google), 청구항이 "행동수집→임베딩→유사도추천" 흐름이라 기술요소 매칭 궁합 좋음.
- **기각**: AI 회의록(1순위 후보였으나 추천으로 변경), 핀테크(규제 맥락 복잡), 바이오(분해 난해).
- **확인 필요(open)**: BigQuery dry run으로 추천 CPC 실제 건수·연도범위 확정.

### ADR-004 — 임베딩 모델: PatentSBERTa 768d
- **상태**: accepted (소스 종속)
- **맥락**: 시그니처가 청구항 중첩인데 범용 임베딩이면 약함. ADR-002로 영어 확정.
- **결정**: **PatentSBERTa**(sentence-transformers, CLS pooling 내장, 768d). 폴백 → e5-large(1024d, 차원 변경 필요).
- **결과**: limitation 단위로 임베딩(짧아 512토큰 청크/평균풀링 불필요). `.encode()` 한 줄.
- **기각**: KorPatBERT(한국어 소스였으면 1순위), PatentSBERTa_V2(다국어 — 한국어 갔으면 후보).

### ADR-005 — 벡터 스토어: PostgreSQL 단일 (pgvector + tsvector)
- **상태**: accepted
- **맥락**: ≤5만 건 규모는 웨어하우스·별도 벡터DB 불필요. v2의 Chroma↔PostgreSQL 동기화가 리스크였음.
- **결정**: **PostgreSQL 하나가 운영·근거·검색 겸임.** pgvector(의미)+tsvector(키워드).
- **결과**: 동기화 문제 소거, 컴포넌트 최소화로 4인 완주. 임베딩=`claim_limitations.embedding`·`documents.embedding`.
- **기각**: Chroma(동기화 부담), 메달리온/Glue/Athena/OpenSearch(과설계).
- **갱신 참고(open)**: 서로 다른 PC 접속 위해 공용 DB(RDS) 도입 논의됨 — 본 ADR의 "로컬 단일" 전제와 정합 정리 필요(§5).

### ADR-006 — 프론트엔드: Chainlit 스트리밍 (Streamlit 폴백)
- **상태**: accepted (**D3 게이트 통과** — 브라우저에서 Chainlit SSE 구독·렌더 확인)
- **맥락**: 멀티에이전트 진행 표시가 데모 핵심인데 Streamlit은 rerun 모델이라 실시간 표시가 수작업. Chainlit은 에이전트 단계·스트리밍 기본 내장.
- **결정**: **Chainlit**. 처음부터 Chainlit-first, 단 **얇은 클라이언트 원칙**(로직은 FastAPI, 프론트는 호출+렌더)으로 폴백 보장.
- **결과**: `ui.py`가 `/analyze` SSE 구독 → `stage`(running/done)를 `cl.Step`으로, `report`를 Evidence Board(결정 배지·요약·가설별 근거 표)로 렌더. SSE 파싱·렌더 로직을 `stream_events()`/`_render_board()`로 분리(chainlit 비의존) → 막히면 뷰 레이어만 Streamlit 교체. **얇은 클라이언트 원칙은 ADR-026에서 한 번 더 확인됨**(ui는 DB를 몰라야 정상).
- **기각**: Streamlit 단독(스트리밍 약함), React(10일·데이터팀엔 오버).

### ADR-007 — 스트리밍 구조: FastAPI SSE, D 봉투 겉 + astream_events 안
- **상태**: accepted (구현 완료)
- **맥락**: `/analyze`가 최종 JSON + 스트리밍 둘 다 지원해야. C 그래프를 직접 호출하되 포맷은 D가 통제해야 UI·평가가 안정.
- **결정**: **D가 SSE 이벤트 봉투 포맷 소유**(겉), 내부는 LangGraph `astream_events`로 노드 진행 중계(안).
- **결과**: C가 mock→실 LLM 바꿔도 api.py 불변. **이벤트 봉투 포맷**(§3 참조)이 UI·평가의 계약면.
- **기각**: 단계 하드코딩(C 그래프 붙일 때 재작성), astream_events 날것 노출(포맷 불안정).

### ADR-008 — 스코프: 혼합 (②⑤ full / ③④⑥ light)
- **상태**: accepted
- **맥락**: (가)넓은 실사=②③④⑤ 풀(작업 4배, 절반이 seed 연출) vs (나)좁은 IP=⑤만 풀(작업 1.5배, 시장분석 빈약).
- **결정**: **혼합** — 5가설 보드는 넓게 띄우되 깊이는 ⑤ IP + ②로. Tier 0 = **②⑤ full + ③④⑥ light + ⑦**.
- **결과**: 데모 넓어 보이고 차별점은 IP에 박힘. "경량"은 죽은 칸 아님(ADR-014).
- **기각**: (가)순수 넓이(작업량), (나)순수 IP(빈약해 보임).

### ADR-009 — 에이전트 구성: 7개
- **상태**: accepted
- **결정**: ① Structuring(전처리), ② Market(full), ③ Competitor(light), ④ Tech(light), ⑤ **IP 청구항 중첩(full·시그니처)**, ⑥ Business Model(light), ⑦ **Critic & Experiment(supervisor·척추)**.
- **참고**: VentureScout 원안의 ④ Tech&IP를 ④ Tech + ⑤ IP로 분리. ⑦은 Critic(반박)+Judge(판단) 겸 — 필요시 두 노드로 쪼개도 같은 소유(C).

### ADR-010 — 기계/판정 분리
- **상태**: accepted
- **맥락**: ④⑤ 에이전트가 검색·파싱까지 하면 환각·비결정성. "검색≠분석" 원칙.
- **결정**: **④⑤ 에이전트는 LLM 판정·서술만.** 기계(검색·파싱·임베딩·매핑)는 파이프라인/tool이 DB에 적재, 에이전트는 **읽어와 판정만**.
- **결과**: 경계는 `evidence_id`. 기계가 `ip_overlap_candidates`(`{evidence_id, limitation, similarity}`)를 produce → ⑤가 read. mock 병렬화 가능.

### ADR-011 — 역할 분담 (A/B/C/D)
- **상태**: accepted
- **결정**:
  - **A** 데이터 파이프라인(에이전트 0) — 수집·파싱(독립항·limitation 분해)·적재. 인프라 헤비. D6~ C 보조.
  - **B** 검색·임베딩 + ②③ — 임베딩 모델(PatentSBERTa)·pgvector+tsvector·rerank·검색 tool.
  - **C** 에이전트 플랫폼 + ①④⑤⑦ — 척추(State·계약·few-shot·가드레일 중앙배포). 난도 헤비.
  - **D** 백엔드·UI·평가 + ⑥ — FastAPI·Chainlit·평가 하네스·통합. 팀장 자리.
- **결과**: A·C 양대 헤비(A 인프라·전반피크 / C 난도·전구간). 피크 어긋남 + A→C(D6) 보조로 균형.
- **요건**: C는 팀에서 프롬프트·에이전트 가장 센 사람.

### ADR-012 — 잎/척추 분리
- **상태**: accepted
- **결정**: **잎 에이전트(②③⑤ 등, State 키 하나만 쓰고 서로 호출 안 함)는 분산 가능.** **척추(State 스키마·그래프 배선·⑦ Critic·evidence_id 그라운딩)는 C 단독.**
- **결과**: ⑦은 ②~⑥ 출력을 다 받는 통합지점이라 분산 시 적대검증 일관성 깨짐 → 안 나눔.

### ADR-013 — 표면/의미 변동 처리
- **상태**: accepted
- **맥락**: "에이전트마다 말하는 방식이 달라 통합이 깨진다" 우려.
- **결정**: 변동을 둘로 가름 — **표면 변동(톤·표현·JSON 습관)은 억제**(출력 스키마 고정·구조화 출력 강제·표현 가드레일·공유 few-shot·낮은 temperature, **C가 중앙 정의·배포**), **의미 변동(근거 애매 시 다른 판단)은 살려 ⑦ Critic에 투입**.
- **결과**: 통합을 깨는 건 표면 변동뿐(의미 변동은 JSON 모양 불변). 장치는 "각자 적용"이 아니라 "**공유**"가 핵심.

### ADR-014 — "경량(light)" 정의
- **상태**: accepted
- **결정**: 경량 = **seed 검색 + evidence_id 묶음 + Low confidence + next_experiment.** 근거 없는 한 줄(❌, Critic이 쳐낼 overclaim)이 아님.
- **결과**: 경량 칸도 보드에서 동작(찬반·confidence·다음실험 있음). confidence가 Low로 깔려 정직 + Critic 먹잇감. ⑤는 반드시 풀로 깊이 증명.

### ADR-015 — 입력 정책
- **상태**: accepted
- **맥락**: 녹음 전사 입력은 ①이 추정으로 채워 그라운딩 오염. 계획서형은 분류만.
- **결정**: **Tier 0 = 계획서형 텍스트만.** 전사·음성·파일 업로드는 **Tier 3**(① 앞에 ⓪ 추출·정제 노드 + 사용자 확인).
- **결과**: ① 신뢰도 확보, 그라운딩 본질 집중. `ideas.user_confirmed`가 확인 플래그.

### ADR-016 — 스키마 설계 원칙
- **상태**: accepted
- **결정**: **계약 필드는 strict, payload는 loose.** strict = `evidence_id·grounded_on·confidence·stance·depth`(pydantic 검증). loose = 분석 본문(`payload`/`output_json`, 검증 안 함). `evidence_id`가 그라운딩 원자.
- **결과**: 프롬프트 바뀌어도 스키마 마이그레이션 없음. 통합 면은 안정, 본문은 자유.

### ADR-017 — 스키마 규모: Tier 0 9테이블
- **상태**: accepted
- **맥락**: 팀원 초안 22테이블 — 잘 설계됐으나 10일에 다 못 채움(절반은 Tier 0에 한 행도 안 들어감), 전부 strict라 thrash 위험.
- **결정**: **Tier 0 = 9테이블**(기본 6: ideas·analysis_jobs·hypotheses·documents·evidence_items·agent_runs / 시그니처 3: patent_claims·claim_limitations·ip_overlap_candidates). 22테이블은 **목표 ERD(부록)**. 권장 3컬럼: `analysis_jobs.decision`·`decision_summary`, `agent_runs.target_run_id`.
- **결과**: 파일 업로드 5종 Tier 3로, 분석 정규화(agent_claims·critic_objections·final_decisions 등)는 JSON에 담다 Tier 1~2 승격.

### ADR-018 — Tier 로드맵 + 하드게이트
- **상태**: accepted
- **결정**: **Tier 0 E2E(②⑤ full + ③④⑥ light + ⑦ + Evidence Board) 돌기 전까지 Tier 1+ 금지.** Tier 1: ③⑥ 풀 승격·rerank 고도화. Tier 2: 평가 하네스(Critic ON/OFF). Tier 3: 라이브 freshness·음성입력·산업군 분기.
- **게이트**: D3 임베딩·Chroma 동기화 / D3 Chainlit 스트리밍(✅ 통과, ADR-024/025/026).

### ADR-019 — 평가: process 기반
- **상태**: accepted
- **맥락**: 창업 검증은 outcome 정답 없음.
- **결정**: verdict 정확도 대신 **과정 지표** — Retrieval(Precision@K·Contradiction Coverage), Agent(Groundedness·Overclaim·JSON Validity), **멀티에이전트 효과(Critic ON/OFF 정량화)**, 시스템(latency·cost).
- **결과**: 평가=멀티에이전트 증명. D 소유, 헤드라인 지표.

### ADR-020 — 레포·인프라
- **상태**: accepted
- **결정**: **모노레포**(트랙별 디렉터리, `shared/`에 계약). **Docker Compose**(pgvector + api + ui), 컨테이너 Python **3.11**(로컬 3.14 분리). `.gitignore`로 `.env`·데이터·모델 보호.
- **결과**: 4명이 같은 환경에서 stub로 병렬 착수. **로컬 3.14 직접 실행은 미지원으로 확인됨(ADR-025) → Docker가 표준.**
- **기각**: 트랙별 멀티레포(계약 공유 깨짐), Kubernetes(과설계).

### ADR-021 — GitHub Org · 권한
- **상태**: accepted
- **결정**: 개인 아님 **Organization**(`de-ai-AIAgentPJ-team4`). **Owner 2명**(본인+1) + 나머지 Member·**Write**. `main` 브랜치 보호 + PR 리뷰 1인.
- **결과**: 소유권 팀 귀속, 사고 반경 축소(전원 Owner ❌). 개발 동등성은 Write로 보장.

### ADR-022 — LLM 백엔드: Bedrock ChatBedrockConverse
- **상태**: accepted
- **결정**: AWS Bedrock `ChatBedrockConverse`. Day 1에 콘솔 모델 액세스 신청 + IAM.

### ADR-023 — (구현) D FastAPI 스트리밍 + State reducer
- **상태**: done (**실제 레포 랜딩 완료** — 이전엔 문서만 done, 코드 미푸시)
- **맥락**: `/analyze` SSE 구현 중 병렬 노드(②③④⑤⑥)가 `findings`에 동시 기록 → `INVALID_CONCURRENT_GRAPH_UPDATE`. (레포 클론 결과 이 버그가 그대로 재현됨 — reducer가 푸시 안 됨.)
- **결정**: `shared/state.py`의 `findings`에 **reducer(`operator.add`)**, `evidence_pool`에 dict 머지 reducer 추가. api.py는 단일 실행(astream_events에서 단계 중계 + 노드 출력 누적, ainvoke 재실행 제거).
- **결과**: job→①→②③④⑤⑥ 병렬→⑦→report E2E 동작 확인(mock, 단계 14개). **이건 C 소유 State 계약 변경 — C에 공유 필요(리뷰 대기).**
- **api.py 봉투 수정**: 기존 stub의 `stage` 이벤트에 `"type":"stage"` 누락 → §3 계약대로 추가. `report`에 `summary`·`confidence`·`objections`·`next_experiments`·`findings` 동봉. `DEMO_DELAY` env(기본 0.4) 노출.

### ADR-024 — (구현) D Chainlit SSE 구독 (D3 게이트)
- **상태**: done
- **맥락**: ADR-006 Chainlit-first의 D3 게이트 — 프론트가 `/analyze` SSE를 실제로 구독·렌더할 수 있는지 검증 필요.
- **결정**: `app/ui.py`를 **얇은 클라이언트**로 구현. `stream_events(idea)`가 httpx로 SSE 라인 파싱→이벤트 dict yield, `@cl.on_message`가 `stage`→`cl.Step` 생성/업데이트(병렬 단계 dict 추적), `report`→`_render_board()` 마크다운. **chainlit 미설치 시에도 `stream_events`/`_render_board` import 가능**하도록 chainlit를 try/except 가드 → Streamlit 폴백·단위테스트 재사용.
- **결과**: live FastAPI 대상 E2E 검증(이벤트 순서 job→stage×14→report→job, Evidence Board 렌더 정상). `requirements.txt`에 httpx 명시, compose `ui` 서비스에 `API_URL=http://api:8000`, `.env.example`에 `API_URL`/`DEMO_DELAY` 추가.
- **기각**: cl.Step async context-manager 자동 닫기(병렬 running/done이 별 이벤트라 수동 lifecycle 필요), UI에서 직접 graph 호출(얇은 클라이언트 원칙 위배).

### ADR-025 — (운영) 실행 환경: 로컬 3.14 배제, Docker(3.11) 표준 확정
- **상태**: accepted (D3 게이트 검증 중 확정)
- **맥락**: D3 게이트(Chainlit 스트리밍)를 **로컬(Windows, Python 3.14)**에서 검증하려다 연쇄 실패 —
    * `pip install` 시 langgraph 설치 난항 + dbt-core 의존성 충돌 경고(cffi/protobuf/urllib3).
    * uvicorn 기동 시 `ModuleNotFoundError: langgraph`(설치/인터프리터 불일치).
    * chainlit 기동 후 `localhost:8001` 정적파일 서빙에서 **`anyio.NoEventLoopError`** — Python 3.14 + anyio/starlette 비호환. 코드 문제가 아니라 런타임(3.14)이 너무 최신이라 에이전트/웹 스택이 미지원.
- **결정**: **로컬 직접 실행을 표준에서 배제. Docker Compose(컨테이너 Python 3.11)를 표준 실행/검증 환경으로 확정**(ADR-020 재확인). 코드는 `volumes: .:/app` 마운트로 로컬 파일을 그대로 쓰되, 런타임만 3.11 컨테이너가 제공. 즉 "내 코드를 격리된 3.11 환경에서 실행".
- **결과**: 3.14발 오류 전부 소거. `docker compose up`으로 db+api(8000)+ui(8001) 기동, 브라우저 `localhost:8001` Evidence Board 정상 렌더 확인. compose `env_file: .env`가 `.env` 파일을 요구 → `.env.example` 복사로 생성(`.env`는 커밋 금지, .gitignore 확인). 로컬 개발이 꼭 필요하면 **3.11 venv 별도 생성**(3.14와 혼합 금지).
- **기각**: 3.14 로컬 강행(스택 미지원), 누락 패키지 개별 설치로 증상만 가리기(ADR-026 참조).

### ADR-026 — (구현) ui 컨테이너 DATABASE_URL 차단 (Chainlit 데이터레이어 off)
- **상태**: accepted (D 소유 — compose `ui` 설정)
- **맥락**: Docker로 띄운 뒤 `localhost:8001`이 흰 화면. 실제로는 **500 에러** — Chainlit이 환경변수 `DATABASE_URL`(우리 특허 DB용)을 감지하면 **자체 채팅 영속화(데이터 레이어)**를 자동 활성화하는데, 그 레이어가 `asyncpg`를 import → 이미지에 미설치라 프론트 로드 시 호출하는 `/project/settings`(`get_data_layer()`)가 매번 실패.
- **결정**: ui 서비스에서 `env_file: .env`를 **제거**하고 필요한 것만 명시 주입(`environment: API_URL`), `DATABASE_URL: ""`로 데이터레이어 비활성화. **ui는 DB를 몰라야 정상**(ADR-006 얇은 클라이언트) — 데이터 접근은 api 경유.
- **결과**: 500 소거, Evidence Board 정상 렌더. ui 컨테이너가 불필요한 DB/Bedrock 자격증명을 안 들고 있게 되어 **결합도·보안 동시 개선**. 잘못 주입된 설정 제거이지 우회 패치(누더기) 아님 — 오히려 ADR-006 의도를 코드로 정직하게 드러냄.
- **기각**: `asyncpg` 설치로 500만 제거(Chainlit이 우리 DB에 자기 채팅 테이블 생성 → 스키마 오염, 의도와 무관 — 전형적 증상 가리기).

### ADR-027 — ⑥ BM 노드 설계: 순수 추론(a) + 검색·LLM 자리 예약
- **상태**: accepted (D 단독 결정 — D 소유 영역, 팀 전달 의무 없음)
- **맥락**: ⑥ BM은 light 에이전트. 검색 grounding(b)이 **완성 상태론 더 우수**하나 —
    1. B의 `retrieve()`가 아직 mock이라 지금 붙여도 가짜 근거(`ev_mock_0001`)만 옴 → 이득 0, B 실연결 후에야 가치 실현.
    2. light 정의상 full(②⑤)만큼 검색을 무겁게 쓰지 않음(ADR-008/014).
    3. LLM 연결은 C 소유(프롬프트·가드레일 중앙배포, ADR-013) — D가 BM에 직접 Bedrock 붙이면 충돌.
- **결정**: **검색 (a) 순수 추론** 채택 + 검색·LLM **호출 자리만 주석으로 명시 예약**.
    * BM 노드는 입출력 계약(`AgentFinding`)·`payload` 구조를 D 영역에서 완결.
    * `retrieve()` 호출은 `# TODO(B): retrieve("H_bm","pricing") 실연결 시 grounding` 주석으로 자리만.
    * LLM 판단은 `# TODO(C): Bedrock 중앙배포 후 수익구조·단가·시장 판단` 주석으로 자리만.
    * `grounded_on`은 계약상 필수(비면 검증 실패) → 지금은 mock evidence_id 유지.
- **결과**: 지금은 (a)로 동작(mock 본문), B·C 실연결 시 **주석 두 곳을 코드로 바꾸면 (b)로 무재작업 승격**. `payload`는 BM 분석 본문(수익모델·가격가설·시장규모 신호·단위경제 LTV/CAC 방향·핵심 리스크) — **loose 영역(ADR-016)이라 ADR에 고정하지 않고 노드 docstring에 명시**(프롬프트 변경 시 ADR 마이그레이션 불필요).
- **승격 조건(추후)**: B `retrieve("H_bm","pricing")` 실데이터 반환 → 검색 주석 해제 / C BM용 프롬프트·가드레일 배포 → LLM 주석 해제.
- **기각**: (b) 지금 검색 붙이기(mock 위라 이득 0·B 실연결 때 재작업), D가 Bedrock 직접 호출(C 소유 LLM 연결 침범).

---

## 2. 레포 상태

**구조** (push 완료, `de-ai-AIAgentPJ-team4/venturescout`)
```
shared/contracts.py   계약(strict/loose): EvidenceItem·Hypothesis·AgentFinding·OverlapCandidate·CriticResult
shared/state.py       VentureScoutState (findings/evidence_pool reducer 포함) ★ADR-023
db/init.sql           9테이블 DDL + pgvector hnsw + tsvector gin (권장 3컬럼 포함)
db/schema.dbml        dbdiagram.io용
data/                 Track A — README + stub
retrieval/tools.py    Track B — retrieve()/vector_search() mock 반환 ★mock 병렬화 핵심
agents/graph.py       Track C — LangGraph 7노드 골격(① + ②③④⑤⑥ 병렬 + ⑦), 현재 mock·동기
app/api.py            Track D — /analyze SSE 스트리밍 ★구현 완료 (ADR-023)
app/ui.py             Track D — Chainlit Evidence Board ★구현 완료 (ADR-024)
tests/test_contracts.py  계약 검증 (green)
docs/plan_v3.md, schema_tier0.md  기획·스키마
docker-compose.yml(ui는 env_file 대신 API_URL만·DATABASE_URL="" ★ADR-026), Dockerfile, .gitignore, .env.example, requirements.txt(httpx 포함)
```

**구현 상태**
- ✅ 계약 코드·DDL·docker-compose·트랙 stub
- ✅ `pytest` green / `agents/graph.py` mock E2E 동작 (state reducer 적용 후)
- ✅ `app/api.py` SSE 실 스트리밍 — `astream_events`로 7노드 중계, §3 봉투 준수
- ✅ `shared/state.py` `findings`/`evidence_pool` reducer (ADR-023, ★C 리뷰 대기)
- ✅ `app/ui.py` Chainlit SSE 구독 → 단계 렌더 → Evidence Board (ADR-024)
- ✅ **Docker 실행 검증 — 브라우저 `localhost:8001` Evidence Board 실렌더** (ADR-025/026, D3 게이트 통과)
- ✅ `D` 브랜치 푸시 완료 (PR 머지 대기)
- ⬜ A·B·C 실데이터·실LLM / ⑥ BM 본문(설계는 ADR-027 확정) / 평가 하네스

**실행** (표준 = Docker, 로컬 3.14 미지원 — ADR-025)
```bash
# 표준: Docker (컨테이너 Python 3.11)
copy .env.example .env                  # (PowerShell: Copy-Item) compose env_file 요구
docker compose up                       # db + api:8000 + ui:8001
#   → 브라우저 localhost:8001 에서 Evidence Board 확인

# 환경변수 변경 반영이 안 되면:
docker compose up --build               # 또는 --force-recreate

# 로컬 개발이 필요하면 반드시 3.11 venv (3.14 금지):
#   py -3.11 -m venv .venv ; .venv\Scripts\activate ; pip install -r requirements.txt
#   uvicorn app.api:app --reload --port 8000   /   chainlit run app/ui.py --port 8001
```
> ⚠️ PowerShell 주의: `curl`은 `Invoke-WebRequest` 별칭이라 `-H`/공백/한글이 깨짐 → SSE 테스트는 `curl.exe` 사용 또는 브라우저 UI로 확인.

---

## 3. 살아있는 계약 (다음 작업이 의존)

**SSE 이벤트 봉투 포맷** (D 소유, UI·평가가 의존)
```
{"type":"job",   "status":"running|done|failed", "stage":null}
{"type":"stage", "stage":"<노드명>", "label":"<표시>", "status":"running|done"}
{"type":"report","decision":"go|pivot|kill|more_research", "summary":"...", "findings":[...]}
```

**C ↔ D 계약** (api.py가 C에 요구)
1. 각 노드는 `async` 함수 **권장**(sync도 astream_events가 중계하나, 실 LLM I/O 병렬성 위해 async 권장 — §5 open)
2. 노드 진입 시 노드 이름이 stage로 잡히게(현 구조 OK)
3. critic 노드 최종 출력에 `decision`·`summary` 포함(`CriticResult`)

**기계 ↔ 에이전트 계약** (B → C, ADR-010)
- B: `vector_search() → list[OverlapCandidate]`, `retrieve() → list[EvidenceItem]` (반환 타입 고정, 내부만 교체)
- C(⑤): `OverlapCandidate` 읽어 판정만

**State 계약 변경 알림** (ADR-023) — `findings`/`evidence_pool` reducer 추가됨. C는 노드가 이 키에 기록할 때 reducer 전제. ★C 리뷰 필요(§5).

**BM 노드 승격 포인트** (ADR-027) — `bm_node` 내 `# TODO(B)`(검색)·`# TODO(C)`(LLM) 주석 2곳. 실연결 시 (a)→(b) 승격.

---

## 4. 트랙별 다음 작업 (Day 1~)

**A (데이터)** — BigQuery dry run(추천 CPC 건수·연도) → 수집 → 독립항/limitation 분해 → `documents`·`patent_claims`·`claim_limitations` 적재. (소스=BigQuery, 분류=CPC 확정)

**B (검색·임베딩)** — PatentSBERTa 768d 임베딩 파이프라인 → `claim_limitations.embedding`·`documents.embedding` → pgvector+tsvector 하이브리드+rerank → `tools.py` mock을 실검색으로 교체(시그니처 고정). ⑥ BM 승격을 위해 `retrieve("H_bm","pricing")` 경로도 고려.

**C (에이전트)** — `agents/graph.py` 노드를 **async + Bedrock 실연결**. few-shot·가드레일·출력 pydantic 중앙 배포. ⑤ IP 풀 판정(청구항 요소별 중첩), ⑦ Critic 적대검증. **State reducer 전제 확인(ADR-023 리뷰)** + BM 노드 LLM 자리(ADR-027) 배포.

**D (백엔드·UI·평가)** — ✅ Chainlit SSE 구독·Evidence Board(D3 게이트 통과, 브라우저 검증). ✅ 실행 환경 Docker 확정(ADR-025/026). ▶ **다음: ⑥ BM 노드 본문 구현(ADR-027 설계대로) → 평가 하네스(Critic ON/OFF)** → 통합. `D` 브랜치 PR 머지(state.py 포함 → C 리뷰어 지정). (`/health` 완비, job 상태는 SSE `job` 봉투로 노출 중. 영속 job 조회 필요해지면 `/jobs/{id}` 추가 검토.)

**공통 Day 1** — Bedrock 모델 액세스+IAM 신청 / 팀원 org 초대(Member·Write) / 브랜치 보호 / `.env`는 절대 커밋 금지.

---

## 5. 미해결·확인 필요 (open)

- [ ] BigQuery 추천 CPC 실제 건수 dry run (ADR-003) — 너무 많으면 연도 축소, 적으면 CPC 확장
- [ ] e5 폴백 시 `vector(768)→vector(1024)` 차원 변경 필요 (ADR-004)
- [ ] 권장 3컬럼(decision·decision_summary·target_run_id) 최종 채택 여부 — 현재 DDL에 포함, 뺄 거면 해당 줄 삭제 (ADR-017)
- [x] D3 게이트(Chainlit 스트리밍) 통과 → ADR-006 accepted 확정 (브라우저 E2E 검증, ADR-024/025/026). 임베딩 동기화 게이트는 B 진행 후 확인.
- [ ] **★C 리뷰**: `shared/state.py` reducer 변경(ADR-023) — C 소유 척추 계약. C가 노드 async+실LLM 전환 시 reducer 전제 유지 확인.
- [ ] **★D 브랜치 PR 머지** — `D` → main(또는 dev), ADR-021 1리뷰. state.py 포함이므로 **C를 리뷰어로 지정**.
- [ ] **RDS(공용 DB)** — 서로 다른 PC 접속 위해 도입 논의됨(설정된 것으로 보임). **결정 ADR 미작성** → 호스팅 방식·자격증명 공유·ADR-005(로컬 단일) 정합 정리 필요. (`.env`에 RDS 접속정보 들어가면 커밋 금지 재확인)
- [ ] DEMO_DELAY(기본 0.4s) — 실 LLM 붙으면 0으로 (env로 분리 완료, 배포 시 0 설정)
- [ ] 그래프 노드 현재 sync — astream_events는 sync도 중계되나, C가 실 LLM 붙일 때 async 전환 권장(I/O 병렬성). §3 C↔D 계약 #1 참조.
