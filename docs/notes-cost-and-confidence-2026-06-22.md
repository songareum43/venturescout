# 작업 메모 — 비용 실측 + confidence 임계값 재보정 (2026-06-22)

> ADR 작성용 근거 메모. 아래 두 변경의 **맥락·결정·근거·검증·범위 밖**을 정리한다.
> 관련 기존 ADR: ADR-016(strict/loose), ADR-019(process 평가), ADR-029/035(평가 하네스·cost_usd),
> ADR-036/037(live·strict LLM). 변경 파일: `agents/llm.py`, `agents/graph.py`,
> `eval/harness.py`, `requirements.txt`, 루트 `langsmith.py`→`langsmith_check.py`.

---

## 변경 1 — Bedrock 토큰 실측 비용 + LangSmith 트레이싱

### 맥락
- ADR-029/035에서 `eval/harness.py`의 `system_metrics.cost_usd`는 **None + TODO**였다.
  사유: `agents/llm.py`가 Bedrock `converse` 응답의 `usage`(input/output 토큰)를 버리고 있어서.
- 비용 질문에 매번 **수기 추정**으로만 답할 수 있었음(풀 E2E 6회에 약 $1 등).

### 결정
1. `agents/llm.py`에 토큰 누적기 + 단가 환산 추가.
   - `converse` 응답 `usage.inputTokens/outputTokens`를 **파싱 이전에** 캡처(파싱 실패해도 호출은 과금됨).
   - 스레드 안전 누적(`threading.Lock`) — 분석 5노드가 병렬 스레드라 필수.
   - `reset_usage()` / `usage_snapshot()` 공개. 단가는 **Sonnet 4.6 $3/$15 per 1M**(Bedrock=1P 동일,
     claude-api 레퍼런스 2026-06). `.env`의 `BEDROCK_PRICE_INPUT_PER_MTOK`/`..._OUTPUT_...`로 override.
2. `eval/harness.py`: `evaluate()`가 첫 회 **ON 1회분**(structuring+분석5+critic = 7콜)만 reset→snapshot으로
   재서 `cost_usd`·`tokens` 채움 = "분석 1건 비용". OFF는 제외.
3. **LangSmith 트레이싱(선택)**: `invoke_claude_json`을 `@traceable(run_type="llm")`로 감싸고
   run에 `usage_metadata`(토큰)+`ls_model_name/ls_provider`(모델별 단가 attribution) 부착.
   - `langsmith` 미설치 또는 `LANGCHAIN_TRACING_V2` 미설정이면 **완전 no-op**(가드).
   - 활성 시: 각 Bedrock 호출이 LLM 스팬으로, LangGraph 노드 트리와 함께 smith.langchain.com에 표시.
   - `requirements.txt`에 `langsmith` 명시(langchain 전이 의존이지만 명시).

### 근거 / 트레이드오프
- **수동 누적기 vs LangSmith**: 둘 다 둠. 누적기는 오프라인·결정적(하네스 cost_usd, CI에서도 동작).
  LangSmith는 대시보드·노드별·트레이스별 추적이 강점이나 외부 SaaS·키 필요. 상호보완.
- 누적기는 **전역 + lock**이라 잡이 순차일 때만 잡 단위로 정확. 프로덕션에서 `/analyze` **동시 다발**이면
  잡 간 합산이 섞임 → 그때는 `contextvars` 또는 job_id 스코프로 분리 필요(아래 범위 밖).

### ⚠️ 부수 발견 (버그픽스) — `langsmith.py` 이름 충돌
- 루트의 진단 스크립트 `langsmith.py`가 **langsmith pip 패키지를 shadowing**.
  프로젝트 루트가 sys.path에 있으면 `import langsmith`가 패키지 대신 이 스크립트를 로드 →
  import마다 스크립트 top-level print 실행 + 진짜 패키지(트레이싱) 사용 불가.
  langgraph 등 langsmith를 쓰는 모든 코드에 영향 가능.
- **조치**: `langsmith.py` → `langsmith_check.py`로 rename(untracked 진단 스크립트라 안전).
  교훈: 루트에 설치된 패키지와 동명인 모듈을 두지 말 것.

### 검증
- import 시 print 오염 사라짐, `_LANGSMITH=True`(패키지 정상 로드).
- `usage_snapshot()` 산식 확인: in 1000 + out 500 → `$0.0105` (= 1000/1e6·3 + 500/1e6·15). ✓
- **실측 분석 1건 비용**(실 RDS+Bedrock, 2026-06-22):
  | 판정 | 콜 수 | input 토큰 | output 토큰 | 비용 |
  |---|---|---|---|---|
  | KILL | 8 (structuring+5+critic+**alternatives**) | 52,177 | 18,471 | **$0.434** |
  | PIVOT | 7 (structuring+5+critic) | 45,662 | 18,054 | **$0.408** |
  | GO/그 외 | 7 | 45,003 | 17,674 | **$0.400** |
  → 분석 1건 **≈ $0.40**, KILL은 ⑧ alternatives 노드 때문에 +$0.03. (이전 수기 추정 $0.20은
  input 토큰을 과소평가 — 실제 in ~45–52K/건. 수기 추정 폐기, 이제 실측.)

---

## 변경 2 — confidence 임계값 재보정 (`_confidence_from_strength`)

`high 0.75 → 0.60`, `mid 0.45 → 0.40` (`agents/graph.py`).

### 맥락 — "입력 무관 KILL" 구조적 결함
- `strength = mean(relevance × reliability)`. confidence는 `high≥0.75 / mid≥0.45 / low<0.45`.
- 시드 출처(seed_review/competitor/pricing) `reliability_score = 0.6`(2차 출처, ADR보다 `load_seed.py`에서
  고정). 따라서 시드 strength ≤ `0.6 × hybrid`. `hybrid = 0.6·cosine + 0.4·ts_rank`라 잘 나와도 ~0.78 →
  **시드 strength는 사실상 0.47 상한**.
- 결과: 0.75(high)는 시드로 **도달 불가**, 0.45(mid)도 거의 항상 미달 →
  `market`/`competitor`/`bm`이 **구조적으로 늘 low** → `_decide` 규칙3(low≥3)으로 **입력과 무관하게
  KILL이 기본값**. (실 RDS+Bedrock E2E 3건이 전부 KILL로 실증됨.)
- 동시에 `_naive_decision`(Critic OFF)은 "근거 있으면 go" → OFF는 항상 GO.
  즉 **GO/PIVOT은 사실상 도달 불가능**한 상태였음.

### 실측 strength 분포 (실 structuring LLM이 생성한 H1~H5 쿼리 기준)
| 아이디어(도메인) | market | competitor | tech | bm |
|---|---|---|---|---|
| 축산 IoT (off-domain) | 0.155 | 0.173 | 0.510 | 0.325 |
| 임베디드 결제 (중간)   | 0.258 | 0.386 | 0.485 | 0.402 |
| SaaS 업무관리 (on-domain) | 0.200 | 0.424 | 0.448 | 0.454 |
- ip는 후보·근거 있으면 항상 mid(별도 산정).
- on-domain 시드 근거는 0.40~0.45 구간, off-domain은 0.12~0.33 구간으로 **자연 분리**.

### 결정 / 근거
- `mid = 0.40`: on-domain(0.42~0.45)을 mid로, off-domain(0.16~0.33)을 low로 가르는 경계.
  → low 개수: 축산=3(KILL), 결제=2(PIVOT), SaaS=1(GO)로 **네 판정 모두 도달 가능**.
- `high = 0.60`: reliability 0.9인 **특허 근거(tech/ip)**가 강한 매칭일 때 high에 닿게(시드론 불가, 의도된 구분).
- 0.75/0.45는 strength가 0~1 전구간에 퍼진다는 (틀린) 가정에 기반했음 → 실분포에 맞춰 보정.

### 검증 (실 RDS+Bedrock, 2026-06-22)
- `tests/test_critic_decision.py`(=`_decide` 직접 검증)는 임계값 비의존 → 16 tests green 유지.
- 보정 **전**: 동일 3건 전부 KILL(입력 무관 KILL 결함 실증).
- 보정 **후** 실 LLM ON 3건:
  | 아이디어(도메인) | 판정 | low 에이전트 |
  |---|---|---|
  | 축산 IoT (off-domain) | **KILL** | bm·competitor·market (3) |
  | 임베디드 결제 (중간)   | **PIVOT** | competitor·market (2) |
  | SaaS 업무관리 (on-domain) | **PIVOT** | competitor·market (2) |
  → KILL/PIVOT은 목표대로. **GO는 reachable해졌으나 fragile**: 이 런에서 competitor가 0.40 경계 바로
  아래로 떨어져(structuring LLM 문구 변동) low 2개 → PIVOT. (calib 측정 땐 competitor 0.424=mid라 low 1개=GO였음.)
- **핵심**: 보정 전엔 GO/PIVOT이 **도달 불가**였으나, 보정 후 PIVOT은 안정·GO는 도달 가능권에 진입.
  GO가 **안정적으로** 나오려면 근본 원인(아래) 처리 필요 — 임계값을 더 내려 억지로 맞추는 건 과적합이라 안 함.

### 범위 밖 / 후속(ADR에 남길 것)
- **근본 원인은 reliability 가중(시드 0.6) + hybrid 상한**. 임계값 하향은 그 분포에 맞춘 보정이지
  원인 제거가 아님. 대안:
  (a) 시드 `reliability_score` 상향(0.6→0.8+) — 단 `documents` 행에 저장돼 있어 재적재/UPDATE 필요(공유 RDS),
  (b) source_type별 strength 정규화(시드/특허 분포를 각자 0~1로),
  (c) market이 항상 최저인 문제(seed_review가 고객문제 쿼리와 의미적으로 약매칭) 별도 검토.
- 3개 표본 기반 보정이라 **표본 한계** 명시 필요. 더 많은 아이디어로 분포 재확인 권장.
- 임계값을 `graph.py` 상수 하드코딩 → 추후 `config.py`로 빼면 튜닝·실험이 쉬움.
