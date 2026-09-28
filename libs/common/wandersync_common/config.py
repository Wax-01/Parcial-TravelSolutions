import hashlib
import os


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def internal_token() -> str:
    """Shared secret for service-to-service calls, derived from JWT_SECRET (never sent to clients)."""
    return hashlib.sha256((env("JWT_SECRET") + ":internal").encode()).hexdigest()
