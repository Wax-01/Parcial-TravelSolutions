"""Sliding-window rate limiter on Redis (atomic via Lua)."""
import time
import uuid
from dataclasses import dataclass

_LUA = """
local key, now, window, limit, member = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, tonumber(oldest[2]) + window - now}
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return {1, 0}
"""


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_s: int


class RateLimiter:
    def __init__(self, redis):
        self.redis = redis
        self._script = redis.register_script(_LUA)

    async def hit(self, rule: Rule, identity: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now_ms = int(time.time() * 1000)
        allowed, retry_ms = await self._script(
            keys=[f"rl:{rule.name}:{identity}"],
            args=[now_ms, rule.window_s * 1000, rule.limit, f"{now_ms}-{uuid.uuid4().hex[:8]}"])
        return bool(allowed), max(1, int(-(-int(retry_ms) // 1000)))


# Sensitive operations (top-level GraphQL mutation fields) -> rules. Identity is chosen in main.py.
LOGIN_PER_ACCOUNT = Rule("login_acct", limit=5, window_s=60)
LOGIN_PER_IP = Rule("login_ip", limit=20, window_s=60)
REGISTER_PER_IP = Rule("register_ip", limit=5, window_s=600)
CHECKOUT_PER_USER = Rule("checkout_user", limit=5, window_s=60)
CHECKOUT_PER_IP = Rule("checkout_ip", limit=20, window_s=60)
