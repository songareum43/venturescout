"""
vector_search / evidence_search tool — C의 에이전트가 호출하는 핵심 인터페이스.
B가 만들고, C가 쓴다. 파라미터·반환 형식은 Day 1에 C와 합의 후 변경 금지.

v4 스키마 변경점:
- vector_search:   claim_limitations(시그니처 검색 단위) 대상, ⑤ IP 에이전트용
- evidence_search: documents(시드/웹/특허 문서) 대상, ② Market·③ Competitor용.
                    반환값에 stance는 없음 — evidence_items 적재(가설별 stance 태깅)는
                    별도 단계(C/그래프 오케스트레이션)에서 document_id를 참조해 수행.
"""
from __future__ import annotations

from langchain_core.tools import tool

from search.hybrid import HybridSearcher
from search.reranker import ReRanker

_searcher = HybridSearcher()
_reranker = ReRanker()

_INTERNAL_KEYS = frozenset({
    "_debug", "meta",
    # C팀 계약에 없는 hybrid.py 내부 점수 필드
    "lexical_score", "similarity_score", "limitation_order",
    # evidence_search: ext_id는 계약 필드 아님
    "ext_id",
})


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in _INTERNAL_KEYS}


@tool
def vector_search(
    query: str,
    top_k: int = 10,
    code_filter: str | None = None,
    prefer_contradicting: bool = True,
) -> list[dict]:
    """
    특허 청구항 limitation 하이브리드 검색 + rerank.
    C의 ⑤ IP 에이전트가 기술 요소별 청구항 중첩 분석에 사용.

    Args:
        query:                검색 쿼리 (기술 요소 키워드)
        top_k:                반환할 결과 수
        code_filter:          분류코드 접두사 (ex: 'G06F', 'H04W') — documents.meta.cpc_code
        prefer_contradicting: True면 반박·충돌 근거를 상위에 올림

    Returns:
        [
          {
            "limitation_id": str,
            "claim_id": str,
            "document_id": str,
            "patent_id": str,        # documents.ext_id
            "title": str,
            "normalized_text": str,
            "claim_no": int,
            "is_independent": bool,
            "hybrid_score": float,
            "rerank_score": float,
          },
          ...
        ]
    """
    # 특허당 다양성 확보: top_k * 3개 fetch 후 rerank, 특허당 최대 1개 limitation만 반환
    raw = _searcher.search_claim_limitations(
        query=query,
        top_k=top_k * 3,
        code_filter=code_filter,
    )
    ranked = _reranker.rerank(raw, prefer_contradicting=prefer_contradicting, top_k=top_k * 3)

    # 특허(patent_id) 단위 dedup — 같은 특허의 limitation 중 rerank_score 최상위 1개만 유지
    seen_patents: set[str] = set()
    deduped = []
    for item in ranked:
        pid = item.get("patent_id")
        if pid not in seen_patents:
            seen_patents.add(pid)
            deduped.append(item)
        if len(deduped) >= top_k:
            break
    return [_clean(item) for item in deduped]


@tool
def evidence_search(
    query: str,
    top_k: int = 8,
    source_types: list[str] | None = None,
    prefer_contradicting: bool = True,
) -> list[dict]:
    """
    documents 하이브리드 검색 (evidence 후보).
    ② Market, ③ Competitor 에이전트가 사용.

    Args:
        query:        검색 쿼리
        top_k:        반환 수
        source_types: ['seed_review', 'seed_competitor', 'seed_pricing', 'web'] 중 선택
        prefer_contradicting: True면 반박 근거 상위 (stance 없으면 영향 없음)

    Returns:
        [
          {
            "document_id": str,
            "source_type": str,
            "title": str,
            "clean_text": str,
            "reliability_score": float,
            "freshness_score": float,
            "hybrid_score": float,
            "rerank_score": float,
          },
          ...
        ]
    """
    raw = _searcher.search_documents(
        query=query,
        top_k=top_k * 2,
        source_types=source_types,
    )
    ranked = _reranker.rerank(raw, prefer_contradicting=prefer_contradicting, top_k=top_k)
    return [_clean(item) for item in ranked]


# B가 C에 넘기는 tool 목록 (LangGraph 노드에 바인딩)
TRACK_B_TOOLS = [vector_search, evidence_search]
