"""Executes GraphQL against Supabase's pg_graphql (graphql.resolve) as a read-only Postgres role."""
import logging
from collections import deque
from typing import Any

from graphql import GraphQLError
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger("gateway.pg_graphql")

# Last queries sent to the database: evidence for the "no over-fetching" demo/tests (GET /debug/last-queries).
recent_queries: deque[str] = deque(maxlen=20)


async def resolve(pool: AsyncConnectionPool, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    recent_queries.append(query)
    log.info("pg_graphql query: %s", " ".join(query.split()))
    async with pool.connection() as conn:
        async with conn.transaction():
            # Only the ws_* read-only views are visible to this role; base tables are not.
            await conn.execute("set local role wandersync_reader")
            row = await (await conn.execute(
                "select graphql.resolve(%s, %s::jsonb) as result", (query, Jsonb(variables or {})))).fetchone()
    result = row["result"]
    if result.get("errors"):
        log.error("pg_graphql errors: %s", result["errors"])
        raise GraphQLError("La consulta al catálogo falló")
    return result["data"]
