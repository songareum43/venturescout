"""
하이브리드 검색: pgvector (semantic) + tsvector (keyword) 가중합.

- search_claim_limitations: claim_limitations(시그니처 검색 단위) → ⑤ IP 에이전트
- search_documents:          documents(evidence 검색 단위)        → ② Market, ③ Competitor
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

from config import config
from pipeline.embedder import PatentEmbedder
from pipeline.persistence import create_ip_overlap_candidates


class HybridSearcher:
    def __init__(self):
        self.embedder = PatentEmbedder()
        self._conn: psycopg2.extensions.connection | None = None

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                config.db_dsn,
                connect_timeout=config.db_connect_timeout,
            )
            register_vector(self._conn)
        return self._conn

    def search_claim_limitations(
        self,
        query: str,
        top_k: int | None = None,
        code_filter: str | None = None,   # CPC(USPTO) 또는 IPC(KIPRIS) 접두사 → documents.meta
        independent_only: bool = False,
    ) -> list[dict]:
        """
        특허 청구항 limitation 하이브리드 검색 (시그니처: 청구항 중첩 분석).

        Args:
            query:            기술 요소 키워드 (영어)
            top_k:            반환 수 (기본값 config.top_k_fetch)
            code_filter:      분류코드 접두사. ex) 'G06F', 'H04L' (documents.meta->>'cpc_code')
            independent_only: True면 독립항(상위 patent_claims.is_independent)만

        Returns:
            limitation_id, claim_id, document_id, patent_id, title, normalized_text,
            claim_no, is_independent, meta, hybrid_score 포함 dict 리스트
        """
        top_k = top_k or config.top_k_fetch
        query_vec = self.embedder.embed(query)
        ts_lang = "korean" if config.is_korean else "english"

        conditions = ["cl.embedding IS NOT NULL", "d.source_type = 'patent'"]
        params: dict = dict(vec=query_vec.tolist(), query=query, top_k=top_k)

        if independent_only:
            conditions.append("pc.is_independent = TRUE")
        if code_filter:
            conditions.append("d.meta->>'cpc_code' LIKE %(code)s")
            params["code"] = f"{code_filter}%"

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT
                cl.limitation_id,
                cl.claim_id,
                cl.normalized_text,
                cl.limitation_order,
                pc.claim_no,
                pc.is_independent,
                pc.document_id,
                d.ext_id  AS patent_id,
                d.title,
                d.meta,
                -- ① vector 유사도 (cosine distance → similarity)
                1 - (cl.embedding <=> %(vec)s::vector)              AS similarity_score,
                -- ② keyword 점수
                ts_rank(
                    to_tsvector('{ts_lang}', cl.normalized_text),
                    plainto_tsquery('{ts_lang}', %(query)s)
                )                                                    AS lexical_score,
                -- ③ 하이브리드 합산
                (
                    {config.vector_weight}  * (1 - (cl.embedding <=> %(vec)s::vector))
                  + {config.keyword_weight} * ts_rank(
                        to_tsvector('{ts_lang}', cl.normalized_text),
                        plainto_tsquery('{ts_lang}', %(query)s)
                    )
                )                                                    AS hybrid_score
            FROM claim_limitations cl
            JOIN patent_claims pc ON cl.claim_id = pc.claim_id
            JOIN documents d      ON pc.document_id = d.document_id
            WHERE {where_clause}
            ORDER BY hybrid_score DESC
            LIMIT %(top_k)s
        """

        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def find_ip_overlap_candidates(
        self,
        *,
        job_id: str,
        hypothesis_id: str,
        plan_technical_element: str,
        top_k: int | None = None,
        code_filter: str | None = None,
        independent_only: bool = True,
    ) -> list[str]:
        """
        plan_technical_element(자사 계획의 기술 요소 설명)로 claim_limitations를 검색하고,
        그 결과를 evidence_items + ip_overlap_candidates에 적재한다.

        Schema_explains.md ⑨: B(기계)가 candidate를 produce, ⑤ IP 에이전트(C)가 read.
        중첩 여부(supports/contradicts) 판단은 하지 않음 — evidence_items.stance='neutral'.

        Returns: candidate_id 리스트 (rank 순, 빈 검색결과면 [])
        """
        candidates = self.search_claim_limitations(
            plan_technical_element,
            top_k=top_k,
            code_filter=code_filter,
            independent_only=independent_only,
        )
        if not candidates:
            return []

        return create_ip_overlap_candidates(
            self.conn,
            job_id=job_id,
            hypothesis_id=hypothesis_id,
            plan_technical_element=plan_technical_element,
            candidates=candidates,
        )

    def search_documents(
        self,
        query: str,
        top_k: int | None = None,
        source_types: list[str] | None = None,
    ) -> list[dict]:
        """
        documents 하이브리드 검색 (evidence 후보).
        ② Market, ③ Competitor 에이전트가 시드/웹 데이터 검색에 사용.

        Args:
            query:        검색 쿼리
            top_k:        반환 수
            source_types: ['seed_review', 'seed_competitor', 'seed_pricing', 'web', 'patent'] 중 선택

        Returns:
            document_id, source_type, ext_id, title, clean_text, meta,
            reliability_score, freshness_score, hybrid_score 포함 dict 리스트
        """
        top_k = top_k or config.top_k_fetch
        query_vec = self.embedder.embed(query)
        ts_lang = "korean" if config.is_korean else "english"

        conditions = ["embedding IS NOT NULL", "clean_text IS NOT NULL"]
        params: dict = dict(vec=query_vec.tolist(), query=query, top_k=top_k)

        if source_types:
            conditions.append("source_type = ANY(%(source_types)s)")
            params["source_types"] = source_types

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT
                document_id,
                source_type,
                ext_id,
                title,
                clean_text,
                meta,
                reliability_score,
                freshness_score,
                (
                    {config.vector_weight}  * (1 - (embedding <=> %(vec)s::vector))
                  + {config.keyword_weight} * ts_rank(
                        to_tsvector('{ts_lang}', clean_text),
                        plainto_tsquery('{ts_lang}', %(query)s)
                    )
                ) AS hybrid_score
            FROM documents
            WHERE {where_clause}
            ORDER BY hybrid_score DESC
            LIMIT %(top_k)s
        """

        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
