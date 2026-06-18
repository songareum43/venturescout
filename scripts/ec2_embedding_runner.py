#!/usr/bin/env python3
"""
EC2 전용 독립 임베딩 러너.
config 모듈 불필요 — DB_DSN 환경변수만 있으면 동작.
"""
import logging
import os
from typing import Iterator

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

DB_DSN = os.environ["DB_DSN"]
MODEL_NAME = "AI-Growth-Lab/PatentSBERTa"
EMBED_BATCH = 512   # GPU(A10G) 기준 최적 배치


def get_write_conn():
    conn = psycopg2.connect(DB_DSN)
    register_vector(conn)
    return conn


def fetch_unembedded(
    table: str, id_col: str, text_col: str
) -> Iterator[list[dict]]:
    """server-side named 커서로 스트리밍.

    - client-side fetchmany는 결과셋 전체를 RAM에 올린 뒤 잘라낸다(OOM 위험).
    - 쓰기 커넥션(write_conn)과 분리: write_conn.commit()이 같은 커넥션의
      named 커서를 닫지 않도록.
    """
    read_conn = psycopg2.connect(DB_DSN)
    try:
        with read_conn.cursor(
            name=f"fetch_{table}", cursor_factory=psycopg2.extras.DictCursor
        ) as cur:
            cur.itersize = EMBED_BATCH
            cur.execute(
                f"""
                SELECT {id_col}, {text_col}
                FROM {table}
                WHERE embedding IS NULL AND {text_col} IS NOT NULL
                ORDER BY {id_col}
                """
            )
            batch: list[dict] = []
            for row in cur:
                batch.append(dict(row))
                if len(batch) >= EMBED_BATCH:
                    yield batch
                    batch = []
            if batch:
                yield batch
    finally:
        read_conn.close()


def update_batch(conn, table: str, id_col: str, pairs: list[tuple]) -> None:
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            f"UPDATE {table} SET embedding = %s WHERE {id_col} = %s",
            pairs,
            page_size=256,
        )


def run():
    logger.info(f"모델 로드: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    write_conn = get_write_conn()

    # claim_limitations
    total_cl = 0
    for batch in fetch_unembedded("claim_limitations", "limitation_id", "normalized_text"):
        texts = [r["normalized_text"] for r in batch]
        embeddings = model.encode(texts, batch_size=EMBED_BATCH, show_progress_bar=False)
        pairs = [(emb.tolist(), r["limitation_id"]) for r, emb in zip(batch, embeddings)]
        update_batch(write_conn, "claim_limitations", "limitation_id", pairs)
        write_conn.commit()
        total_cl += len(pairs)
        logger.info(f"[claim_limitations] {total_cl}건 완료")

    # documents
    total_docs = 0
    for batch in fetch_unembedded("documents", "document_id", "clean_text"):
        texts = [r["clean_text"] for r in batch]
        embeddings = model.encode(texts, batch_size=EMBED_BATCH, show_progress_bar=False)
        pairs = [(emb.tolist(), r["document_id"]) for r, emb in zip(batch, embeddings)]
        update_batch(write_conn, "documents", "document_id", pairs)
        write_conn.commit()
        total_docs += len(pairs)
        logger.info(f"[documents] {total_docs}건 완료")

    # D3 Gate
    with write_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(embedding) FROM claim_limitations")
        total, embedded = cur.fetchone()

    gate = "PASS" if total == embedded else f"FAIL (누락 {total - embedded}건)"
    logger.info(f"D3 Gate: {embedded}/{total} → {gate}")
    write_conn.close()

    return {"claim_limitations": total_cl, "documents": total_docs, "d3_gate": gate}


if __name__ == "__main__":
    result = run()
    logger.info(f"최종 결과: {result}")
