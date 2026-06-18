"""
pgvector 적재 + D3 게이트 동기화 검증.

v4 스키마 임베딩 대상 (B 책임):
- claim_limitations.normalized_text → embedding  (⑤ IP 시그니처 검색 단위)
- documents.clean_text              → embedding  (② Market·③ Competitor evidence 검색 단위)
"""
from __future__ import annotations

import logging
from typing import Iterator

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

from config import config
from pipeline.embedder import PatentEmbedder

logger = logging.getLogger(__name__)


class PatentIndexer:
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

    # ── 메인 파이프라인 ──────────────────────────────────────────────────────

    def run(self, batch_size: int = 256) -> dict:
        """claim_limitations + documents 임베딩 적재 (이미 채워진 행은 건너뜀, 재실행 안전).

        HNSW 인덱스를 임베딩 전 삭제하고 완료 후 재생성한다.
        인덱스를 유지한 채 bulk UPDATE하면 매 행마다 HNSW가 갱신되어 ~5x 느려짐.
        """
        self._drop_hnsw_indexes()
        result = {
            "claim_limitations": self.run_claim_limitations(batch_size),
            "documents": self.run_documents(batch_size),
        }
        self._rebuild_hnsw_indexes()
        return result

    def run_claim_limitations(self, batch_size: int = 256) -> dict:
        """A가 분해한 claim_limitations.normalized_text → embedding (시그니처 검색 단위)."""
        total = 0
        for batch in self._fetch_unembedded("claim_limitations", "limitation_id", "normalized_text", batch_size):
            texts = [row["normalized_text"] for row in batch]
            embeddings = self.embedder.embed_batch(texts, batch_size=batch_size)
            pairs = [(emb.tolist(), row["limitation_id"]) for row, emb in zip(batch, embeddings)]
            self._update_embedding_batch("claim_limitations", "limitation_id", pairs)
            total += len(pairs)
            self.conn.commit()
            logger.info(f"[claim_limitations] indexed {total} so far...")
        return {"indexed": total}

    def run_documents(self, batch_size: int = 256) -> dict:
        """A가 적재한 documents.clean_text → embedding (evidence 검색 단위)."""
        total = 0
        for batch in self._fetch_unembedded("documents", "document_id", "clean_text", batch_size):
            pairs = []
            for row in batch:
                embedding = self.embedder.embed(row["clean_text"])
                pairs.append((embedding.tolist(), row["document_id"]))
                total += 1
            self._update_embedding_batch("documents", "document_id", pairs)
            self.conn.commit()
            logger.info(f"[documents] indexed {total} so far...")
        return {"indexed": total}

    # ── D3 게이트: 동기화 검증 ────────────────────────────────────────────────

    def verify_sync(self) -> dict:
        """
        D3 게이트에서 반드시 실행.
        claim_limitations 전체 건수 = pgvector 임베딩 적재 건수 일치 확인.
        불일치 시 즉시 수정 — 매핑 깨지면 ⑤ IP 에이전트(ip_overlap_candidates) 전체가 오염됨.
        """
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM claim_limitations")
            total: int = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM claim_limitations WHERE embedding IS NOT NULL")
            embedded: int = cur.fetchone()[0]

        sync_ok = total == embedded

        result = {
            "total_limitations": total,
            "total_embedded":    embedded,
            "missing":           total - embedded,
            "sync_rate":         (embedded / total) if total else 0.0,
            "gate_pass":         sync_ok,
        }

        if not sync_ok:
            logger.error(
                f"[D3 GATE FAIL] {result['missing']}건 임베딩 누락. "
                "indexer.run_claim_limitations()을 다시 실행하세요."
            )
        else:
            logger.info("[D3 GATE PASS] claim_limitations ↔ pgvector 동기화 완료.")

        return result

    # ── HNSW 인덱스 관리 ──────────────────────────────────────────────────────

    def _drop_hnsw_indexes(self) -> None:
        """bulk 임베딩 전 HNSW 인덱스 삭제. 인덱스 없으면 무시."""
        with self.conn.cursor() as cur:
            cur.execute("DROP INDEX IF EXISTS idx_claim_limitations_embedding")
            cur.execute("DROP INDEX IF EXISTS idx_documents_embedding")
        self.conn.commit()
        logger.info("[HNSW] 인덱스 삭제 완료 — bulk UPDATE 모드")

    def _rebuild_hnsw_indexes(self) -> None:
        """임베딩 완료 후 HNSW 인덱스 재생성 (m=16, ef_construction=64)."""
        logger.info("[HNSW] 인덱스 재생성 중... (수 분 소요)")
        with self.conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_claim_limitations_embedding
                ON claim_limitations
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_documents_embedding
                ON documents
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
        self.conn.commit()
        logger.info("[HNSW] 인덱스 재생성 완료")

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────────

    def _fetch_unembedded(
        self, table: str, id_col: str, text_col: str, batch_size: int
    ) -> Iterator[list[dict]]:
        """임베딩이 비어 있고 텍스트가 있는 행만 배치로 가져옴. (table/컬럼명은 내부 상수만 전달)"""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                f"""
                SELECT {id_col}, {text_col}
                FROM {table}
                WHERE embedding IS NULL AND {text_col} IS NOT NULL
                ORDER BY {id_col}
                """
            )
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                yield [dict(r) for r in rows]

    def _update_embedding_batch(self, table: str, id_col: str, pairs: list[tuple]) -> None:
        """(embedding_list, row_id) 쌍을 execute_batch로 한 번에 UPDATE."""
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                f"UPDATE {table} SET embedding = %s WHERE {id_col} = %s",
                pairs,
                page_size=256,
            )
