"""Async Postgres pool against Supabase (transaction pooler friendly)."""
import os
from urllib.parse import urlsplit, urlunsplit

from .config import env


def conninfo() -> str:
    """Use Supavisor transaction mode (6543) by default so many services don't exhaust the session pool."""
    url = env("DATABASE_URL").strip().strip('"').strip("'")
    parts = urlsplit(url)
    if "pooler.supabase.com" in (parts.hostname or "") and parts.port == 5432 \
            and os.environ.get("DB_USE_SESSION_POOLER", "false").lower() != "true":
        netloc = parts.netloc.rsplit(":", 1)[0] + ":6543"
        parts = parts._replace(netloc=netloc)
    return urlunsplit(parts)


def make_pool(max_size: int = 3):
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool  # only services need the pool; ingestion uses plain psycopg

    return AsyncConnectionPool(
        conninfo=conninfo(),
        min_size=0,
        max_size=max_size,
        max_idle=30,
        timeout=15,
        kwargs={"row_factory": dict_row, "prepare_threshold": None, "connect_timeout": 15},
        open=False,
    )
