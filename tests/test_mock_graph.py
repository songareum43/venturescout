import pytest

from agents import graph
from agents.mock_data import MOCK_DOCUMENTS, MOCK_EVIDENCE, MOCK_IP_CANDIDATES
from shared.contracts import EvidenceItem, IPOverlapCandidate


@pytest.fixture(autouse=True)
def isolate_mock_graph_from_external_search(monkeypatch):
    """mock graph 테스트가 RDS/pgvector에 접근하지 않도록 검색 경계를 고정한다."""

    def mock_retrieve(hypothesis_id, query, *, job_id="", k=5, source_types=None):
        rows = [
            item for item in MOCK_EVIDENCE
            if item["hypothesis_id"] == hypothesis_id
            and (source_types is None or item["source_type"] in source_types)
        ]
        return [EvidenceItem(job_id=job_id, **item) for item in rows][:k]

    def mock_vector_search(
        technical_elements,
        *,
        job_id="",
        hypothesis_id="H5",
        k=10,
    ):
        elements = set(technical_elements)
        return [
            IPOverlapCandidate(
                job_id=job_id,
                **{
                    key: value
                    for key, value in item.items()
                    if key != "limitation_text"
                },
            )
            for item in MOCK_IP_CANDIDATES
            if item["hypothesis_id"] == hypothesis_id
            and (
                not elements
                or item["plan_technical_element"] in elements
            )
        ][:k]

    monkeypatch.setenv("AGENT_LLM_PROVIDER", "mock")
    monkeypatch.setattr(graph, "retrieve", mock_retrieve)
    monkeypatch.setattr(graph, "vector_search", mock_vector_search)


def test_mock_graph_uses_mock_data_as_source():
    result = graph.build_graph().invoke(
        {
            "job_id": "job_test",
            "idea_id": "idea_test",
        }
    )

    assert result["idea"].title == "AI 회의록 자동화 SaaS"
    assert len(result["documents"]) == len(MOCK_DOCUMENTS)
    assert len(result["evidence_items"]) == len(MOCK_EVIDENCE)
    assert len(result["ip_overlap_candidates"]) == len(MOCK_IP_CANDIDATES)
    assert len(result["agent_runs"]) == 6


def test_every_agent_run_cites_existing_mock_evidence():
    result = graph.build_graph().invoke(
        {
            "job_id": "job_test",
            "idea_id": "idea_test",
        }
    )

    evidence_ids = set(result["evidence_items"])

    for run in result["agent_runs"]:
        assert set(run.grounded_on).issubset(evidence_ids)
