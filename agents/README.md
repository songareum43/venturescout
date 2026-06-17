# Track C - 에이전트 플랫폼

Track C는 VentureScout의 에이전트 실행 파트다. 사용자의 창업 아이디어를 검증 가능한 가설로 구조화하고, 기술/IP 관점에서 근거 기반으로 검토한 뒤, Critic이 최종 판단과 다음 검증 실험을 제안한다.

현재 구현은 실제 LLM/DB 연결 전 단계의 **mock LangGraph E2E**다. 목적은 모델 품질을 증명하기보다, 데이터 계약과 에이전트 흐름이 올바르게 이어지는지 검증하는 것이다.

## 한 줄 요약

```text
아이디어 입력
  -> ① Structuring
  -> ② Market / ③ Competitor / ④ Tech / ⑤ IP / ⑥ BM 병렬 분석
  -> ⑦ Critic
  -> decision / final_report
```

Track C 담당 범위는 다음 네 노드다.

```text
① Structuring
④ Tech(light)
⑤ IP(full·시그니처)
⑦ Critic
```

## 현재 파일 구조

```text
agents/
  graph.py              현재 실제 실행되는 mock LangGraph
  mock_data.py          mock 입력, 가설, 문서, 근거, IP 후보의 단일 원천
  mock_repository.py    상세 노드 호환용 mock repository
  schemas.py            LLM/prompt 출력용 Pydantic 스키마
  state.py              shared.state/shared.contracts 호환 export
  nodes/                예전 상세 노드들. 아직 graph.py에 직접 연결하지 않음

retrieval/
  tools.py              mock_data를 EvidenceItem/IPOverlapCandidate로 변환

shared/
  contracts.py          9개 Tier 0 테이블 기준 Pydantic 계약
  state.py              LangGraph 런타임 State
```

## 현재 데이터 흐름

```text
agents/mock_data.py
  -> retrieval/tools.py
  -> agents/graph.py
  -> scripts/run_mock_graph.py
```

각 파일의 역할은 다음과 같다.

- `agents/mock_data.py`: 현재 mock DB 역할. 입력, 구조화 결과, 가설, 문서, evidence, IP 후보가 들어 있다.
- `retrieval/tools.py`: `retrieve()`, `vector_search()`를 제공한다. 지금은 mock 데이터를 읽지만, 나중에는 Track B의 실제 검색으로 교체된다.
- `agents/graph.py`: LangGraph 노드와 edge를 정의한다.
- `scripts/run_mock_graph.py`: 전체 mock workflow를 실행한다.

## 핵심 계약

Track C는 아래 계약을 중심으로 움직인다.

```text
hypotheses
  무엇을 검증할 것인가

evidence_items
  각 주장에 붙는 근거 원자

agent_runs
  각 에이전트 실행 결과

ip_overlap_candidates
  IP 시그니처 검색이 만든 특허 중첩 후보
```

가장 중요한 연결은 이것이다.

```text
agent_runs.grounded_on
  -> evidence_items.evidence_id
```

즉 에이전트가 어떤 주장을 하려면 반드시 evidence_id를 인용해야 한다. 근거 없는 주장은 Critic이 문제로 잡아야 한다.

## 현재 mock 데이터

`agents/mock_data.py`에는 다음 데이터가 있다.

```text
MOCK_RAW_INPUT
MOCK_STRUCTURED_IDEA
MOCK_HYPOTHESES
MOCK_DOCUMENTS
MOCK_EVIDENCE
MOCK_IP_CANDIDATES
```

현재 mock 데이터 규모:

```text
hypotheses             5개
documents              7개
evidence_items          7개
ip_overlap_candidates   2개
```

아이디어는 “AI 회의록 자동화 SaaS”이며, STT, 회의 요약, 액션 아이템 추출, Slack/Notion 연동을 가정한다.

## LangGraph 흐름

현재 `agents/graph.py`의 흐름은 다음과 같다.

```text
START
  -> structuring
  -> market
  -> competitor
  -> tech
  -> ip
  -> bm
  -> critic
  -> END
```

`market`, `competitor`, `tech`, `ip`, `bm`은 structuring 이후 병렬로 실행되고, 모든 결과가 critic으로 모인다.

## 에이전트별 처리 로직 한눈에 보기

각 에이전트는 공통적으로 아래 패턴을 따른다.

```text
입력 State 읽기
  -> 필요한 evidence 조회
  -> 근거를 해석해 output_json 작성
  -> AgentRun 생성
  -> Critic이 읽을 수 있게 State에 누적
```

공통 출력 단위는 `AgentRun`이다.

```text
AgentRun
  agent_name       어떤 에이전트가 실행됐는가
  hypothesis_id    어떤 가설을 검토했는가
  confidence       판단 신뢰도
  depth            full 또는 light
  grounded_on      인용한 evidence_id 목록
  output_json      에이전트별 분석 본문
```

아래는 현재 `agents/graph.py` 기준의 실제 처리 순서다.

| 에이전트 | 입력 | 처리 | 출력 |
|---|---|---|---|
| ① Structuring | `raw_input`, `MOCK_STRUCTURED_IDEA`, `MOCK_HYPOTHESES`, `MOCK_DOCUMENTS` | 아이디어 구조화, 가설 생성, 필수 필드 검증 | `idea`, `analysis_job`, `hypotheses`, `documents` |
| ② Market | H1 evidence | 고객 문제 근거 확인 | market `AgentRun` |
| ③ Competitor | H2 evidence | 경쟁/차별화 근거 확인 | competitor `AgentRun` |
| ④ Tech | H4 evidence, 기술요소 | 구현 가능성, 비용, 지연, 보안 리스크 평가 | tech `AgentRun` |
| ⑤ IP | H5 evidence, IP 후보 | claim limitation 중첩 신호와 회피 전략 평가 | ip `AgentRun`, `ip_overlap_candidates` |
| ⑥ BM | H3 evidence | 가격/구독 모델 검증 필요성 확인 | bm `AgentRun` |
| ⑦ Critic | 모든 `AgentRun`, `evidence_items`, `ip_overlap_candidates` | 근거 연결, 가설 커버리지, confidence, 반박 근거, IP 리스크 종합 | critic `AgentRun`, `decision`, `final_report` |

## 처리 로직 상세

### ① Structuring 로직

Structuring은 실제 분석을 시작하기 전에 입력을 정리하는 단계다.

처리 순서:

```text
1. state에서 job_id, idea_id, raw_input을 읽는다.
2. agents/mock_data.py의 MOCK_STRUCTURED_IDEA를 가져온다.
3. idea_id와 raw_input은 현재 실행 요청 값으로 덮어쓴다.
4. IdeaRecord를 만든다.
5. MOCK_HYPOTHESES로 H1~H5 Hypothesis를 만든다.
6. MOCK_DOCUMENTS로 DocumentRecord 목록을 만든다.
7. 필수 필드와 가설 축이 모두 있는지 검사한다.
8. 문제가 없으면 analysis_job.status = running으로 둔다.
```

검증 기준:

```text
필수 idea 필드가 비어 있지 않은가
H1~H5에 해당하는 5개 축이 모두 있는가
technical_elements가 있는가
patent_keywords가 있는가
```

Structuring이 실패하면 뒤 에이전트들이 잘못된 전제를 바탕으로 분석하므로, 현재는 누락이 있으면 `ValueError`를 발생시킨다.

### ④ Tech(light) 로직

Tech는 “만들 수 있나?”를 보는 노드지만, 현재는 단순 가능/불가능이 아니라 검증 가능한 리스크 단위로 나눈다.

처리 순서:

```text
1. analysis_job.job_id를 읽는다.
2. retrieve("H4", ...)로 H4 관련 evidence를 가져온다.
3. evidence의 stance를 센다.
   - supports
   - contradicts
   - neutral
4. evidence_strength를 계산한다.
   relevance_score * reliability_score의 평균
5. strength 기준으로 confidence를 정한다.
6. 반박 evidence가 있으면 feasibility_signal을 보수적으로 mid로 둔다.
7. 기술 가정, 리스크, 검증 계획, go/no-go 기준을 output_json에 담는다.
8. tech AgentRun을 만든다.
```

현재 evidence_strength 계산:

```text
각 evidence 점수 = relevance_score * reliability_score
전체 strength = evidence 점수 평균
```

confidence 기준:

```text
strength >= 0.75  -> high
strength >= 0.45  -> mid
그 외             -> low
```

현재 Tech가 보는 주요 리스크:

```text
긴 회의 처리 지연
토큰/전사 비용 증가
B2B 회의 데이터 보안
```

Tech 출력의 목적은 “좋아 보인다”가 아니라, 다음 실험을 바로 설계할 수 있게 만드는 것이다.

### ⑤ IP(full·시그니처) 로직

IP는 Track C에서 가장 깊게 보는 시그니처 노드다. 단, 법적 판단은 하지 않는다.

처리 순서:

```text
1. idea.technical_elements를 읽는다.
2. retrieve("H5", ...)로 IP 관련 evidence를 가져온다.
3. vector_search(...)로 ip_overlap_candidates를 가져온다.
4. 각 후보의 hybrid_score를 기준으로 risk_band를 붙인다.
5. high_watch 후보를 high_overlap_elements로 모은다.
6. overlap_signal을 정한다.
7. design-around 옵션과 수동 검토 질문을 만든다.
8. ip AgentRun을 만든다.
```

IP 후보 해석 기준:

```text
hybrid_score >= 0.78  -> high_watch
hybrid_score >= 0.70  -> watch
그 외                 -> low_watch
```

현재 IP가 출력하는 주요 항목:

```text
claim_review_queue
high_overlap_elements
design_around_options
manual_review_questions
legal_guardrail_note
```

IP 노드의 핵심 원칙:

```text
"침해다" 또는 "안전하다"라고 말하지 않는다.
"중첩 신호가 있으니 수동 claim chart 검토가 필요하다"라고 말한다.
```

### ⑦ Critic 로직

Critic은 최종 판단 노드다. 각 에이전트의 결론을 그대로 믿지 않고, 근거 연결과 과장을 검사한다.

처리 순서:

```text
1. 모든 agent_runs를 읽는다.
2. 모든 evidence_items를 읽는다.
3. 각 run의 grounded_on이 비어 있는지 확인한다.
4. grounded_on이 실제 evidence_id에 존재하는지 확인한다.
5. H1~H5 중 agent_run이 없는 가설이 있는지 확인한다.
6. low confidence agent를 모은다.
7. contradicting evidence를 모은다.
8. high IP candidate를 모은다.
9. scorecard를 만든다.
10. decision_rule에 따라 decision을 정한다.
11. critic AgentRun과 final_report를 만든다.
```

현재 decision rule:

```text
근거 연결이 없거나 잘못됨
  -> more_research

커버되지 않은 가설이 있음
  -> more_research

low confidence가 3개 이상
  -> more_research

high IP candidate가 있음
  -> pivot

반박 근거가 적고 low confidence가 1개 이하
  -> go

그 외
  -> pivot
```

Critic이 남기는 scorecard 예시:

```text
agent_run_count
evidence_count
grounded_claim_count
low_confidence_agents
uncovered_hypotheses
contradicting_evidence
high_ip_candidates
invalid_grounding
```

이 scorecard는 나중에 Critic ON/OFF 평가나 Evidence Board 디버깅에 쓸 수 있다.

## ① Structuring

역할:

```text
사용자 아이디어를 분석 가능한 데이터로 구조화한다.
```

현재 생성하는 State:

```text
idea
analysis_job
hypotheses
documents
```

검증하는 것:

```text
title
target_customer
problem_statement
solution_summary
business_model_hint
H1~H5 가설 축
technical_elements
patent_keywords
```

구조화 결과에 필수 필드가 빠지거나 가설 축이 빠지면 실패한다. 이 단계가 틀리면 후속 에이전트가 모두 잘못된 입력을 받기 때문에, Track C의 입구 gate 역할을 한다.

## ④ Tech(light)

역할:

```text
기술 구현 가능성을 경량으로 판단한다.
```

검증 가설:

```text
H4: 핵심 기능은 현재 STT와 LLM API로 프로토타입 구현이 가능한가?
```

입력:

```text
H4 evidence_items
technical_elements
```

현재 output_json에 담는 것:

```text
summary
feasibility_signal
evidence_strength
stance_counts
supporting_evidence
risk_evidence
architecture_assumption
required_models_or_apis
risk_register
validation_plan
go_no_go_metrics
recommendations
```

현재 판단:

```text
STT와 LLM 조합으로 프로토타입 경로는 있다.
다만 긴 회의 처리 지연, 토큰/전사 비용, B2B 보안 요구사항은 검증 전이다.
```

Tech(light)는 깊은 기술 설계 문서가 아니라, 다음 검증으로 갈 수 있는 “근거 있는 기술 신호”를 만드는 역할이다.

## ⑤ IP(full·시그니처)

역할:

```text
아이디어 기술요소와 특허 claim limitation의 중첩 신호를 분석한다.
```

검증 가설:

```text
H5: 기존 특허 claim과 직접 중첩되지 않는 구현 경로가 있는가?
```

입력:

```text
H5 evidence_items
ip_overlap_candidates
technical_elements
```

IP 후보 해석 구간:

```text
hybrid_score >= 0.78  -> high_watch
hybrid_score >= 0.70  -> watch
그 외                  -> low_watch
```

현재 output_json에 담는 것:

```text
summary
overlap_signal
evidence_strength
stance_counts
high_overlap_elements
design_around_options
claim_review_queue
legal_guardrail_note
manual_review_questions
candidates
```

중요한 guardrail:

```text
IP Agent는 법적 침해 판단을 하지 않는다.
특허 claim limitation 유사도 기반의 사전 리스크 신호만 제공한다.
```

현재 IP 판단은 “회의 요약/액션 아이템 추출 관련 claim limitation 후보가 있으므로, 범용 회의 요약보다 vertical workflow 후속 조치 중심으로 좁혀 검토하자”에 가깝다.

## ⑦ Critic

역할:

```text
모든 agent_runs를 모아 최종 의사결정과 다음 실험을 제안한다.
```

확인하는 것:

```text
각 agent_run이 grounded_on을 갖고 있는가
grounded_on이 실제 evidence_items에 존재하는가
H1~H5가 모두 agent_run으로 커버됐는가
low confidence가 너무 많은가
contradicting evidence가 있는가
high IP candidate가 있는가
```

Critic이 output_json에 남기는 것:

```text
decision
confidence
summary
grounded_on
objections
missing_evidence
next_experiments
scorecard
decision_rule
```

현재 decision rule:

```text
missing/invalid grounding 또는 uncovered hypothesis가 있으면 more_research
low confidence가 3개 이상이면 more_research
high IP candidate가 있으면 pivot
반박 근거가 적고 low confidence가 1개 이하이면 go
그 외에는 pivot
```

현재 mock 데이터에서는 market, competitor, bm이 low confidence라서 최종 판단은 보통 `more_research`다.

## 현재 실행 결과

```text
Decision: more_research
Final Report:
대부분의 핵심 가설이 low confidence라 고객/가격/기술 근거를 더 수집해야 한다.
```

현재 graph 실행 결과의 주요 개수:

```text
documents              7
evidence_items          7
ip_overlap_candidates   2
agent_runs              6
```

`agent_runs`가 6개인 이유:

```text
market
competitor
tech
ip
bm
critic
```

## 실행 방법

전체 mock workflow 실행:

```bash
python scripts/run_mock_graph.py
```

테스트:

```bash
python -m pytest
```

현재 기대 결과:

```text
6 passed
```

상세 상태 확인:

```bash
python -c "from agents.graph import build_graph; r=build_graph().invoke({'job_id':'job_test','idea_id':'idea_test'}); print(r['idea'].title); print('documents', len(r['documents'])); print('evidence_items', len(r['evidence_items'])); print('ip_overlap_candidates', len(r['ip_overlap_candidates'])); print('agent_runs', len(r['agent_runs'])); print('decision', r['decision'])"
```

## 현재 모델 연결 상태

기본 실행은 여전히 mock 모드다. AWS 인증 없이도 테스트와 데모를 돌릴 수 있게 하기 위해서다.

```text
model_name = "mock"
```

Bedrock Claude를 실제 호출하려면 환경변수로 provider를 바꾼다.

```bash
$env:AGENT_LLM_PROVIDER="bedrock"
$env:AWS_REGION="us-east-1"
$env:BEDROCK_MODEL_ID="anthropic.claude-3-5-sonnet-20240620-v1:0"
python scripts/run_bedrock_graph.py
```

Bedrock 모드에서는 `agents/llm.py`가 AWS Bedrock Runtime Converse API를 호출한다. 각 `AgentRun.model_name`에는 `bedrock:{BEDROCK_MODEL_ID}`가 기록된다.

현재 Bedrock 연결 방식:

```text
Structuring -> Claude가 raw_input을 ideas + H1~H5로 구조화
Market/Competitor/BM -> Claude가 mock evidence 기반 output_json 보강
Tech -> Claude가 H4 evidence 기반 기술 리스크/검증 계획 보강
IP -> Claude가 H5 evidence + ip_overlap_candidates 기반 IP 리스크 해석 보강
Critic -> 코드의 결정 규칙을 유지하면서 Claude가 summary/objection/next_experiments 보강
```

그래도 evidence 검색은 아직 mock이다. 즉, 현재 상태는 **Bedrock Claude가 분석 문장을 보강하지만, DB/pgvector/특허 검색은 아직 실제 서비스가 아니라 mock 데이터 원천을 사용한다.**

## 왜 agents/nodes/*를 바로 연결하지 않았나

`agents/nodes/*`에는 예전 상세 노드가 있지만, 아직 현재 graph에 직접 연결하지 않았다.

이유:

```text
예전 노드들은 tech_result, ip_result, critic_result 같은 옛 State 구조를 사용한다.
현재 표준 구조는 agent_runs, evidence_items, ip_overlap_candidates다.
```

따라서 바로 연결하면 State 구조가 두 벌로 갈라질 수 있다. 다음 단계에서 상세 노드를 현재 계약에 맞게 리팩터링한 뒤 연결하는 것이 안전하다.

## 다음 작업 후보

1. `agents/nodes/*` 상세 노드를 현재 `AgentRun/evidence_items/ip_overlap_candidates` 계약에 맞춰 리팩터링
2. `app/api.py`의 `/analyze`가 실제 `agents.graph.build_graph()`를 호출하도록 연결
3. Critic ON/OFF 평가 하네스 추가
4. 실제 LLM 호출 계층 추가
5. 실제 DB와 retrieval을 붙일 때 `retrieval/tools.py` 내부만 교체

## PostgreSQL 연결 skeleton

실제 DB 연결을 위해 다음 파일을 추가했다.

```text
db/connection.py
  DATABASE_URL을 .env 또는 환경변수에서 읽고 psycopg2 connection/cursor를 제공한다.

scripts/inspect_db.py
  information_schema와 pg_indexes를 이용해 documents, ideas, hypotheses,
  evidence_items, analysis_jobs, agent_runs의 컬럼/PK/FK/index/row count를 출력한다.

scripts/inspect_documents.py
  documents의 embedding 차원, source_type별 count, 샘플 문서 5개를 출력한다.

retrieval/pgvector_search.py
  documents.embedding 기준 pgvector top_k 검색 함수다.
  query embedding 생성은 아직 TODO/mock vector로 분리되어 있다.

agents/db_workflow.py
  LLM 호출 없는 DB workflow skeleton이다.
  create_idea(), create_analysis_job(), create_hypotheses(),
  search_evidence_for_hypothesis(), log_agent_run(), run_analysis_workflow()를 제공한다.
```

실행 전 `.env` 또는 PowerShell 환경변수에 `DATABASE_URL`이 있어야 한다.

```powershell
$env:DATABASE_URL="postgresql://postgres:<PASSWORD>@your-db-host.ap-northeast-1.rds.amazonaws.com:5432/venturescout"
```

스키마 확인:

```bash
python scripts/inspect_db.py
```

documents 확인:

```bash
python scripts/inspect_documents.py
```

pgvector 검색 확인:

```bash
python -m retrieval.pgvector_search
```

검색 결과의 `distance`는 query embedding과 documents.embedding 사이의 cosine distance다.

```text
distance가 작다  -> query와 문서가 의미상 더 가깝다
distance가 크다  -> query와 문서가 의미상 덜 가깝다
```

단, 현재 `retrieval/pgvector_search.py`의 query embedding은 실제 embedding 모델이 아니라 deterministic mock vector다. 따라서 지금 검색 결과는 **검색 품질 평가가 아니라 DB 연결과 pgvector SQL 동작 확인용**이다.

중요한 해석 원칙:

```text
가까운 문서가 나온다
  -> 관련 근거 또는 IP 중첩 후보일 수 있어 검토해야 한다.

가까운 문서가 안 나온다
  -> 현재 검색 조건에서는 강한 유사 신호가 약하다는 뜻이다.
  -> 하지만 "기존 특허와 안 겹친다", "안전하다"로 단정하면 안 된다.
```

IP 판단은 documents 전체 유사도만으로 하지 않는다. 최종적으로는 `claim_limitations`, `ip_overlap_candidates`, 수동 claim chart 검토까지 이어져야 한다.

DB workflow skeleton 실행:

```bash
python -m agents.db_workflow
```

현재 RDS 접속 테스트 결과, host에는 도달했지만 password가 없어 실패했다.

```text
fe_sendauth: no password supplied
```

따라서 실제 컬럼/PK/FK/index 확인은 `DATABASE_URL`에 비밀번호를 넣은 뒤 `scripts/inspect_db.py`로 먼저 수행해야 한다. INSERT 계층은 실행 시 information_schema에서 실제 컬럼을 읽고, 존재하는 컬럼만 사용하도록 작성되어 있다.

DB workflow skeleton 실행 시 `agent_runs.agent_name`은 DB CHECK constraint를 따른다. `skeleton` 같은 임의 이름은 들어갈 수 없고, 현재는 가설 코드에 따라 실제 agent 이름을 사용한다.

```text
H1 -> market
H2 -> competitor
H3 -> bm
H4 -> tech
H5 -> ip
```

이번 실행이 실제 LLM 분석이 아니라 skeleton이라는 정보는 `agent_name`이 아니라 `agent_runs.output_json` 안에 남긴다.

```json
{
  "skeleton": true
}
```

## 개발 원칙

- 모든 에이전트 주장은 `evidence_id`를 인용해야 한다.
- 확정적 표현을 피하고 confidence를 명시한다.
- IP Agent는 법적 판단을 하지 않고 “중첩 신호”만 제공한다.
- Critic은 낙관적 결론을 보강하는 역할이 아니라, 근거 누락과 과장을 잡는 역할이다.
- mock 데이터는 `agents/mock_data.py`를 단일 원천으로 유지한다.
