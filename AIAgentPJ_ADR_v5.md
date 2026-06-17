# VentureScout — Architecture Decision Record (ADR)

> **목적**: 이 문서 하나만 보고 작업을 이어갈 수 있도록 모든 결정·구현·레포 상태·다음 작업을 기록.
> **버전**: v5 (v4에서 이어받음 — C 계약 수렴(agent_runs/AgentRun)·job_id 오케스트레이션 ADR-032 추가)
> **최종 갱신**: Day 0++ (RDS 연결 전환·스키마 동기화 ADR-031. **+ C 계약 수렴 + job_id 오케스트레이션 ADR-032** — D 표면(api/ui/harness/graph)을 C의 `agent_runs`/`AgentRun`에 맞추고, `/analyze`에서 job_id 발급→ideas+analysis_jobs 적재까지 로컬 docker postgres E2E 검증, `D` 커밋 `17470b2`. ADR-023 reducer는 ADR-032가 대체.)
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
- **D: ⑥ BM 노드 본문 구현** — `bm_node`+`_bm_payload`, payload 5필드(전부 str·`[MOCK]`), `state["idea"]` 입력 배선, `# TODO(B)/(C)` 자리 예약 (ADR-028, ADR-027 설계 실행). `build_graph().invoke` 검증 — findings에 bm payload 5필드 정상 누적
- **D: 평가 하네스 골격 구현** — `eval/harness.py`, **Critic ON/OFF 비교(헤드라인)** + json_validity·groundedness·overclaim·latency. 지금 계산 가능한 건 실측, retrieval·cost는 None+TODO (ADR-029, ADR-019 실행). 검증 — `decision_changed: true`(OFF=go → ON=more_research)로 Critic 교정 효과 확인
- **D: `D` 브랜치 푸시 완료** — api/ui/state/requirements/compose/.env.example (※ bm_node·harness는 작업트리 완성, **커밋 대기**)
- **D: AWS RDS 연결 전환 + 스키마 문서 동기화** (ADR-031) — `.env`/`docker-compose`를 로컬 postgres → RDS(verify-full+CA번들)로, `db/init.sql`·`db/schema.dbml`을 RDS 실 스키마와 **바이트 단위 일치**하도록 재작성, 읽기전용 조회도구 `db/introspect_schema.py` 추가. track-b `config.py` 스모크로 머지 시 무재작업 연결 확인. **RDS는 미변경**(읽기 전용). `D`에 커밋 6건 완료.
- **D: C 계약 수렴(agent_runs/AgentRun) + job_id 오케스트레이션** (ADR-032) — `shared/contracts.py`·`state.py`를 ko-agent판 채택(머지 아님, 2파일만), `graph.py`/`api.py`/`ui.py`/`harness.py`를 `agent_runs`로 적응(signal·next_experiment·BM payload는 `output_json` loose). `/analyze`가 `job_id` 발급(ideas+analysis_jobs INSERT)→state+config 주입→종료 시 status/decision 기록. **로컬 docker postgres E2E로 적재 검증** + `pytest` 3 passed. ADR-023 reducer 대체. `D` 커밋 `17470b2`.
- 검증: 서버 기동 → `/analyze` E2E(mock 그래프) → 단계 14개 + report 정상 / `pytest` green

**⚠️ 레포 vs ADR 격차 (해소됨)** — 이전 ADR은 "api.py 스트리밍·reducer 완료"로 기록됐으나 **푸시된 코드엔 미반영**(api.py는 그래프 미호출 stub, state.py reducer 없음, 병렬노드 `INVALID_CONCURRENT_GRAPH_UPDATE` 재현). 이번에 실제 랜딩하여 ADR과 코드 일치.

**다음 할 일(우선순위)**
1. A: BigQuery dry run으로 추천 도메인 특허 건수 확인 → 수집·적재
2. B: PatentSBERTa 임베딩 파이프라인 + 하이브리드 검색 tool
3. C: ①④⑤⑦ 노드 실제 LLM 연결(현재 mock·sync) + **state.py reducer 변경 리뷰**
4. D: ✅ ⑥ BM 노드 본문(ADR-028)·평가 하네스 골격(ADR-029) 완료 → **남은 건 `D` 브랜치 커밋 + PR 머지**(state.py 포함 → C 리뷰어). 하네스 실측은 B·C 실연결 후.

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
- **갱신 참고(resolved, ADR-031)**: 서로 다른 PC 접속 위해 공용 DB를 **AWS RDS PostgreSQL 16으로 전환**(ADR-031). "PostgreSQL 단일 스토어(pgvector+tsvector)" 원칙은 유지 — 호스팅만 로컬 docker → RDS이고, `init.sql`은 로컬 pgvector 컨테이너에도 동일 적용 가능(검증됨).

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

### ADR-028 — (구현) ⑥ BM 노드 본문 — ADR-027 설계 실행
- **상태**: done (작업트리 완성, `D` 브랜치 커밋 대기)
- **맥락**: ADR-027에서 (a) 순수 추론 + 검색·LLM 자리 예약으로 설계 확정 → 본문 구현 차례. 기존 `bm_node`는 `_leaf_finding("bm","light")`로 payload 빈 stub.
- **결정**: `agents/graph.py`의 `bm_node`만 교체 + BM 전용 헬퍼 `_bm_payload(idea)` 추가(다른 노드·`build_graph`·import·`_leaf_finding` 불변).
    * `_bm_payload`는 payload 5필드(`revenue_model`·`pricing_hypothesis`·`market_size_signal`·`unit_economics`·`key_risk`, 전부 str·`[MOCK]` 접두)를 반환. **5필드 스펙은 docstring에 명시**(loose 영역 ADR-016 — ADR 마이그레이션 회피).
    * `bm_node`는 `state.get("idea",{})`를 읽어 `_bm_payload`로 넘김 — 지금 mock이라 값 미사용이나 **입력 배선을 미리 완결**(C가 LLM 붙일 때 입력 연결돼 있음).
    * `AgentFinding(agent="bm", hypothesis_id="H0", signal=…, grounded_on=["ev_mock_0001"], confidence="low", depth="light", next_experiment=…, payload=_bm_payload(idea))` 반환. light 취지(ADR-014): 근거묶음+Low+next_experiment로 ⑦ Critic 검증 가능, overclaim 금지.
    * 승격 자리: `# TODO(B): retrieve("H_bm","pricing")` / `# TODO(C): Bedrock으로 payload 5필드`.
- **결과**: `build_graph().invoke`로 검증 — ②③④⑤⑥ 병렬 findings에 bm finding 누적, payload 5필드 전부 str·`[MOCK]`, pydantic 재검증 통과. **스키마 대조 정정**: `AgentFinding`엔 `stance`/`evidence_id` 필드 없음(그건 `EvidenceItem`/`OverlapCandidate` 소속) — ADR-016의 strict 원자 목록을 finding 필드로 오해 말 것. finding 필수=agent·hypothesis_id·signal·grounded_on·confidence·depth.
- **검증 환경**: 컨테이너 동등(3.11+) `python -m agents.graph` / `build_graph().invoke`. 로컬 3.14 금지(ADR-025). ※ reducer 있는 `D` 브랜치 state.py 기준 — `main`엔 reducer 없어 bm_node 무관하게 병렬노드 깨짐(PR 머지 선행).
- **기각**: `_leaf_finding` 재사용(payload 못 채움), payload 필드를 ADR/스키마에 고정(loose 원칙 위배·프롬프트 변경마다 마이그레이션).

### ADR-029 — (구현) 평가 하네스 골격 — ADR-019 실행
- **상태**: done (골격, 작업트리 완성·커밋 대기) — 실측은 B·C 실연결 후
- **맥락**: ADR-019에서 process 기반 평가·헤드라인=Critic ON/OFF로 결정 → 실행 차례. 레포에 평가 디렉터리 없음(greenfield). 전부 mock이라 의미있는 수치는 아직 안 나옴.
- **결정**: 신규 `eval/harness.py`(graph.py 불변). 지금 계산 가능한 지표는 실측, 실데이터/실LLM 의존 지표는 `None`+TODO로 자리만.
    * `build_graph_no_critic()` — Critic OFF 대조군. graph.py 노드(structuring+분석5)를 재사용하되 ⑦/엣지만 빼고 별도 컴파일(원본 불변).
    * agent 지표 — `json_validity`(계약 재검증 비율)·`groundedness`(grounded_on 비율)·`overclaim_count`(근거 없이 high confidence 프록시).
    * **헤드라인 `compare_critic(idea)`** — 같은 idea를 ON/OFF로 돌려 `decision_changed`·`objections_added`·`overclaims_in_off`·`critic_latency_overhead_s` 산출. OFF 판정=낙관 규칙(`_naive_decision`).
    * `evaluate(idea)` — 묶음 반환. `precision_at_k`·`contradiction_coverage`(B 실검색+정답라벨 필요)·`cost_usd`(Bedrock 토큰) = `None`+TODO.
- **결과**: `python -m eval.harness` 실행 — `decision_changed: true`(OFF=`go` → ON=`more_research`). **mock 단계에서도 Critic이 낙관 편향을 교정함을 정량 입증** = ADR-019 헤드라인. 나머지 수치는 자리만(승격 시 채워짐).
- **승격 조건(추후)**: C ⑦ 실LLM → `objections_added`·`decision` 실값 / B 실검색+라벨 → `precision_at_k`·`contradiction_coverage` / C Bedrock → `cost_usd`. Tier 2(ADR-018) 진입점.
- **검증 환경**: 컨테이너 동등(3.11+) `python -m eval.harness` 또는 `docker compose run --rm api python -m eval.harness`. 로컬 3.14 금지. ※ ADR-028과 동일하게 reducer 있는 `D` 브랜치 기준.
- **기각**: graph.py에 critic 토글 플래그 추가(척추 오염·C 소유 침범 — 하네스에서 별도 배선이 더 깨끗), mock 단계에서 precision 억지 측정(정답 라벨 없어 무의미).

### ADR-030 — 하네스 분산 대응: N회 반복·집계 (실 LLM 승격과 함께)
- **상태**: accepted (설계만 — 구현은 C ⑦ 실 LLM 승격 시점으로 보류)
- **맥락**: 현 `compare_critic`은 같은 idea를 ON/OFF로 **각 1회** 돌려 `decision_changed`(불리언 1개)·`objections_added`를 보고. mock 노드는 결정적이라 1회로 충분하나, C가 ⑦ Critic을 Bedrock 실 LLM으로 붙이면 비결정성에 노출 — 같은 idea도 회마다 판정이 뒤집히거나 objections 수가 들쭉날쭉할 수 있음. 1회 측정으론 "Critic의 효능"인지 "그 회의 우연"인지 구분 불가(표본 1).
- **결정**: 승격과 **동시에** `compare_critic`을 N회 반복 → 집계로 교체. 반환을 단발 값에서 분포 지표로 격상.
    * `change_rate` — N회 중 `decision_changed` 비율(0.0~1.0). 헤드라인의 진짜 답("Critic이 판정을 교정하는 경향").
    * `objections_mean`·`objections_stdev` — 반박 수 평균·표준편차. stdev가 출력 일관성 척도(작으면 안정, 크면 못 믿음).
    * `on_decision_distribution` — ON 판정 분포(go N1·kill N2…). 한 판정 수렴 = 신뢰, 흩어지면 모호.
    * 1차 방어(분산 자체를 줄임): 판정 노드 temperature↓ + few-shot로 판정 기준 고정(ADR-013 연장 — 표면 변동 억제). 단 ⑦의 "의미 변동"까지 죽이지 않게 과도한 temperature 억제는 경계.
    * 기존 `compare_critic`(1회)은 내부 단위로 재사용 — 반복 루프가 그걸 N번 호출해 모으는 구조(무재작업).
- **결과(예상)**: "Critic이 판정을 바꿨다(1회 True)" → "80% 확률로 교정, 반박 5±1개로 안정"처럼 통계적 진술로 격상. 발표 설득력 ↑.
- **표본·비용 타협**: N회는 Bedrock 호출 N배 → `cost_usd`도 N배(ADR-029 cost 칸과 연동). 10일 타임라인상 무한 반복 불가 → **idea 2~3개 × 5회** 수준으로 표본·비용 절충. 결과에 "표본 한계" 명시.
- **기각**: 통계 검정(OFF/ON 분포 유의성)까지 — 10일 프로젝트엔 과설계, 변화율+평균+stdev로 충분. mock 단계 선제 구현(잴 분산이 없어 change_rate∈{0,1}·stdev=0인 빈 골격만 생김 — ADR-029 None 칸과 같은 논리로 승격 시 구현).

### ADR-031 — (구현) AWS RDS 연결 전환 + 스키마 문서 동기화
- **상태**: done (`D` 브랜치 커밋 6건 완료, PR 머지 대기) — §5/ADR-005의 "RDS 공용 DB·결정 ADR 미작성" open 항목 해소.
- **맥락**: §5에 "RDS 도입 논의됨(설정된 것으로 보임)·결정 ADR 미작성"으로 남아있던 항목. 확인 결과 팀이 **이미 RDS PostgreSQL 16.13(ap-northeast-1) 인스턴스를 프로비저닝 + 스키마(9테이블+pgvector) + 실데이터까지 적재**해둠 — A 트랙 진행분으로 `ideas` 8 · `documents` 3,500+(전부 patent, CPC `G06Q30`계열 = ADR-003 도메인 일치) · `patent_claims` 6.3만 · `claim_limitations` 13.3만, 임베딩 백그라운드 적재 중(~40%). 그러나 레포의 `.env`/`docker-compose`는 로컬 docker postgres(localhost) 전제였고, RDS 실제 스키마는 커밋된 `db/init.sql`과 세부가 어긋나 있었음(문서-실물 격차).
- **결정**: **연결 설정만 RDS로 전환 + 스키마 문서를 RDS 실제 상태에 맞춰 재작성. RDS 자체는 변경하지 않음(읽기 전용 조회만).** 앱 DB 접근 코드(track-b의 `config.py`·`pipeline`·`search`)는 손대지 않음 — 이 브랜치(D)엔 원래 없고, 환경변수 이름을 track-b `config.py`가 읽는 `POSTGRES_*`/`DATABASE_URL`로 맞춰 **머지 시 무재작업 연결**되게만 함.
    * `.env`/`.env.example`: localhost → RDS 엔드포인트, `sslmode=verify-full` + RDS 글로벌 CA 번들(`db/certs/rds-global-bundle.pem`, 공개 파일·비밀 아님). `DATABASE_URL`에 SSL 파라미터 동봉, 임시 `DB_*` 제거.
    * `docker-compose.yml`: 로컬 `db` 서비스·`pgdata`·`init.sql` 마운트 제거(api/ui만 — DB는 `.env` 경유 RDS). `ui`의 `DATABASE_URL:""`는 유지(ADR-026).
    * `db/init.sql`·`db/schema.dbml`: RDS 실 스키마를 `db/introspect_schema.py`(신규·읽기전용 조회 도구)로 덤프해 재작성. RDS의 quirk를 **고치지 않고 그대로 반영 + 주석**: `gen_random_uuid()`(pgcrypto)·pg_trgm 확장, varchar 길이 무제한, NOT NULL/CHECK/DEFAULT 추가, FK 전부 `ON DELETE CASCADE`, `created_at` 6테이블 추가, numeric 정밀도 유지(`numeric(5,4)`/`(3,2)`), `agent_runs.target_run_id` 자기참조 FK. **RDS-side 잠재 결함 2건도 문서화만**: `patent_claims`·`claim_limitations` 중복 UNIQUE 각 2건(마이그레이션 2회 적용 추정), FTS가 `simple`→`english`(한국어 특허 텍스트엔 부적합 가능).
- **결과**:
    * verify-full로 서버 인증서 검증까지 통과해 안전 연결 확인(PostgreSQL 16.13).
    * **라운드트립 증명**: 재작성한 `init.sql`을 빈 `pgvector:pg16` 컨테이너에 적용 → 스키마 덤프가 RDS와 **바이트 단위 일치(diff 0)**. 첫 시도에 numeric 정밀도 누락을 라운드트립이 잡아내 수정(요약만으론 못 봤을 차이).
    * **Tier A 스모크**: track-b `config.py`를 앱처럼 import → `config.db_dsn`으로 RDS 접속 → 실데이터 read 성공. 환경변수 정렬이 맞아 **track-b 머지 시 추가 수정 0** 확인.
    * 설계·계획 문서: `docs/superpowers/specs/…-rds-connection-switch-design.md`, `…/plans/…-rds-connection-switch.md`.
    * **ADR-005 정합**: "PostgreSQL 단일 스토어(pgvector+tsvector)" 원칙은 유지 — 호스팅만 로컬 docker → RDS. `init.sql`은 여전히 로컬 pgvector 컨테이너에도 동일 적용 가능(라운드트립으로 검증됨).
- **범위 밖(미수행)**: 앱이 실제 RDS를 읽고 **쓰는** E2E(track-b DB 코드 머지 후 가능 — 이 브랜치엔 쿼리 코드 없음), RDS-side 결함 수정(운영 공유 DB·팀 논의 후 별도), 보안그룹 인바운드(현 테스트 PC 공인 IP만 허용 추정 — 다른 PC·배포 시 규칙 추가 필요, AWS 콘솔 권한 요함), IAM DB 인증(정적 비밀번호 유지).
- **기각**: `sslmode=require`만(암호화O·인증서검증X → MITM 취약), RDS 스키마를 `init.sql` 기준으로 재생성(실데이터 13만행+임베딩 진행 중 → 파괴적), `DB_*` 별도 이름 유지(track-b `config.py`와 불일치 → 머지 시 재작업).

### ADR-032 — (구현) C 계약 수렴(agent_runs/AgentRun) + job_id 오케스트레이션
- **상태**: done (`D` 브랜치 커밋 `17470b2`, 로컬 docker postgres E2E 검증). ADR-023의 `findings`/`evidence_pool` reducer를 **대체**(superseded).
- **맥락**: D·B는 Day-1 base 계약(`findings`/`AgentFinding`)을, C(ko-agent)는 **DB 테이블 행을 그대로 계약화**한 `agent_runs`/`AgentRun` + `job_id` 필수 모델로 진화시켜 둘이 갈라져 있었음(같은 이름 `EvidenceItem`도 C는 `job_id`·`relevance_score` 필수 추가, `OverlapCandidate`→`IPOverlapCandidate` 별칭). 척추(State·계약)는 C 소유(ADR-011/012)라 **C 계약으로 수렴**하는 게 자연스러움. 또한 C·B는 `job_id`를 받아 쓰는데(C는 `state["job_id"]`, B persistence는 `RunnableConfig.configurable.job_id`) **발급 주체가 없었음** → 발급은 진입점 D 몫(ADR-007).
- **결정**: **머지 없이 C의 계약 파일 2개만 채택 + D 표면을 그 계약에 맞춰 적응 + job_id 발급을 D 진입점에 신설.**
    * `shared/contracts.py`·`shared/state.py` ← ko-agent판 **그대로 채택**(바이트 동일). C 소유 단일 진실원천. (ko-agent 전체 머지는 기각 — `agents/nodes/*` 등 C 구현까지 끌려옴.)
    * `agents/graph.py`: 노드 봉투 `findings/AgentFinding` → `agent_runs/AgentRun`. **signal·next_experiment·BM payload 5필드는 strict 필드가 아니라 `AgentRun.output_json`(loose, ADR-016)에 담음**. throwaway `retrieve()/vector_search()` 호출 제거(C의 `EvidenceItem`은 job_id 필수라 mock 생성 시 검증 실패·결과도 미사용이었음).
    * `app/api.py`: **job_id 오케스트레이션** — `/analyze`에서 `ideas` INSERT → `analysis_jobs` INSERT(status=running)로 `job_id` 발급(FK: ideas←analysis_jobs), **초기 state + RunnableConfig 둘 다**에 주입(C·B 두 소비자 충족), 종료 시 `analysis_jobs.status`(done/failed)·`decision` 기록. DB 연결은 D가 독립적으로 최소 보유(`DATABASE_URL`) — config.py(B) 머지 시 교체 가능.
    * `app/ui.py`: report `findings`→`agent_runs` 렌더, `signal`을 `output_json`에서 방어적으로 읽음(loose라 부재 가능).
    * `eval/harness.py`: `AgentRun`/`agent_runs`로 재배선(json_validity·groundedness·overclaim·헤드라인).
    * `tests/test_contracts.py`: C 계약 기준 + `grounded_on` min_length=1 거부 테스트(근거 없는 주장 금지 ADR-014).
- **결과**:
    * **로컬 docker postgres**(init.sql 적용)에 `/analyze` end-to-end: job_id 발급(실 UUID) → stage 14개 스트리밍 → `agent_runs` 5개(bm output_json에 signal·next_experiment·payload 5필드 보존) → **`analysis_jobs` 행 적재(status=done, decision) + ideas 행 생성** 확인.
    * `pytest` 3 passed, 전 모듈 import OK.
    * 매핑 메모: `agent`→`agent_name`·`payload`→`output_json`(순수 개명) / `signal`·`next_experiment`(strict→loose output_json) / `EvidenceItem`·`IPOverlapCandidate` 필수 필드 추가(job_id 등) / `objections` list[dict]→list[str](ui 이미 방어).
- **범위 밖·주의**: `contracts.py`·`state.py`는 C와 **바이트 동일 복사본** — C가 다시 고치면 D 복사본이 stale(머지 순서 자체는 동일 내용이라 무관). `retrieval/tools.py`(B)는 미수정 — import는 되나 호출 시 C `EvidenceItem`(job_id 필수)에 걸림(D 흐름에선 미호출, B가 통합 때 교체). `DATABASE_URL`이 RDS면 운영 DB에 테스트 ideas/analysis_jobs 행이 쌓임(검증은 로컬 docker로).
- **기각**: ko-agent 전체 머지(C agent 트리까지 통합 — 과함), D가 계약을 독자 재작성(base·C와 다른 3번째 변종 위험), job_id를 graph 내부 생성(C 노드는 받아 쓰는 소비자 — 발급은 진입점 D).
---

## 2. 레포 상태

**구조** (push 완료, `de-ai-AIAgentPJ-team4/venturescout`)
```
shared/contracts.py   계약(C 소유, ko-agent판): AgentRun·EvidenceItem(+job_id)·IPOverlapCandidate·IdeaRecord·AnalysisJob·CriticResult ★ADR-032
shared/state.py       VentureScoutState (agent_runs/evidence_items reducer + job_id/idea_id) ★ADR-032 (ADR-023 reducer 대체)
db/init.sql           9테이블 DDL + pgvector hnsw + tsvector gin — ★RDS 실 스키마와 바이트 일치(ADR-031)
db/schema.dbml        dbdiagram.io용 — ★RDS 실 스키마 반영(ADR-031)
db/introspect_schema.py  읽기전용 스키마 조회(RDS↔로컬 diff 검증) ★ADR-031
db/certs/rds-global-bundle.pem  AWS RDS 공개 CA 번들(verify-full용, 비밀 아님) ★ADR-031
data/                 Track A — README + stub
retrieval/tools.py    Track B — retrieve()/vector_search() mock 반환 ★mock 병렬화 핵심
agents/graph.py       Track C — LangGraph 7노드 골격, 현재 mock·동기 / 노드는 agent_runs/AgentRun 반환 ★ADR-032 (⑥ bm_node payload→output_json)
app/api.py            Track D — /analyze SSE + job_id 발급(ideas+analysis_jobs)·state+config 주입 ★ADR-023/032
app/ui.py             Track D — Chainlit Evidence Board (agent_runs 렌더) ★ADR-024/032
eval/harness.py       Track D — 평가 하네스(Critic ON/OFF, agent_runs 기준) ★ADR-029/032
tests/test_contracts.py  계약 검증 (green)
docs/plan_v3.md, schema_tier0.md  기획·스키마
docker-compose.yml(로컬 db 서비스 제거·DB는 .env 경유 RDS ★ADR-031 / ui는 API_URL만·DATABASE_URL="" ★ADR-026), Dockerfile, .gitignore(db/certs/*.pem 예외 ★ADR-031), .env.example(RDS+verify-full ★ADR-031), requirements.txt(httpx 포함)
```

**구현 상태**
- ✅ 계약 코드·DDL·docker-compose·트랙 stub
- ✅ `pytest` green / `agents/graph.py` mock E2E 동작 (state reducer 적용 후)
- ✅ `app/api.py` SSE 실 스트리밍 — `astream_events`로 7노드 중계, §3 봉투 준수
- ✅ `shared/state.py` `findings`/`evidence_pool` reducer (ADR-023, ★C 리뷰 대기)
- ✅ `app/ui.py` Chainlit SSE 구독 → 단계 렌더 → Evidence Board (ADR-024)
- ✅ **Docker 실행 검증 — 브라우저 `localhost:8001` Evidence Board 실렌더** (ADR-025/026, D3 게이트 통과)
- ✅ **⑥ BM 노드 본문 — `bm_node`+`_bm_payload`, payload 5필드, invoke 검증** (ADR-028, 커밋 대기)
- ✅ **평가 하네스 골격 — `eval/harness.py`, Critic ON/OFF diff 검증** (ADR-029, 커밋 대기)
- ✅ `D` 브랜치 푸시 완료 (PR 머지 대기)
- ⬜ A·B·C 실데이터·실LLM / 하네스 실측(retrieval·cost — B·C 실연결 후)

**실행** (표준 = Docker, 로컬 3.14 미지원 — ADR-025)
```bash
# 표준: Docker (컨테이너 Python 3.11)
copy .env.example .env                  # (PowerShell: Copy-Item) compose env_file 요구. RDS 비밀번호 채우기(ADR-031)
docker compose up                       # api:8000 + ui:8001 (DB는 RDS — 로컬 db 컨테이너 없음, ADR-031)
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

**SSE 이벤트 봉투 포맷** (D 소유, UI·평가가 의존) ★ADR-032: findings→agent_runs, job_id 추가
```
{"type":"job",   "status":"running|done|failed", "stage":null, "job_id":"<uuid>"}
{"type":"stage", "stage":"<노드명>", "label":"<표시>", "status":"running|done"}
{"type":"report","decision":"go|pivot|kill|more_research", "summary":"...", "agent_runs":[...]}
```
※ `agent_runs[*]`는 `AgentRun` dict — `signal`·`next_experiment`는 strict 필드가 아니라 `output_json` 안에 있음(ADR-016 loose).

**C ↔ D 계약** (api.py가 C에 요구)
1. 각 노드는 `async` 함수 **권장**(sync도 astream_events가 중계하나, 실 LLM I/O 병렬성 위해 async 권장 — §5 open)
2. 노드 진입 시 노드 이름이 stage로 잡히게(현 구조 OK)
3. critic 노드 최종 출력에 `decision`·`summary` 포함(`CriticResult`)

**기계 ↔ 에이전트 계약** (B → C, ADR-010)
- B: `vector_search() → list[OverlapCandidate]`, `retrieve() → list[EvidenceItem]` (반환 타입 고정, 내부만 교체)
- C(⑤): `OverlapCandidate` 읽어 판정만

**State 계약** (ADR-032가 ADR-023 대체) — 이제 C판 State: 분석 산출은 `agent_runs`(Annotated `operator.add`)·`evidence_items`(머지), 진입 문맥은 `job_id`/`idea_id`/`raw_input`. D 노드/표면 모두 이 키로 정합. `findings`/`evidence_pool`(ADR-023)은 더 이상 사용 안 함.

**job_id 전송 계약** (ADR-032) — D가 `/analyze`에서 발급한 job_id를 **초기 state(`state["job_id"]`, C가 읽음) + RunnableConfig(`configurable.job_id`, B persistence가 읽음) 둘 다**로 주입. B/C 두 소비자 충족.

**BM 노드 승격 포인트** (ADR-027/028) — `bm_node` 내 `# TODO(B)`(검색)·`# TODO(C)`(LLM) 주석 2곳. 실연결 시 (a)→(b) 승격. payload 5필드 스펙은 `_bm_payload` docstring.

**평가 하네스 승격 포인트** (ADR-029) — `eval/harness.py`의 `evaluate()` 내 `None` 3곳: `precision_at_k`·`contradiction_coverage`(B 실검색+정답라벨)·`cost_usd`(Bedrock 토큰). `compare_critic`의 `objections_added`는 C ⑦ 실LLM 후 실값.

---

## 4. 트랙별 다음 작업 (Day 1~)

**A (데이터)** — BigQuery dry run(추천 CPC 건수·연도) → 수집 → 독립항/limitation 분해 → `documents`·`patent_claims`·`claim_limitations` 적재. (소스=BigQuery, 분류=CPC 확정)

**B (검색·임베딩)** — PatentSBERTa 768d 임베딩 파이프라인 → `claim_limitations.embedding`·`documents.embedding` → pgvector+tsvector 하이브리드+rerank → `tools.py` mock을 실검색으로 교체(시그니처 고정). ⑥ BM 승격을 위해 `retrieve("H_bm","pricing")` 경로도 고려.

**C (에이전트)** — `agents/graph.py` 노드를 **async + Bedrock 실연결**. few-shot·가드레일·출력 pydantic 중앙 배포. ⑤ IP 풀 판정(청구항 요소별 중첩), ⑦ Critic 적대검증. **State reducer 전제 확인(ADR-023 리뷰)** + BM 노드 LLM 자리(ADR-027) 배포.

**D (백엔드·UI·평가)** — ✅ Chainlit SSE·Evidence Board(D3 게이트). ✅ Docker 확정(ADR-025/026). ✅ ⑥ BM 노드 본문(ADR-028). ✅ 평가 하네스 골격(ADR-029). ▶ **다음: `D` 브랜치 커밋(bm_node·harness) → PR 머지**(state.py 포함 → C 리뷰어 지정). 하네스 실측은 B·C 실연결 후. (`/health` 완비, job 상태는 SSE `job` 봉투. 영속 job 조회 필요해지면 `/jobs/{id}` 검토.)

**공통 Day 1** — Bedrock 모델 액세스+IAM 신청 / 팀원 org 초대(Member·Write) / 브랜치 보호 / `.env`는 절대 커밋 금지.

---

## 5. 미해결·확인 필요 (open)

- [ ] BigQuery 추천 CPC 실제 건수 dry run (ADR-003) — 너무 많으면 연도 축소, 적으면 CPC 확장
- [ ] e5 폴백 시 `vector(768)→vector(1024)` 차원 변경 필요 (ADR-004)
- [ ] 권장 3컬럼(decision·decision_summary·target_run_id) 최종 채택 여부 — 현재 DDL에 포함, 뺄 거면 해당 줄 삭제 (ADR-017)
- [x] D3 게이트(Chainlit 스트리밍) 통과 → ADR-006 accepted 확정 (브라우저 E2E 검증, ADR-024/025/026). 임베딩 동기화 게이트는 B 진행 후 확인.
- [x] **★C 리뷰(reducer, ADR-023)** — ADR-032로 해소: D가 C판 `shared/contracts.py`·`state.py`를 그대로 채택(agent_runs/AgentRun). 별도 reducer 협의 불필요해짐. **잔여**: contracts/state는 C와 바이트 동일 복사본 — C가 이후 또 고치면 D 복사본 stale(동기화 필요). PR 디프상 C 척추 파일이 D 변경으로 보이는 점 리뷰어에 공유.
- [ ] **★D 브랜치 커밋 + PR 머지** — `bm_node`·`_bm_payload`(graph.py, ADR-028)·`eval/harness.py`+`eval/__init__.py`(ADR-029)를 `D`에 커밋 → `D` → main(또는 dev), ADR-021 1리뷰. state.py 포함이므로 **C를 리뷰어로 지정**.
- [ ] **하네스 실측** (ADR-029) — C ⑦ 실LLM → `objections_added`·`decision` 실값 / B 실검색+정답라벨 → `precision_at_k`·`contradiction_coverage` / C Bedrock → `cost_usd`. Tier 2 진입(ADR-018).
- [x] **RDS(공용 DB)** — AWS RDS PostgreSQL 16으로 전환·스키마 문서 동기화 완료(ADR-031). ADR-005 정합 정리 끝(호스팅만 로컬→RDS, 단일 스토어 원칙 유지). `.env`는 .gitignore로 보호 확인. **잔여 open**:
  - [~] **앱 E2E** — ADR-032로 진전: `/analyze`가 job_id 발급→`ideas`·`analysis_jobs` 적재까지 로컬 docker postgres에서 검증됨(쓰기 경로 확인). 단 `agent_runs`/`evidence_items` 실적재는 B persistence 머지 후(D는 job_id만 발급·전달). RDS 대상 E2E는 보안그룹·운영데이터 주의로 미실행.
  - [ ] **보안그룹** — 현재 테스트 PC 공인 IP만 허용 추정. 팀원 PC·배포 환경은 인바운드 규칙 추가 필요(AWS 콘솔 권한 요함).
  - [ ] **RDS-side 결함**(ADR-031 문서화만, 미수정) — `patent_claims`·`claim_limitations` 중복 UNIQUE 각 2건, FTS `english`(한국어 부적합 가능). 운영 공유 DB라 팀 논의 후 별도 처리.
- [ ] DEMO_DELAY(기본 0.4s) — 실 LLM 붙으면 0으로 (env로 분리 완료, 배포 시 0 설정)
- [ ] 그래프 노드 현재 sync — astream_events는 sync도 중계되나, C가 실 LLM 붙일 때 async 전환 권장(I/O 병렬성). §3 C↔D 계약 #1 참조.
- [ ] **하네스 분산 대응** (ADR-030) — C ⑦ 실 LLM 승격과 동시에 `compare_critic`을 N회 반복·집계(change_rate·objections stdev·판정 분포)로 교체. 1차 방어 temperature↓+few-shot. 표본 idea 2~3개×5회, cost_usd N배 반영. ADR-029 승격 조건과 같은 타이밍.