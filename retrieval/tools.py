"""
Track B — 검색 tool (에이전트가 호출).
shared.contracts의 EvidenceItem / IPOverlapCandidate를 반환 — 시그니처(반환 타입) 고정.
내부는 pgvector + tsvector 하이브리드 검색(search/hybrid.py) + rerank(search/reranker.py).

실행 모드 토글 (agents/llm.py의 AGENT_LLM_PROVIDER 패턴과 동일):
- 기본 RETRIEVAL=mock  → agents/mock_data의 고정 근거를 반환(RDS·임베더 불필요, 오프라인/테스트).
- RETRIEVAL=live       → 실제 RDS 하이브리드 검색(임베더 로드 + pgvector + rerank).
  반환 타입은 ko-agent(C팀) contracts와 일치하므로 agents/graph.py 교체 없이 동작.

주의(FK): live 모드의 EvidenceItem.document_id는 documents에 실재하는 행이지만,
mock의 document_id는 가짜다 → mock 결과를 evidence_items에 그대로 적재하면 FK 위반.
적재(persistence)는 live 모드에서만 하거나, mock document를 documents에 함께 넣어야 한다.
"""
from __future__ import annotations

import os
import uuid

from shared.contracts import EvidenceItem, IPOverlapCandidate

# ── 실행 모드 ──────────────────────────────────────────────────────────────

def retrieval_live() -> bool:
    """실 RDS 검색 모드인지. 기본은 mock(오프라인 안전)."""
    return os.getenv("RETRIEVAL", "mock").lower() == "live"


# ── 검색기 lazy 생성 (mock 모드에선 임베더/DB를 import·로드하지 않음) ──────────

_searcher = None
_reranker = None


def _get_engines():
    """live 모드에서만 HybridSearcher/ReRanker를 생성한다.
    import도 함수 안에서 — mock 실행은 search/pipeline 스택(torch 등)을 안 건드린다."""
    global _searcher, _reranker
    if _searcher is None:
        from search.hybrid import HybridSearcher
        from search.reranker import ReRanker
        _searcher = HybridSearcher()
        _reranker = ReRanker()
    return _searcher, _reranker


def retrieve(
    hypothesis_id: str,
    query: str,
    *,
    job_id: str = "",
    k: int = 5,
) -> list[EvidenceItem]:
    """가설별 찬반 근거 회수 (documents 하이브리드 검색 + rerank).

    mock 모드: agents/mock_data의 MOCK_EVIDENCE에서 해당 가설 근거를 반환.
    live 모드: RDS documents 하이브리드 검색.
    """
    if not retrieval_live():
        return _mock_retrieve(hypothesis_id, job_id=job_id, k=k)

    searcher, reranker = _get_engines()
    raw = searcher.search_documents(query=query, top_k=k * 2)
    ranked = reranker.rerank(raw, prefer_contradicting=True, top_k=k)

    return [
        EvidenceItem(
            evidence_id=str(item["document_id"]),
            job_id=job_id,
            hypothesis_id=hypothesis_id,
            document_id=str(item["document_id"]),
            source_type=item["source_type"],
            evidence_text=str(item["clean_text"])[:1000],
            stance=item.get("stance", "neutral"),
            relevance_score=float(item.get("hybrid_score") or 0.0),
            reliability_score=float(item.get("reliability_score") or 0.0),
        )
        for item in ranked
    ]


def vector_search(
    technical_elements: list[str],
    *,
    job_id: str = "",
    hypothesis_id: str = "",
    k: int = 10,
) -> list[IPOverlapCandidate]:
    """시그니처: 기술요소 ↔ 특허 limitation 매칭 후보 (claim_limitations 하이브리드 검색 + rerank).

    mock 모드: MOCK_IP_CANDIDATES 반환. live 모드: RDS claim_limitations 검색.
    """
    if not retrieval_live():
        return _mock_vector_search(hypothesis_id=hypothesis_id, job_id=job_id, k=k)

    query = " ".join(technical_elements)
    plan_technical_element = technical_elements[0] if technical_elements else ""

    searcher, reranker = _get_engines()
    raw = searcher.search_claim_limitations(query=query, top_k=k * 3)
    ranked = reranker.rerank(raw, prefer_contradicting=False, top_k=k * 3)

    # 특허(patent_id) 단위 dedup — 같은 특허의 limitation 중 rerank_score 최상위 1개만 유지
    seen_patents: set[str] = set()
    deduped = []
    for item in ranked:
        pid = item.get("patent_id")
        if pid not in seen_patents:
            seen_patents.add(pid)
            deduped.append(item)
        if len(deduped) >= k:
            break

    return [
        IPOverlapCandidate(
            candidate_id=str(uuid.uuid4()),
            job_id=job_id,
            hypothesis_id=hypothesis_id,
            limitation_id=str(item["limitation_id"]),
            evidence_id=str(item["document_id"]),
            plan_technical_element=plan_technical_element,
            lexical_score=float(item.get("lexical_score") or 0.0),
            similarity_score=float(item.get("similarity_score") or 0.0),
            hybrid_score=float(item["hybrid_score"]),
            rank=rank,
        )
        for rank, item in enumerate(deduped, start=1)
    ]


# ── mock 경로 (agents/mock_data 재사용) ───────────────────────────────────

def _mock_retrieve(hypothesis_id: str, *, job_id: str, k: int) -> list[EvidenceItem]:
    from agents.mock_data import MOCK_EVIDENCE

    rows = [e for e in MOCK_EVIDENCE if e["hypothesis_id"] == hypothesis_id][:k]
    return [
        EvidenceItem(
            evidence_id=e["evidence_id"],
            job_id=job_id,
            hypothesis_id=e["hypothesis_id"],
            document_id=e["document_id"],
            source_type=e["source_type"],
            evidence_text=e["evidence_text"],
            stance=e.get("stance", "neutral"),
            relevance_score=float(e.get("relevance_score") or 0.0),
            reliability_score=float(e.get("reliability_score") or 0.0),
        )
        for e in rows
    ]


def _mock_vector_search(*, hypothesis_id: str, job_id: str, k: int) -> list[IPOverlapCandidate]:
    from agents.mock_data import MOCK_IP_CANDIDATES

    rows = [
        c for c in MOCK_IP_CANDIDATES
        if not hypothesis_id or c.get("hypothesis_id") == hypothesis_id
    ][:k]
    return [
        IPOverlapCandidate(
            candidate_id=c["candidate_id"],
            job_id=job_id,
            hypothesis_id=c["hypothesis_id"],
            limitation_id=c["limitation_id"],
            evidence_id=c["evidence_id"],
            plan_technical_element=c["plan_technical_element"],
            lexical_score=float(c.get("lexical_score") or 0.0),
            similarity_score=float(c.get("similarity_score") or 0.0),
            hybrid_score=float(c.get("hybrid_score") or 0.0),
            rank=int(c.get("rank") or (i + 1)),
        )
        for i, c in enumerate(rows)
    ]
