"""
Track B — 검색 tool (에이전트가 호출).
shared.contracts의 EvidenceItem / IPOverlapCandidate를 반환 — 시그니처(반환 타입) 고정.
내부는 pgvector + tsvector 하이브리드 검색(search/hybrid.py) + rerank(search/reranker.py).

실제 데이터 전환 지점:
- retrieve(): documents 하이브리드 검색 → EvidenceItem 변환
- vector_search(): claim_limitations 하이브리드 검색 → IPOverlapCandidate 변환
- 반환 타입은 ko-agent (C팀) contracts와 일치하므로 agents/graph.py 교체 없이 동작.
"""
from __future__ import annotations

import uuid

from shared.contracts import EvidenceItem, IPOverlapCandidate
from search.hybrid import HybridSearcher
from search.reranker import ReRanker

_searcher = HybridSearcher()
_reranker = ReRanker()


def retrieve(
    hypothesis_id: str,
    query: str,
    *,
    job_id: str = "",
    k: int = 5,
) -> list[EvidenceItem]:
    """가설별 찬반 근거 회수 (documents 하이브리드 검색 + rerank)."""
    raw = _searcher.search_documents(query=query, top_k=k * 2)
    ranked = _reranker.rerank(raw, prefer_contradicting=True, top_k=k)

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
    """시그니처: 기술요소 ↔ 특허 limitation 매칭 후보 (claim_limitations 하이브리드 검색 + rerank)."""
    query = " ".join(technical_elements)
    plan_technical_element = technical_elements[0] if technical_elements else ""

    raw = _searcher.search_claim_limitations(query=query, top_k=k * 3)
    ranked = _reranker.rerank(raw, prefer_contradicting=False, top_k=k * 3)

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
