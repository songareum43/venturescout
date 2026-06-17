"""PostgreSQL 연결 공통 모듈.

DB 연결 정보는 환경변수 ``DATABASE_URL``에서 읽는다.
로컬 개발 편의를 위해 repo 루트의 ``.env`` 파일이 있으면 먼저 로드한다.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PsycopgConnection
from psycopg2.extras import RealDictCursor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def load_env_file(path: Path = ENV_PATH) -> None:
    """python-dotenv 없이 간단한 KEY=VALUE 형식의 .env를 로드한다."""

    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_database_url() -> str:
    """DATABASE_URL을 반환한다. 없으면 실행자가 바로 알 수 있게 예외를 낸다."""

    load_env_file()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL이 설정되어 있지 않습니다. "
            "repo 루트에 .env를 만들거나 PowerShell 환경변수로 DATABASE_URL을 설정하세요."
        )
    return database_url


def get_connection() -> PsycopgConnection:
    """psycopg2 connection을 생성한다."""

    return psycopg2.connect(get_database_url())


@contextmanager
def db_cursor(commit: bool = False) -> Iterator[RealDictCursor]:
    """RealDictCursor를 열고 자동으로 commit/rollback/close를 처리한다."""

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
