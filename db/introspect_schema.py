"""읽기 전용 스키마 조회 — DATABASE_URL(또는 INTROSPECT_DSN)로 접속한 PostgreSQL의
public 스키마를 JSON으로 덤프한다. RDS와 로컬 DB의 스키마가 같은지 비교할 때 쓴다
(db/init.sql 동기화 검증). DDL을 실행하거나 대상 DB를 수정하지 않는다.

사용:
    python db/introspect_schema.py > dump.json                      # .env의 DATABASE_URL 사용
    INTROSPECT_DSN="postgresql://..." python db/introspect_schema.py > dump.json   # 다른 DB 지정
"""
from __future__ import annotations

import json
import os

import psycopg2
from dotenv import load_dotenv


def introspect(dsn: str) -> dict:
    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension ORDER BY extname;")
            extensions = [r[0] for r in cur.fetchall()]

            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
            )
            tables = [r[0] for r in cur.fetchall()]

            result: dict = {"extensions": extensions, "tables": {}}

            for table in tables:
                cur.execute(
                    """
                    SELECT a.attname,
                           format_type(a.atttypid, a.atttypmod) AS data_type,
                           a.attnotnull,
                           pg_get_expr(d.adbin, d.adrelid) AS default_expr
                    FROM pg_attribute a
                    JOIN pg_class c ON c.oid = a.attrelid
                    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                    WHERE c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
                    ORDER BY a.attnum
                    """,
                    (table,),
                )
                columns = [
                    {"name": r[0], "type": r[1], "not_null": r[2], "default": r[3]}
                    for r in cur.fetchall()
                ]

                cur.execute(
                    """
                    SELECT con.conname, pg_get_constraintdef(con.oid)
                    FROM pg_constraint con
                    JOIN pg_class t ON t.oid = con.conrelid
                    WHERE t.relname = %s
                    ORDER BY con.conname
                    """,
                    (table,),
                )
                constraints = [{"name": r[0], "def": r[1]} for r in cur.fetchall()]

                cur.execute(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname='public' AND tablename=%s ORDER BY indexname",
                    (table,),
                )
                indexes = [{"name": r[0], "def": r[1]} for r in cur.fetchall()]

                result["tables"][table] = {
                    "columns": columns,
                    "constraints": constraints,
                    "indexes": indexes,
                }

        return result
    finally:
        conn.close()


if __name__ == "__main__":
    load_dotenv()
    dsn = os.environ.get("INTROSPECT_DSN") or os.environ["DATABASE_URL"]
    print(json.dumps(introspect(dsn), indent=2, sort_keys=True, default=str))
