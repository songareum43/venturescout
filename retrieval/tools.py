"""Live PostgreSQL/pgvector retrieval tools used by VentureScout agents."""

from __future__ import annotations

import os
import threading
import uuid

from shared.contracts import EvidenceItem, IPOverlapCandidate

# psycopg2 연결은 스레드 안전하지 않으므로 스레드마다 별도 인스턴스를 유지한다.
# 5개 에이전트가 병렬 스레드로 실행될 때 같은 연결을 공유하면 SSL 에러가 발생한다.
_local = threading.local()


def _require_live_retrieval() -> None:
    mode = os.getenv("RETRIEVAL", "live").lower()
    if mode != "live":
        raise RuntimeError(
            "RETRIEVAL must be 'live'. Fixed retrieval data is not allowed."
        )


def _get_engines():
    _require_live_retrieval()
    if not hasattr(_local, "searcher"):
        from search.hybrid import HybridSearcher
        from search.reranker import ReRanker

        _local.searcher = HybridSearcher()
        _local.reranker = ReRanker()
    return _local.searcher, _local.reranker


def retrieve(
    hypothesis_id: str,
    query: str,
    *,
    job_id: str = "",
    k: int = 5,
    source_types: list[str] | None = None,
) -> list[EvidenceItem]:
    """Retrieve evidence from live documents using hybrid search."""

    searcher, reranker = _get_engines()
    raw = searcher.search_documents(
        query=query,
        top_k=k * 2,
        source_types=source_types,
    )
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
    """Retrieve live claim-limitation overlap candidates."""

    query = " ".join(technical_elements)
    if not query.strip():
        raise RuntimeError("IP search requires at least one technical element.")

    searcher, reranker = _get_engines()
    raw = searcher.search_claim_limitations(query=query, top_k=k * 3)
    ranked = reranker.rerank(raw, prefer_contradicting=False, top_k=k * 3)

    seen_patents: set[str] = set()
    deduped = []
    for item in ranked:
        patent_id = item.get("patent_id")
        if patent_id not in seen_patents:
            seen_patents.add(patent_id)
            deduped.append(item)
        if len(deduped) >= k:
            break

    plan_technical_element = technical_elements[0]
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
