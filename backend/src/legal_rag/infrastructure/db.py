from contextlib import contextmanager
from functools import lru_cache

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from legal_rag.config import settings


@lru_cache
def pool():
    return ConnectionPool(
        settings().database_url.get_secret_value(),
        min_size=1,
        max_size=8,
        kwargs={"row_factory": dict_row},
        timeout=10,
        open=True,
    )


@contextmanager
def connection():
    with pool().connection() as conn:
        yield conn


def fetch(sql, params=()):
    with connection() as conn:
        return conn.execute(sql, params).fetchall()


def one(sql, params=()):
    with connection() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql, params=()):
    with connection() as conn:
        return conn.execute(sql, params).rowcount
