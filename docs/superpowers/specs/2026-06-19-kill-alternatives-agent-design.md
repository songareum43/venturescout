# Kill 판정 시 대안 제안 에이전트 — 설계

## 배경

현재 `critic_node`의 `_decide()`([agents/graph.py:799-863](agents/graph.py#L799-L863))는 kill을
두 경로로 판정한다: (1) IP 고위험 후보 + 반박 근거 동시 존재(치명적 문제), (2) low confidence
에이전트 ≥3개(근거 약함). 그래프는 `critic → END`로 끝나기 때문에, kill이 나온 뒤에도 사용자에게
"그래서 어떻게 하면 되는지"에 대한 다음 행동이 제시되지 않는다.

## 목표

`decision == "kill"`일 때만 그래프 안에서 자동으로 이어지는 새 노드(`alternatives_node`)를 추가한다.
기존 아이디어의 핵심 컨셉은 유지하되 타겟 고객/포지셔닝/가격정책 등을 조정하는 대안 2~3개를,
kill이 어떤 원인으로 나왔는지(IP 충돌형 vs 근거 약함형)에 따라 다른 전략으로 제안한다.
백엔드(그래프 노드·계약) + API(SSE) + UI(Evidence Board) 전체를 다룬다.

## 범위 제외

- 완전히 다른 아이디어 제안 — 기존 아이디어의 핵심 컨셉을 유지한 조정안만 다룬다.
- go/pivot/more_research에 대한 후속 제안 — 이번 설계는 kill에 한정한다.
- grounding 계약 완화 — 새 에이전트도 다른 모든 노드와 동일하게 `grounded_on`을 코드가 programmatic하게
  채우는 방식을 그대로 따른다. 별도의 느슨한 검증 경로를 만들지 않는다.
- `agents/grounding.py`의 `validate_grounded_output()` 도입 — 이 함수는 현재 코드베이스 어디서도
  import/호출되지 않는 미사용 상태다(확인됨). 실제 grounding 강제는 `_agent_run()`이 evidence 객체
  리스트로부터 `grounded_on`을 직접 계산하는 방식으로 이뤄지고 있으므로, 새 노드도 이 실제 패턴을
  따르고 미사용 함수를 새로 끌어오지 않는다.

## 설계

### 1. 계약 추가 (`shared/contracts.py`, `shared/state.py`)

- `AgentName` Literal([contracts.py:26-34](shared/contracts.py#L26-L34))에 `"alternatives"` 추가.
- `VentureScoutState`([shared/state.py](shared/state.py))에 `critic_scorecard: dict[str, Any]` 필드 추가.
- 새 Pydantic 모델은 만들지 않는다 — 결과는 다른 에이전트들과 동일하게 `AgentRun.output_json`
  (loose dict)에 담는다.

### 2. `critic_node` 변경 (최소)

`critic_node`가 이미 내부에서 계산하는 `scorecard` dict([graph.py:961-970](agents/graph.py#L961-L970))를
반환 dict에 한 줄 추가해 state로 흘려보낸다: `"critic_scorecard": scorecard`. 새로운 계산은 없다.
이렇게 하면 `alternatives_node`가 동일한 임계값(IP `hybrid_score >= 0.78`, low confidence `>= 3`)을
중복 계산하지 않고 critic이 이미 낸 결과를 그대로 재사용하게 되어, 두 곳의 기준이 어긋날 위험이 없다.

### 3. `alternatives_node` (신규, `agents/graph.py`)

**kill 원인 판별** — 순수 함수로 분리해 LLM 없이 단위 테스트 가능하게 만든다:

```python
def _kill_reason(scorecard: dict) -> Literal["ip_conflict", "weak_evidence"]:
    if scorecard.get("high_ip_candidates") and scorecard.get("contradicting_evidence"):
        return "ip_conflict"
    return "weak_evidence"
```

**인용 근거 선택** — LLM이 임의로 근거를 인용하지 못하도록, "어떤 evidence_id를 인용해도 되는지"를
코드가 먼저 정한다. 이 역시 순수 함수로 분리한다:

```python
def _alternatives_evidence_ids(
    kill_reason: str, scorecard: dict, agent_runs: list[AgentRun]
) -> list[str]:
    if kill_reason == "ip_conflict":
        ip_evidence = {
            c.evidence_id for c in candidates if c.candidate_id in scorecard["high_ip_candidates"]
        }
        return sorted(set(scorecard["contradicting_evidence"]) | ip_evidence)
    low_conf_names = set(scorecard["low_confidence_agents"])
    return sorted({
        eid for run in agent_runs if run.agent_name in low_conf_names for eid in run.grounded_on
    })
```

(`IPOverlapCandidate.evidence_id` 필드는 이미 존재 — [contracts.py:157](shared/contracts.py#L157))

- 위 함수가 빈 리스트를 반환하면 다른 노드들의 "근거 0건 → graceful skip" 패턴과 동일하게
  `return {"agent_runs": []}`로 끝낸다(예: [graph.py:752-754](agents/graph.py#L752-L754)).

**LLM 호출** — 기존 `_agent_output_with_llm()`을 그대로 재사용한다.
- `agent_name="alternatives"`, `hypothesis_id="all"`(critic과 동일 관례)
- `role`은 `kill_reason`별로 다르게: `ip_conflict`는 "IP 회피 설계 또는 vertical 범위 축소 중심",
  `weak_evidence`는 "더 강한 근거가 있는 포지셔닝/타겟/가격정책으로 전환" 중심
- `required_fields=["kill_reason", "alternatives"]`
- `context`에 `idea`, `kill_reason`, `critic.objections`, 선택된 `evidence` 전달

출력 JSON 스키마:
```json
{
  "kill_reason": "weak_evidence",
  "alternatives": [
    {"title": "B2B 세그먼트로 타겟 전환", "rationale": "...", "next_experiment": "..."},
    {"title": "가격정책 조정(월구독→사용량과금)", "rationale": "...", "next_experiment": "..."}
  ]
}
```
대안 개수(2~3개)는 prompt 텍스트로만 지시한다 — 다른 노드들도 출력 리스트 길이를 코드로 검증하지
않는 것과 동일한 검증 깊이를 유지한다.

**AgentRun 생성** — 기존 `_agent_run()`을 그대로 재사용한다. `depth="light"`(새 retrieval 없이
기존 state만 사용), `confidence="low"`로 고정(검증되지 않은 새 방향이므로 — `_decide()`가
more_research에 항상 `"low"`를 쓰는 것과 같은 이유).

### 4. 그래프 라우팅 (`agents/graph.py` `build_graph()`)

```python
def _route_after_critic(state: VentureScoutState) -> str:
    return "alternatives" if state.get("decision") == "kill" else END

graph.add_node("alternatives", alternatives_node)
graph.add_conditional_edges(
    "critic", _route_after_critic, {"alternatives": "alternatives", END: END}
)
graph.add_edge("alternatives", END)
```

`graph.add_edge("critic", END)`(기존 [graph.py:1117](agents/graph.py#L1117))는 위 conditional_edges로
대체된다. `decision != "kill"`이면 `alternatives_node`는 그래프에서 전혀 호출되지 않는다 — LLM 호출,
AgentRun 생성, SSE stage 이벤트가 모두 발생하지 않는다.

### 5. API 변경 (`app/api.py`)

- `STAGE_LABELS`([api.py:32-40](app/api.py#L32-L40))에 `"alternatives": "⑧ 대안 제안 (kill 시에만)"` 추가.
  `KNOWN_NODES`가 이 dict의 키 집합이므로, 추가하지 않으면 이 노드의 stage/agent_run 이벤트가
  `astream_events` 필터([api.py:162](app/api.py#L162))에서 그냥 버려진다.
- `report` 이벤트 payload([api.py:230-240](app/api.py#L230-L240))는 변경 없음 — `agent_runs` 리스트에
  `agent_name="alternatives"` 항목이 다른 에이전트들과 동일하게 자연히 포함된다.

### 6. UI 변경 (`app/ui.py`)

- `AGENT_KO`([ui.py:39-43](app/ui.py#L39-L43))에 `"alternatives": "대안 제안"` 추가.
- `_render_board()`([ui.py:105-153](app/ui.py#L105-L153))에 kill 한정 섹션을 추가한다:

```python
alt_run = next((r for r in runs if r.get("agent_name") == "alternatives"), None)
if decision == "kill" and alt_run:
    alts = (alt_run.get("output_json") or {}).get("alternatives", [])
    lines.append("### 🔁 대안 제안 (이 방향이면 다시 검토할 만함)")
    for a in alts:
        lines.append(f"- **{a.get('title', '')}** — {a.get('rationale', '')}")
        lines.append(f"  - 다음 실험: {a.get('next_experiment', '')}")
    lines.append("")
```

이 섹션은 `_render_board()` 함수의 맨 끝(`objections`/`next_experiments` 섹션 다음)에 추가한다 —
"왜 죽었는지(objections) → 같은 방향을 검증/뒤집으려면(next_experiments) → 그래도 안 되면 다른
방향(alternatives)"의 순서가 자연스럽기 때문이다. `decision == "kill"`이고 `alternatives`
AgentRun이 실제로 존재할 때만 나타나므로 go/pivot/more_research 화면에는 영향이 없다.

## 테스트 계획 (`tests/test_alternatives_node.py`)

기존 `tests/test_critic_decision.py`가 `_decide()`만 LLM 없이 단위 테스트하는 철학을 그대로 따른다.

1. `_kill_reason()` — `high_ip_candidates`+`contradicting_evidence` 조합이면 `"ip_conflict"`,
   그렇지 않으면(low confidence 경로) `"weak_evidence"`를 반환하는지.
2. `_alternatives_evidence_ids()` — 두 `kill_reason` 각각에서 올바른 `evidence_id` 집합이
   나오는지(IP 후보의 `evidence_id` 포함 여부, low confidence 에이전트의 `grounded_on` 합산 여부).
3. 위 함수가 빈 리스트를 반환할 때 `alternatives_node`가 `{"agent_runs": []}`를 반환하는지
   (graceful skip).
4. `_route_after_critic()` — `decision`별로 `"alternatives"`/`END` 중 올바른 쪽으로 분기하는지.

LLM 호출이 들어가는 `_agent_output_with_llm` 경로는 기존 다른 노드들과 동일하게 통합/수동 테스트
영역으로 남기고, 위 4가지 순수 로직만 단위 테스트로 커버한다.

## 알려진 한계

- "포지셔닝/핏 조정"과 "완전히 다른 아이디어"의 경계는 LLM prompt(`role` 텍스트) 수준에서만
  강제된다. 코드로 강제하는 장치는 없으므로, 실제 운영 중 LLM이 경계를 넘는 제안을 하는지
  점검이 필요할 수 있다.
- 대안 리스트 길이(2~3개) 역시 prompt 수준 강제이며, `required_fields` 검증은 키 존재 여부만
  확인하므로 LLM이 1개나 4개를 반환해도 통과한다.
- `ip_conflict` 케이스에서 `high_ip_candidates`가 가리키는 `evidence_id`가 `evidence_items`에
  없는 경우(이론상 critic의 `invalid_grounding` 검사를 통과했다면 발생하지 않아야 함), 해당
  `evidence_id`는 조용히 스킵된다.
