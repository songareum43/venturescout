#!/usr/bin/env python3
"""
EC2 전용 독립 임베딩 러너.
config 모듈 불필요 — DB_DSN 환경변수만 있으면 동작.

크로스리전(EC2=서울, RDS=도쿄)에서 장시간 연결이 끊기는 문제 방지:
  - server-side 커서로 연결을 장시간 붙들지 않음. 대신 청크(CHUNK)마다 새 연결을
    열고 닫아 연결 수명을 짧게 유지 → 크로스리전 유휴 단절 자체를 회피.
  - 모든 DB 작업은 connect_with_retry / 재시도 루프로 감싸 RDS 일시 불통도 버팀.
  - 쿼리가 embedding IS NULL 기준이라, 어디서 끊겨도 항상 남은 것부터 이어서 진행.
"""
import logging
import os
import time

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

DB_DSN = os.environ["DB_DSN"]
MODEL_NAME = "AI-Growth-Lab/PatentSBERTa"
EMBED_BATCH = 64       # GPU encode 배치
CHUNK = 2000           # 한 연결에서 처리할 행 수 (연결 수명 ~20초로 제한)

KEEPALIVE_OPTS = dict(
    keepalives=1,
    keepalives_idle=30,
    keepalives_interval=10,
    keepalives_count=5,
    connect_timeout=30,
)


def connect_with_retry(max_attempts: int = 30):
    """연결이 될 때까지 백오프 재시도. RDS 일시 불통/네트워크 단절을 버팀."""
    attempt = 0
    while True:
        try:
            conn = psycopg2.connect(DB_DSN, **KEEPALIVE_OPTS)
            register_vector(conn)
            return conn
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            attempt += 1
            if attempt >= max_attempts:
                raise
            wait = min(60, 5 * attempt)
            logger.warning(
                f"연결 실패({type(e).__name__}: {str(e).strip()}). "
                f"{wait}초 후 재시도 ({attempt}/{max_attempts})"
            )
            time.sleep(wait)


ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def fetch_chunk(conn, table, id_col, text_col, last_id, limit):
    """last_id 이후의 미임베딩 행을 limit개 가져온다(keyset + embedding IS NULL).

    임베딩된 행이 uuid 공간에 흩어져 있어도, last_id를 단조 증가시키며 전체 id 공간을
    한 번씩만 통과하므로 누락이 없다. 부분 인덱스 idx_<table>_unembedded 를 사용해 빠름.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            f"""
            SELECT {id_col}, {text_col}
            FROM {table}
            WHERE {id_col} > %s AND embedding IS NULL AND {text_col} IS NOT NULL
            ORDER BY {id_col}
            LIMIT %s
            """,
            (last_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def update_batch(conn, table, id_col, pairs):
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            f"UPDATE {table} SET embedding = %s WHERE {id_col} = %s",
            pairs,
            page_size=256,
        )


def embed_table(model, table, id_col, text_col):
    """한 테이블 전체 임베딩. 청크마다 새 연결을 열고 닫아 연결 수명을 짧게 유지.
    keyset(last_id 이후) 방식으로 진행하며, 끊겨도 DB의 마지막 임베딩 위치부터 재개."""
    total = 0
    last_id = ZERO_UUID   # 전체 id 공간을 처음부터. embedding IS NULL 필터가 이미 된 것을 건너뜀
    while True:
        conn = connect_with_retry()
        try:
            rows = fetch_chunk(conn, table, id_col, text_col, last_id, CHUNK)
            if not rows:
                break
            # 청크를 GPU 배치 단위로 인코딩 후 즉시 update/commit
            for i in range(0, len(rows), EMBED_BATCH):
                batch = rows[i : i + EMBED_BATCH]
                texts = [r[text_col] for r in batch]
                embeddings = model.encode(
                    texts, batch_size=EMBED_BATCH, show_progress_bar=False
                )
                pairs = [(emb.tolist(), r[id_col]) for r, emb in zip(batch, embeddings)]
                update_batch(conn, table, id_col, pairs)
                conn.commit()
                total += len(pairs)
            last_id = rows[-1][id_col]   # keyset 전진
            logger.info(f"[{table}] {total}건 완료 ({id_col}={last_id})")
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            # 청크 처리 중 끊김 → 새 연결로 재개. last_id는 직전 성공 청크 위치를 유지하고,
            # 부분 commit된 행은 embedding IS NULL 필터가 자동으로 건너뛰므로 중복 없음.
            logger.warning(
                f"[{table}] 처리 중 연결 끊김({type(e).__name__}: {str(e).strip()}). "
                "새 연결로 재개"
            )
            time.sleep(3)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return total


def run():
    logger.info(f"모델 로드: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    total_cl = embed_table(model, "claim_limitations", "limitation_id", "normalized_text")
    total_docs = embed_table(model, "documents", "document_id", "clean_text")

    # D3 Gate
    conn = connect_with_retry()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(embedding) FROM claim_limitations")
        total, embedded = cur.fetchone()
    conn.close()

    gate = "PASS" if total == embedded else f"FAIL (누락 {total - embedded}건)"
    logger.info(f"D3 Gate: {embedded}/{total} → {gate}")

    return {"claim_limitations": total_cl, "documents": total_docs, "d3_gate": gate}


if __name__ == "__main__":
    result = run()
    logger.info(f"최종 결과: {result}")
