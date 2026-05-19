from contextlib import contextmanager
from psycopg_pool import ConnectionPool
import config

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL이 설정되지 않았습니다 (.env 확인)")
        _pool = ConnectionPool(
            conninfo=config.DATABASE_URL,
            min_size=0,
            max_size=4,
            timeout=60,
            kwargs={"autocommit": True},
        )
    return _pool


@contextmanager
def connection():
    pool = _get_pool()
    with pool.connection() as conn:
        yield conn


def close():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
