"""Process-wide handles created in the FastAPI lifespan."""
from dataclasses import dataclass
from typing import Any

import httpx
from strawberry.fastapi import BaseContext
from psycopg_pool import AsyncConnectionPool

from .ratelimit import RateLimiter
from .sessions import Session, SessionStore


class State:
    pool: AsyncConnectionPool
    redis: Any
    sessions: SessionStore
    limiter: RateLimiter
    http: httpx.AsyncClient


state = State()


@dataclass
class Ctx(BaseContext):
    """GraphQL context for one request."""
    request: Any
    response: Any
    session: Session
    client_ip: str
