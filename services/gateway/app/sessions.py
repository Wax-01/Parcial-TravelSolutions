"""Server-side sessions in Redis. The session ID is regenerated on authentication (anti Session Fixation)."""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from uuid import UUID

COOKIE_NAME = "ws_session"


@dataclass
class Session:
    id: str
    user_id: UUID | None = None
    is_new: bool = False  # cookie must be (re)issued to the browser

    @property
    def authenticated(self) -> bool:
        return self.user_id is not None


class SessionStore:
    def __init__(self, redis, secret: str, ttl_seconds: int = 1800):
        self.redis = redis
        self._secret = secret.encode()
        self.ttl = ttl_seconds

    # Cookie value = "<random id>.<hmac>": forged / tampered IDs are rejected without touching Redis.
    def _sig(self, sid: str) -> str:
        return hmac.new(self._secret, sid.encode(), hashlib.sha256).hexdigest()[:32]

    def encode(self, sid: str) -> str:
        return f"{sid}.{self._sig(sid)}"

    def decode(self, cookie: str | None) -> str | None:
        if not cookie or "." not in cookie:
            return None
        sid, _, sig = cookie.rpartition(".")
        return sid if hmac.compare_digest(sig, self._sig(sid)) else None

    @staticmethod
    def _key(sid: str) -> str:
        return f"sess:{sid}"

    async def create(self, user_id: UUID | None = None) -> Session:
        sid = secrets.token_urlsafe(32)
        await self.redis.hset(self._key(sid), mapping={"user_id": str(user_id) if user_id else ""})
        await self.redis.expire(self._key(sid), self.ttl)
        return Session(id=sid, user_id=user_id, is_new=True)

    async def load(self, cookie: str | None) -> Session:
        """Existing valid session, or a fresh anonymous one."""
        sid = self.decode(cookie)
        if sid:
            data = await self.redis.hgetall(self._key(sid))
            if data:
                await self.redis.expire(self._key(sid), self.ttl)  # sliding expiration
                uid = data.get("user_id")
                return Session(id=sid, user_id=UUID(uid) if uid else None)
        return await self.create()

    async def regenerate(self, old: Session, user_id: UUID) -> Session:
        """Issue a brand-new ID for the authenticated session and invalidate the previous one."""
        new = await self.create(user_id)
        await self.redis.delete(self._key(old.id))
        return new

    async def destroy(self, session: Session) -> None:
        await self.redis.delete(self._key(session.id))
