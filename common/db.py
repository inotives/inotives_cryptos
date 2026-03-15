import asyncpg
from contextlib import asynccontextmanager
from .config import settings


_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.db_dsn,
        min_size=2,
        max_size=10,
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def is_pool_initialized() -> bool:
    return _pool is not None


@asynccontextmanager
async def get_conn():
    if _pool is None:
        raise RuntimeError("DB pool not initialised. Call init_pool() first.")
    async with _pool.acquire() as conn:
        yield conn
