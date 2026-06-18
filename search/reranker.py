"""
rerank — relevance · reliability · freshness · contradiction_value 4축.
contradiction_value는 반박 근거를 의도적으로 상위에 올려 Evidence Board의
"찬반 충돌" 신호를 강화함. Critic 에이전트의 먹잇감.

v4 스키마: reliability_score/freshness_score는 documents에 미리 계산되어
저장되므로(§ Schema_explains.md ④) 여기서는 그대로 읽기만 한다.
stance(supports|contradicts|neutral)는 evidence_items 단계(가설별)에서만
존재하므로, documents 직접 검색 결과에는 없을 수 있음 — 그 경우 neutral 취급.
"""
from __future__ import annotations

from config import config


class ReRanker:
    def __init__(
        self,
        relevance_w: float | None = None,
        reliability_w: float | None = None,
        freshness_w: float | None = None,
        contradiction_w: float | None = None,
    ):
        self.w = {
            "relevance":     relevance_w     or config.rerank_relevance_w,
            "reliability":   reliability_w   or config.rerank_reliability_w,
            "freshness":     freshness_w     or config.rerank_freshness_w,
            "contradiction": contradiction_w or config.rerank_contradiction_w,
        }

    def rerank(
        self,
        candidates: list[dict],
        prefer_contradicting: bool = True,
        top_k: int | None = None,
    ) -> list[dict]:
        """
        Args:
            candidates:            hybrid search 결과
            prefer_contradicting:  True면 반박 근거 contradiction_value 부스트
            top_k:                 반환 수 (None이면 전부)

        Returns:
            rerank_score 필드가 추가된 정렬된 리스트
        """
        top_k = top_k or config.top_k_return
        scored = [self._score(item, prefer_contradicting) for item in candidates]
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]

    # ── 내부 ─────────────────────────────────────────────────────────────────

    def _score(self, item: dict, prefer_contradicting: bool) -> dict:
        relevance = float(item.get("hybrid_score", 0.0))

        reliability = item.get("reliability_score")
        if reliability is None:
            reliability = config.source_reliability.get(item.get("source_type", "web"), 0.5)
        reliability = float(reliability)

        freshness = item.get("freshness_score")
        # TRACK_B §5-2: 출원일 기준 10년 선형 감쇠 — meta.filing_date 우선 계산
        meta = item.get("meta") or {}
        filing_date_str = str(meta.get("filing_date", "")) if isinstance(meta, dict) else ""
        if filing_date_str and len(filing_date_str) >= 4:
            import datetime
            try:
                years_ago = datetime.date.today().year - int(filing_date_str[:4])
                freshness = max(0.0, 1.0 - years_ago / 20.0)
            except (ValueError, TypeError):
                pass
        freshness = float(freshness) if freshness is not None else 0.5

        # contradiction_value — stance(가설별 태깅)가 있을 때만 의미 있음
        stance = item.get("stance", "neutral")
        if prefer_contradicting and stance == "contradicts":
            contradiction_value = 1.0
        elif stance == "supports":
            contradiction_value = 0.2
        else:
            contradiction_value = 0.5

        score = (
            self.w["relevance"]     * relevance
            + self.w["reliability"] * reliability
            + self.w["freshness"]   * freshness
            + self.w["contradiction"] * contradiction_value
        )

        return {
            **item,
            "freshness_score": round(freshness, 4),  # 동적 계산값으로 override
            "rerank_score": round(score, 4),
            "_debug": {
                "relevance":   round(relevance, 4),
                "reliability": round(reliability, 4),
                "freshness":   round(freshness, 4),
                "contradiction_value": round(contradiction_value, 4),
            },
        }
