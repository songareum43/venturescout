"""documents.embedding 기반 pgvector 검색 함수.

query_text -> embedding 생성은 아직 실제 모델과 연결하지 않는다.
현재는 검색 SQL과 함수 경계를 먼저 만들고, embedding 생성부는 TODO/mock vector로 분리한다.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from db.connection import db_cursor


def get_document_embedding_dim() -> int:
    """documents.embedding의 실제 차원을 DB에서 읽는다."""

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT vector_dims(embedding) AS dim
            FROM public.documents
            WHERE embedding IS NOT NULL
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row or not row["dim"]:
            raise RuntimeError("documents.embedding에서 차원을 확인할 수 없습니다.")
        return int(row["dim"])


def build_query_embedding(query_text: str, dim: int) -> list[float]:
    """TODO: 실제 embedding 모델로 교체할 query embedding 생성 지점.

    지금은 DB 검색 SQL을 검증하기 위한 deterministic mock vector를 만든다.
    운영 전환 시 sentence-transformers, Bedrock Titan Embeddings, OpenAI embeddings 등
    팀이 선택한 모델로 이 함수만 교체하면 된다.
    """

    seed = hashlib.sha256(query_text.encode("utf-8")).digest()
    values: list[float] = []
    for i in range(dim):
        byte = seed[i % len(seed)]
        values.append((byte / 255.0) - 0.5)

    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [round(value / norm, 8) for value in values]


def to_pgvector_literal(values: list[float]) -> str:
    """pgvector가 이해하는 '[0.1,0.2,...]' 문자열로 변환한다."""

    return "[" + ",".join(str(value) for value in values) + "]"


def search_documents_by_vector(
    query_text: str,
    *,
    top_k: int = 5,
    query_embedding: list[float] | None = None,
) -> list[dict[str, Any]]:
    """documents.embedding 기준 cosine distance top_k 문서를 반환한다."""

    dim = get_document_embedding_dim()
    embedding = query_embedding or build_query_embedding(query_text, dim)
    if len(embedding) != dim:
        raise ValueError(f"query embedding dim mismatch: expected={dim}, got={len(embedding)}")

    vector_literal = to_pgvector_literal(embedding)

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                document_id::text AS document_id,
                source_type,
                ext_id,
                title,
                canonical_url,
                LEFT(COALESCE(clean_text, ''), 300) AS clean_text_preview,
                vector_dims(embedding) AS embedding_dim,
                embedding <=> %s::vector AS distance
            FROM public.documents
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vector_literal, vector_literal, top_k),
        )
        rows = cur.fetchall()

    return [dict(row) for row in rows]


if __name__ == "__main__":
    results = search_documents_by_vector("meeting summarization patent", top_k=5)
    for idx, item in enumerate(results, start=1):
        print(f"{idx}. distance={item['distance']:.4f} | {item['document_id']}")
        print(f"   source_type={item['source_type']} title={item['title']}")
        print(f"   preview={item['clean_text_preview']}")
