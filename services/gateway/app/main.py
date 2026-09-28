"""API Gateway: the single entry point for the frontend (GraphQL only)."""
import json
import logging
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
import strawberry
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from graphql import GraphQLError, OperationType, parse
from graphql.language.ast import FieldNode, OperationDefinitionNode
from strawberry.extensions import MaskErrors, MaxAliasesLimiter, MaxTokensLimiter, QueryDepthLimiter
from strawberry.extensions.disable_introspection import DisableIntrospection
from strawberry.fastapi import GraphQLRouter

from wandersync_common.config import env, env_bool
from wandersync_common.db import make_pool

from . import ratelimit as rl
from .graphql_db import recent_queries
from .schema import Mutation, Query
from .sessions import COOKIE_NAME, SessionStore
from .state import Ctx, state

logging.basicConfig(level=logging.INFO, format="%(asctime)s gateway %(levelname)s %(message)s")
log = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(_: FastAPI):
    state.pool = make_pool(max_size=3)
    await state.pool.open()
    state.redis = aioredis.from_url(env("REDIS_URL"), decode_responses=True)
    state.sessions = SessionStore(state.redis, env("JWT_SECRET"), int(env("SESSION_TTL_SECONDS", "1800")))
    state.limiter = rl.RateLimiter(state.redis)
    state.http = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=2.0))
    yield
    await state.http.aclose()
    await state.redis.aclose()
    await state.pool.close()


def client_ip(request: Request) -> str:
    # Only the nginx frontend can reach this service (no published port), so X-Real-IP is trustworthy there.
    if env_bool("TRUST_PROXY_HEADERS", False):
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


def _mutation_fields(body: dict) -> list[FieldNode]:
    try:
        doc = parse(body.get("query", ""))
    except Exception:
        return []
    out: list[FieldNode] = []
    for definition in doc.definitions:
        if isinstance(definition, OperationDefinitionNode) and definition.operation == OperationType.MUTATION:
            out.extend(s for s in definition.selection_set.selections if isinstance(s, FieldNode))
    return out


def _literal_arg(field: FieldNode, name: str, variables: dict) -> str:
    """Best-effort read of a string argument (used only to key the per-account limiter)."""
    for arg in field.arguments:
        if arg.name.value == name:
            v = arg.value
            if hasattr(v, "value"):
                return str(v.value).lower()
            if getattr(v, "name", None) is not None:  # $variable
                return str(variables.get(v.name.value, "")).lower()
    return ""


async def _enforce(rule: rl.Rule, identity: str) -> None:
    allowed, retry_after = await state.limiter.hit(rule, identity)
    if not allowed:
        log.warning("rate limit exceeded rule=%s identity=%s", rule.name, identity)
        raise HTTPException(429, "Demasiadas solicitudes. Intenta de nuevo más tarde.",
                            headers={"Retry-After": str(retry_after)})


async def get_context(request: Request, response: Response) -> Ctx:
    ip = client_ip(request)
    if request.method == "POST":
        origin = request.headers.get("origin")
        allowed = {o.strip() for o in env("ALLOWED_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080").split(",")}
        if origin and origin not in allowed:
            raise HTTPException(403, "Origin not allowed")
    session = await state.sessions.load(request.cookies.get(COOKIE_NAME))
    ctx = Ctx(request=request, response=response, session=session, client_ip=ip)

    # ---- Rate limiting on sensitive operations (authentication, checkout/payment) -> HTTP 429
    if request.method == "POST":
        try:
            body = await request.json()  # cached by Starlette; Strawberry re-reads the same body
        except Exception:
            body = {}
        if isinstance(body, dict):
            variables = body.get("variables") or {}
            for field in _mutation_fields(body):
                name = field.name.value
                if name == "login":
                    await _enforce(rl.LOGIN_PER_IP, ip)
                    await _enforce(rl.LOGIN_PER_ACCOUNT, f"{ip}:{_literal_arg(field, 'email', variables)}")
                elif name == "register":
                    await _enforce(rl.REGISTER_PER_IP, ip)
                elif name == "createBooking":
                    await _enforce(rl.CHECKOUT_PER_IP, ip)
                    await _enforce(rl.CHECKOUT_PER_USER, str(session.user_id or ip))
    if session.is_new:
        response.set_cookie(COOKIE_NAME, state.sessions.encode(session.id), max_age=state.sessions.ttl,
                            httponly=True, samesite="lax", secure=env_bool("COOKIE_SECURE", False), path="/")
    return ctx


def should_mask_error(error: GraphQLError) -> bool:
    """Hide unexpected exceptions; keep deliberate GraphQLError messages (validation, auth) visible."""
    original = error.original_error
    return original is not None and not isinstance(original, GraphQLError)


extensions = [
    lambda: QueryDepthLimiter(max_depth=8),
    lambda: MaxAliasesLimiter(max_alias_count=15),
    lambda: MaxTokensLimiter(max_token_count=2500),
    lambda: MaskErrors(should_mask_error=should_mask_error),  # internal errors are never leaked
]
if not env_bool("GRAPHQL_INTROSPECTION", True):
    extensions.append(DisableIntrospection)

schema = strawberry.Schema(query=Query, mutation=Mutation, extensions=extensions)
graphql_app = GraphQLRouter(schema, context_getter=get_context, allow_queries_via_get=False,
                            graphql_ide="graphiql" if env_bool("GRAPHQL_INTROSPECTION", True) else None)

app = FastAPI(title="WanderSync Gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(graphql_app, prefix="/graphql")


@app.get("/healthz")
async def healthz():
    await state.redis.ping()
    async with state.pool.connection() as conn:
        await conn.execute("select 1")
    return {"status": "ok"}


if env_bool("ENABLE_DEBUG_ENDPOINTS", False):
    @app.get("/debug/last-queries")
    async def last_queries():
        """Evidence that only requested columns reach the database (demo / tests)."""
        return {"queries": list(recent_queries)}
