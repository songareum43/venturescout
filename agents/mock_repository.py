"""
개발/테스트 환경에서 사용하는 Mock Repository.

현재 실제 DB 대신 agents.mock_data의 고정 데이터를 반환한다.
상세 노드(agents/nodes/*)가 아직 Repository 인터페이스를 기대하므로
올바른 모듈명인 agents.mock_repository에 구현을 둔다.

실제 데이터 전환 지점:
- 이 클래스는 운영용 Repository가 아니라 상세 노드 호환용 mock이다.
- 실제 DB를 붙일 때는 동일한 메서드 이름을 가진 PostgresRepository 같은 구현체로 교체한다.
- get_evidence_for_hypothesis(), get_ip_overlap_candidates(), insert_agent_run(), update_job_stage()가 우선 교체 대상이다.
"""

from __future__ import annotations

from typing import Any

from agents.mock_data import MOCK_EVIDENCE, MOCK_IP_CANDIDATES


class MockRepository:
    """실제 Repository를 붙이기 전까지 사용하는 mock 구현체."""

    def get_evidence_for_hypothesis(self, hypothesis_id: str) -> list[dict[str, Any]]:
        """가설 ID에 해당하는 evidence_items mock 데이터를 반환한다."""

        # 실제 데이터 전환 지점:
        # evidence_items + documents를 join해서 hypothesis_id 기준 근거를 가져온다.
        # source_type, stance, relevance_score, reliability_score까지 함께 반환해야 한다.
        return [
            evidence
            for evidence in MOCK_EVIDENCE
            if evidence["hypothesis_id"] == hypothesis_id
        ]

    def get_ip_overlap_candidates(
        self,
        job_id: str,
        hypothesis_id: str,
    ) -> list[dict[str, Any]]:
        """IP 시그니처 후보 mock 데이터를 반환한다."""

        # 실제 데이터 전환 지점:
        # ip_overlap_candidates를 job_id/hypothesis_id 기준으로 조회하고,
        # 필요하면 claim_limitations, patent_claims, documents를 join해 표시용 정보를 보강한다.
        return [
            {
                **candidate,
                "job_id": job_id,
            }
            for candidate in MOCK_IP_CANDIDATES
            if candidate["hypothesis_id"] == hypothesis_id
        ]

    def insert_agent_run(self, payload: dict[str, Any]) -> str:
        """Agent 실행 결과 저장을 흉내 낸다."""

        # 실제 데이터 전환 지점:
        # agent_runs 테이블에 payload를 insert하고 생성된 agent_run_id를 반환한다.
        agent_name = payload.get("agent_name", "unknown")
        print("[MOCK insert_agent_run]", agent_name)
        return f"run_{agent_name}"

    def insert_critic_objections(self, objections: list[dict[str, Any]]) -> None:
        """Critic 반론 저장을 흉내 낸다."""

        # 실제 데이터 전환 지점:
        # 별도 critic_objections 테이블을 둘지, agent_runs.output_json 내부에 둘지 결정한 뒤 저장한다.
        print("[MOCK insert_critic_objections]", len(objections))

    def update_job_stage(
        self,
        job_id: str,
        stage: str,
        progress_pct: int,
    ) -> None:
        """Job 진행 상태 업데이트를 흉내 낸다."""

        # 실제 데이터 전환 지점:
        # analysis_jobs.status/current_stage/progress_pct를 update한다.
        print(f"[MOCK stage] {job_id} | {stage} | {progress_pct}%")
