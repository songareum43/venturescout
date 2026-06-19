# solution_advisor 에이전트 — 설계

## 배경

VentureScout의 ⑦ Critic(`critic_node`, `agents/graph.py`)은 `go`/`pivot`/`kill`/`more_research` 중 하나로
최종 판정을 내린다. 그런데 최근까지 `decision_rule`의 if/elif 분기에는 `"kill"`을 실제로 만들어내는
경로가 하나도 없었다 — `Decision` 타입과 UI `DECISION_BADGE`에는 자리가 마련돼 있었지만 코드가
도달시키지 않는 죽은 값이었다. 이번 작업 직전에 `_decide()`로 판정 규칙을 분리하면서 `kill` 경로
2개(치명적 문제: IP 고위험+반박 근거 동시 존재 / 근거 약함: low confidence ≥3)를 추가해 해소했다
(커밋 `692df24`, `tests/test_critic_decision.py`).

`kill`이 실제로 발생할 수 있게 된 지금, "사업을 하면 안 된다"는 판정에서 그치지 않고 어떤 방향으로
피벗하면 좋을지 제안하는 후속 에이전트가 필요하다는 요청이 있었다. ⑦ Critic 바로 뒤에 붙는 8번째
에이전트로, `decision == "kill"`일 때만 실행되고 그 외에는 그래프에서 완전히 건너뛴다.

## 목표

- `kill` 판정일 때만 실행되는 light 에이전트 `solution_advisor`를 추가한다.
- 출력은 "여러 대안 나열"이 아니라 **단일 최적 대안 + 그 이유** 하나로 좁힌다.
- 기존 7개 노드와 동일한 계약(`AgentRun`, `VentureScoutState`)·동일한 코드 패턴(`_agent_output_with_llm`,
  `try_persist_agent_run`)을 그대로 따른다 — 새로운 추상화나 별도 파이프라인을 만들지 않는다.
- `decision != "kill"`이면 LLM 호출도, AgentRun 생성도, SSE stage 이벤트도 전혀 발생하지 않는다
  ("실행을 따로 하지 않아도 된다"는 원 요청을 그래프 레벨에서 보장).

## 범위 제외 (명시적으로 하지 않는 것)

- **UI(`app/ui.py`) 변경** — `_render_board()`는 `agent_runs`를 이름 하드코딩 없이 일반 순회로
  렌더링하므로 `solution_advisor`도 자동으로 표에 나타난다. 별도 "추천 대안" 섹션을 만드는 건
  Track D 영역이라 이번 설계에 포함하지 않는다.
- **`critic_run` 자체의 DB 미영속화 문제** — `critic_node`는 `_agent_run()`을 거치지 않고 `AgentRun`을
  직접 생성해 `try_persist_agent_run()`을 호출하지 않는다. 이 누락은 이번 작업 이전부터 있던 별개
  결함이라 손대지 않는다(아래 "알려진 한계" 참조).
- **`kill`을 만드는 임계값(low confidence ≥3, IP+반박 동시)의 추가 조정** — 이미 `692df24`에서 합의·구현
  완료. 이번 설계는 그 결과를 입력으로만 쓴다.
- **여러 대안 제시·다차원(고객/BM/기술) 분해** — 사용자가 명시적으로 "단일 최적 대안 + 이유"로 범위를
  좁히기로 결정함.

## 설계

### 1. 스키마 변경

**`shared/contracts.py`**
```python
AgentName = Literal[
    "structuring", "market", "competitor", "tech", "ip", "bm", "critic",
    "solution_advisor",
]
```
다른 필드 변경 없음 — `AgentRun`은 이미 `hypothesis_id: str | None`, `output_json: dict`(loose)를
지원하므로 새 컬럼이나 마이그레이션이 필요 없다(ADR-016 strict/loose 원칙 그대로 적용).

**`agents/llm.py`**
```python
MODEL_TIER_BY_AGENT: dict[str, ModelTier] = {
    ...,
    "solution_advisor": "haiku",
}
```
`model_tier_for_agent()`가 이미 `.get(agent_name, "haiku")`로 기본값을 갖고 있어 이 줄이 없어도
동작은 하지만, 다른 7개 에이전트와 동일하게 명시적으로 등록해 일관성을 유지한다.

### 2. State에서 읽는 값

`solution_advisor_node(state: VentureScoutState) -> dict`가 읽는 키:

| 키 | 용도 |
|---|---|
| `state["analysis_job"].job_id` | 다른 노드와 동일한 job_id 출처 |
| `state["critic"]` (`CriticResult`) | **핵심 입력.** `decision`(라우팅 조건), `summary`/`objections`(추천 이유의 재료), `grounded_on`(근거 ID) |
| `state.get("evidence_items", {})` | `critic.grounded_on`의 ID를 실제 `EvidenceItem`으로 환원 |
| `state.get("idea")` | LLM 프롬프트 컨텍스트(고객/문제/기존 솔루션 요약) |

다른 분석 노드(`market_result` 등)의 raw output_json은 다시 읽지 않는다 — critic이 이미
`summary`/`objections`로 종합해 둔 것을 "왜 kill인지"의 단일 진실원천으로 삼는다. 이렇게 하면
solution_advisor는 critic 한 곳만 의존하고, critic의 종합 로직이 바뀌어도 같이 따라간다.

### 3. 근거(`grounded_on`) 처리 — 빈 리스트가 될 수 없음을 구조적으로 보장

```python
critic = state["critic"]
evidence_items = state.get("evidence_items", {})
grounding_evidence = [
    evidence_items[eid] for eid in critic.grounded_on if eid in evidence_items
]
```

`_decide()`의 규칙 1(`missing_evidence`/`invalid_grounding`/`uncovered_hypotheses`)이 `kill`보다
우선순위가 높으므로, **`kill`에 도달했다는 것 자체가 모든 가설에 근거가 이미 정상적으로 연결되어
있다는 뜻**이다. 즉 `evidence_items`는 항상 비어있지 않고, `critic.grounded_on`의 모든 ID도 항상
`evidence_items` 안에 존재한다(같은 이유로 `invalid_grounding`이 비어있어야 kill에 도달하기 때문).
따라서 위 리스트 컴프리헨션은 항상 비어있지 않은 결과를 반환하며, `AgentRun.grounded_on`의
`min_length=1` 제약을 위반할 수 없다. 별도 fallback 분기는 두지 않는다(일어날 수 없는 경우에 대한
방어 코드를 추가하지 않는다).

### 4. 노드 로직

```python
def solution_advisor_node(state: VentureScoutState) -> dict:
    """kill 판정일 때만 critic 뒤에 실행되는 light 에이전트.

    여러 대안을 나열하지 않고, critic의 objections/summary에 근거해
    단일 최적 피벗 방향과 그 이유만 제시한다.
    """
    start_time = time.time()
    log_stage(logger, "8️⃣", "Solution Advisor (대안 방향 제안 - Light)")

    job_id = state["analysis_job"].job_id
    critic = state["critic"]
    evidence_items = state.get("evidence_items", {})
    grounding_evidence = [
        evidence_items[eid] for eid in critic.grounded_on if eid in evidence_items
    ]

    log_input(logger, {"job_id": job_id, "critic_decision": critic.decision})

    default_output = {
        "summary": "[MOCK] 현재 형태로는 추진이 어려우나 단일 방향으로 좁히면 재검증 여지가 있다.",
        "recommended_direction": "[MOCK] 범용 기능 대신 가장 근거가 강한 단일 세그먼트/워크플로우로 좁혀 재출발한다.",
        "rationale": "[MOCK] critic이 지적한 반박 근거·IP 리스크가 범용 범위에서 나왔으므로, 범위를 좁히면 동일 리스크의 노출이 줄어든다.",
        "next_experiment": "[MOCK] 좁힌 단일 방향으로 가장 약했던 가설 하나를 30일 내 재검증한다.",
    }

    output_json = _agent_output_with_llm(
        agent_name="solution_advisor",
        hypothesis_id="all",
        role=(
            "critic의 kill 판정 근거(objections·summary)를 바탕으로, "
            "여러 대안을 나열하지 말고 가장 현실적인 단일 방향과 그 이유만 제시한다. "
            "근거를 넘어서는 단정(법적/투자 확정 등)은 하지 않는다."
        ),
        default_output=default_output,
        context={
            "idea": state.get("idea"),
            "critic_summary": critic.summary,
            "critic_objections": critic.objections,
            "critic_missing_evidence": critic.missing_evidence,
            "grounded_evidence": grounding_evidence,
        },
    )

    agent_run = AgentRun(
        agent_run_id="run_mock_solution_advisor",
        job_id=job_id,
        hypothesis_id=None,  # 특정 H1~H5가 아니라 아이디어 전체에 대한 판단(critic_run과 동일 패턴)
        agent_name="solution_advisor",
        model_name=current_model_name("solution_advisor"),
        depth="light",
        confidence="low",  # 새 근거를 수집한 게 아니라 기존 kill 판단을 재해석한 제안이므로(ADR-014)
        grounded_on=[item.evidence_id for item in grounding_evidence],
        output_json=output_json,
        groundedness_score=1.0,
        overclaim_flag=False,
        status="done",
    )

    from pipeline.persistence import try_persist_agent_run  # 순환 import 방지
    db_run_id = try_persist_agent_run(agent_run, grounding_evidence)
    if db_run_id:
        agent_run = agent_run.model_copy(update={"agent_run_id": db_run_id})

    log_grounding(logger, "solution_advisor", agent_run.grounded_on, agent_run.confidence)

    result = {"agent_runs": [agent_run]}

    duration_ms = (time.time() - start_time) * 1000
    log_completion(logger, "Solution Advisor", duration_ms)

    return result
```

설계 근거:
- `hypothesis_id=None` 때문에 `_agent_run()` 헬퍼(타입이 `hypothesis_id: str`로 고정)를 그대로 못 쓴다.
  `critic_node`가 같은 이유로 `AgentRun(...)`을 직접 생성하는 패턴을 그대로 따른다.
- `confidence="low"`로 고정한다 — ADR-014의 light 정의("seed 검색 + evidence_id 묶음 + Low confidence +
  next_experiment")를 그대로 따르고, "기존 kill 판단을 재해석한 제안"이라는 성격상 새로운 근거
  강도를 계산할 근거가 없다(`_evidence_strength()`를 적용할 새 검색이 없음).
- `critic_run`과 달리 `try_persist_agent_run()`을 명시적으로 호출한다 — DB 적재는 다른 6개 노드가
  공통으로 받는 동작이고, `_agent_run()` 헬퍼의 독스트링도 원래 모든 노드가 이 동작을 공유하는
  것을 의도하고 있다. `critic_run`이 빠뜨린 것은 "알려진 한계"로 분리하고 새 노드는 정상적으로
  영속화되게 만든다.

### 5. 그래프 와이어링 — 조건부 엣지

```python
def _route_after_critic(state: VentureScoutState) -> str:
    return "solution_advisor" if state["critic"].decision == "kill" else "end"
```

`build_graph()`:
```python
graph.add_node("solution_advisor", solution_advisor_node)
...
graph.add_conditional_edges(
    "critic",
    _route_after_critic,
    {"solution_advisor": "solution_advisor", "end": END},
)
graph.add_edge("solution_advisor", END)
```
(기존의 `graph.add_edge("critic", END)` 한 줄을 위 3줄로 교체한다.)

`decision != "kill"`이면 `solution_advisor` 노드는 LangGraph 실행 큐에 전혀 올라가지 않는다 —
LLM 호출 0회, AgentRun 생성 0건, SSE `stage` 이벤트도 발생하지 않는다(`app/api.py`의
`astream_events`는 실제로 실행된 노드만 중계하므로 UI에 "실행 안 됨" 같은 더미 단계가 보이지 않는다).

### 6. 동반 수정 — `_agent_run()` 시그니처, `critic_node`의 `decision_rule` 문자열

- `_agent_run()`의 `hypothesis_id: str` → `hypothesis_id: str | None = None`로 타입을 넓힌다.
  기존 호출부(market/competitor/tech/ip/bm)는 전부 키워드 인자로 문자열을 명시 전달하므로 동작에
  영향이 없다. 이 변경 자체는 solution_advisor가 쓰지 않더라도(직접 `AgentRun` 생성 패턴을 쓰므로)
  타입을 정확하게 만들어 향후 critic 계열 노드가 헬퍼를 재사용할 길을 열어둔다는 부수 효과가 있다.
- `critic_node`의 `decision_rule` 문자열(대시보드/LLM 컨텍스트에 노출되는 사람이 읽는 설명)이
  `692df24`에서 바뀐 새 kill 규칙을 반영하지 못하고 옛 문구로 남아있다. 다음으로 교체한다:
  ```python
  decision_rule = (
      "missing/invalid grounding 또는 uncovered hypothesis가 있으면 more_research; "
      "high IP candidate와 반박 근거가 동시에 있으면(치명적 문제) kill; "
      "low confidence가 3개 이상이면(근거 약함) kill; "
      "high IP candidate만 있으면 pivot; "
      "반박 근거가 적고 low confidence가 1개 이하이면 go; "
      "그 외 반박 신호가 있으면 pivot."
  )
  ```
  이건 이번 기능과 직접 연결된 문서 동기화 수정이라 같은 작업에 포함한다(범위 밖 정리가 아니라
  같은 변경의 누락분).

## 검증 계획

- **단위 테스트 — 라우팅**: `_route_after_critic`에 `critic.decision`이 각각
  `kill`/`go`/`pivot`/`more_research`인 상태를 주고 반환값이 `"solution_advisor"`/`"end"`인지 확인.
- **단위 테스트 — 노드**: `solution_advisor_node`에 `critic`(kill, grounded_on 채움)·`evidence_items`·
  `idea`를 채운 state를 주고 반환된 `agent_runs[0]`이 `agent_name="solution_advisor"`,
  `hypothesis_id is None`, `depth="light"`, `confidence="low"`, `grounded_on`이 비어있지 않고
  `evidence_items`의 부분집합인지 확인. `AGENT_LLM_PROVIDER=mock`(기본값)이므로 Bedrock 호출 없음.
- **회귀(e2e)**: `tests/test_mock_graph.py`의 `isolate_mock_graph_from_external_search` 픽스처를
  재사용해 `build_graph().invoke(...)` 실행.
  - 기존 mock 데이터(기본 시나리오)에서는 `decision != "kill"`이면 `agent_runs`에
    `solution_advisor`가 **없어야** 한다.
  - low confidence ≥3 또는 IP+반박 조합이 되도록 mock evidence/candidate를 조정한 시나리오에서는
    `decision == "kill"`이고 `agent_runs`에 `solution_advisor` 1건이 있어야 한다.
- 전부 `AGENT_LLM_PROVIDER=mock`(기본값) 기준으로 검증 — Bedrock 실제 호출(비용·네트워크 발생)은
  이번 검증 범위에 포함하지 않는다.

## 알려진 한계

- `critic_run`이 `try_persist_agent_run()`을 호출하지 않아 DB에 영속화되지 않는 기존 결함은 이번
  작업에서 고치지 않는다. `solution_advisor_node`는 critic의 코드를 복사하면서 이 결함까지 복제하지
  않도록 의도적으로 다르게(영속화 포함) 작성한다.
- `solution_advisor`의 출력 품질은 mock 모드에서는 `[MOCK]` 접두 고정 문구다. 실제 의미 있는 추천은
  `AGENT_LLM_PROVIDER=bedrock` 연결 후에만 확인 가능하며, 이번 설계·검증 범위는 mock 모드의 계약
  정합성(스키마·그래프 분기)까지다.
- `kill`을 만드는 임계값(low confidence ≥3 등)이 보수적이거나 공격적인지는 실 데이터로 더 튜닝이
  필요할 수 있다 — `_decide()`의 "조정 가능 지점" 주석에 이미 명시되어 있고, 이번 작업의 범위는 아니다.
