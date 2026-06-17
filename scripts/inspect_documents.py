"""documents 테이블의 벡터/출처/샘플 상태를 확인한다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.connection import db_cursor


def inspect_documents() -> None:
    """embedding 차원, source_type 분포, 샘플 문서 5개를 출력한다."""

    with db_cursor() as cur:
        print("=" * 88)
        print("documents overview")
        print("=" * 88)

        cur.execute("SELECT COUNT(*) AS row_count FROM public.documents")
        print(f"rows: {cur.fetchone()['row_count']}")

        print("\n[embedding dimensions]")
        cur.execute(
            """
            SELECT
                vector_dims(embedding) AS embedding_dim,
                COUNT(*) AS row_count
            FROM public.documents
            WHERE embedding IS NOT NULL
            GROUP BY vector_dims(embedding)
            ORDER BY embedding_dim
            """
        )
        dim_rows = cur.fetchall()
        if dim_rows:
            for row in dim_rows:
                print(f"  dim={row['embedding_dim']}: {row['row_count']} rows")
        else:
            print("  no non-null embeddings")

        print("\n[source_type counts]")
        cur.execute(
            """
            SELECT COALESCE(source_type, '(null)') AS source_type, COUNT(*) AS row_count
            FROM public.documents
            GROUP BY source_type
            ORDER BY row_count DESC, source_type
            """
        )
        for row in cur.fetchall():
            print(f"  {row['source_type']:<24} {row['row_count']}")

        print("\n[sample documents]")
        cur.execute(
            """
            SELECT
                document_id::text AS document_id,
                source_type,
                ext_id,
                title,
                LEFT(COALESCE(clean_text, ''), 180) AS clean_text_preview,
                vector_dims(embedding) AS embedding_dim,
                LEFT(embedding::text, 100) AS embedding_preview
            FROM public.documents
            ORDER BY document_id
            LIMIT 5
            """
        )
        for idx, row in enumerate(cur.fetchall(), start=1):
            print("-" * 88)
            print(f"{idx}. document_id: {row['document_id']}")
            print(f"   source_type: {row['source_type']}")
            print(f"   ext_id: {row['ext_id']}")
            print(f"   title: {row['title']}")
            print(f"   embedding_dim: {row['embedding_dim']}")
            print(f"   embedding_preview: {row['embedding_preview']}")
            print(f"   clean_text_preview: {row['clean_text_preview']}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    inspect_documents()
