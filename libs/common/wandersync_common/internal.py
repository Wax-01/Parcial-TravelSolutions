"""Internal service authentication + demo failure injection for domain services."""
import asyncio
import hmac
from collections import defaultdict

from fastapi import Header, HTTPException

from .config import env_bool, internal_token


async def require_internal(x_internal_token: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_internal_token, internal_token()):
        raise HTTPException(status_code=401, detail="internal token required")


_flaky_calls: dict[str, int] = defaultdict(int)


async def apply_simulated_failure(mode: str | None, key: str) -> None:
    """mode: 'error' -> 503, 'timeout' -> hang, 'flaky:N' -> first N calls for `key` fail.

    Only active when ALLOW_FAILURE_SIMULATION=true (demo environments)."""
    if not mode or not env_bool("ALLOW_FAILURE_SIMULATION", True):
        return
    if mode == "error":
        raise HTTPException(status_code=503, detail="simulated failure")
    if mode == "timeout":
        await asyncio.sleep(60)
    if mode.startswith("flaky:"):
        limit = int(mode.split(":", 1)[1])
        _flaky_calls[key] += 1
        if _flaky_calls[key] <= limit:
            raise HTTPException(status_code=503, detail=f"simulated flaky failure {_flaky_calls[key]}/{limit}")
