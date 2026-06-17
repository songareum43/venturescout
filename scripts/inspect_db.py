"""VentureScout 핵심 테이블 스키마를 실제 DB에서 inspect한다.

컬럼명을 추측하지 않기 위해 INSERT 구현 전에 이 스크립트로 현재 DB 구조를 확인한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.connection import db_cursor, get_database_url


TABLES = [
    "documents",
    "ideas",
    "hypotheses",
    "evidence_items",
    "analysis_jobs",
    "agent_runs",
]


def _print_database_target() -> None:
    database_url = get_database_url()
    safe_url = database_url
    if "@" in safe_url and "://" in safe_url:
        scheme, rest = safe_url.split("://", 1)
        safe_url = f"{scheme}://***:***@{rest.split('@', 1)[1]}"
    print(f"DATABASE_URL: {safe_url}")


def inspect_tables() -> None:
    """컬럼, PK, FK, index, row count를 보기 좋게 출력한다."""

    _print_database_target()

    with db_cursor() as cur:
        for table_name in TABLES:
            print("\n" + "=" * 88)
            print(f"TABLE: public.{table_name}")
            print("=" * 88)

            cur.execute(
                """
                SELECT COUNT(*) AS row_count
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = %s
                """,
                (table_name,),
            )
            exists = cur.fetchone()["row_count"] == 1
            if not exists:
                print("NOT FOUND")
                continue

            cur.execute(f'SELECT COUNT(*) AS row_count FROM public."{table_name}"')
            print(f"rows: {cur.fetchone()['row_count']}")

            print("\n[COLUMNS]")
            cur.execute(
                """
                SELECT
                    c.ordinal_position,
                    c.column_name,
                    c.data_type,
                    c.udt_name,
                    c.is_nullable,
                    c.column_default,
                    c.character_maximum_length,
                    c.numeric_precision,
                    c.numeric_scale
                FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                  AND c.table_name = %s
                ORDER BY c.ordinal_position
                """,
                (table_name,),
            )
            for row in cur.fetchall():
                type_name = row["data_type"]
                if row["udt_name"] and row["udt_name"] != row["data_type"]:
                    type_name = f"{type_name} ({row['udt_name']})"
                extras = []
                if row["character_maximum_length"]:
                    extras.append(f"max_len={row['character_maximum_length']}")
                if row["numeric_precision"]:
                    extras.append(
                        f"numeric={row['numeric_precision']},{row['numeric_scale']}"
                    )
                if row["column_default"]:
                    extras.append(f"default={row['column_default']}")
                extra_text = f" | {'; '.join(extras)}" if extras else ""
                print(
                    f"  {row['ordinal_position']:>2}. {row['column_name']:<28} "
                    f"{type_name:<24} nullable={row['is_nullable']}{extra_text}"
                )

            print("\n[PRIMARY KEY]")
            cur.execute(
                """
                SELECT
                    tc.constraint_name,
                    kcu.column_name,
                    kcu.ordinal_position
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.table_schema = 'public'
                  AND tc.table_name = %s
                  AND tc.constraint_type = 'PRIMARY KEY'
                ORDER BY kcu.ordinal_position
                """,
                (table_name,),
            )
            pk_rows = cur.fetchall()
            if pk_rows:
                print(
                    "  "
                    + ", ".join(
                        f"{row['column_name']} ({row['constraint_name']})"
                        for row in pk_rows
                    )
                )
            else:
                print("  none")

            print("\n[FOREIGN KEYS]")
            cur.execute(
                """
                SELECT
                    tc.constraint_name,
                    kcu.column_name,
                    ccu.table_name AS foreign_table_name,
                    ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema = tc.table_schema
                WHERE tc.table_schema = 'public'
                  AND tc.table_name = %s
                  AND tc.constraint_type = 'FOREIGN KEY'
                ORDER BY tc.constraint_name, kcu.ordinal_position
                """,
                (table_name,),
            )
            fk_rows = cur.fetchall()
            if fk_rows:
                for row in fk_rows:
                    print(
                        f"  {row['column_name']} -> "
                        f"{row['foreign_table_name']}.{row['foreign_column_name']} "
                        f"({row['constraint_name']})"
                    )
            else:
                print("  none")

            print("\n[INDEXES]")
            cur.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = %s
                ORDER BY indexname
                """,
                (table_name,),
            )
            index_rows = cur.fetchall()
            if index_rows:
                for row in index_rows:
                    print(f"  {row['indexname']}: {row['indexdef']}")
            else:
                print("  none")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    inspect_tables()
