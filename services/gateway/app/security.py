"""Password hashing (Argon2id) with a high cost factor."""
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi's PasswordHasher defaults to Argon2id. Parameters follow the OWASP recommendation
# (m >= 19 MiB, t >= 2, p = 1); t=3 gives extra margin while staying light on a 256 MB container.
hasher = PasswordHasher(time_cost=3, memory_cost=19_456, parallelism=1, hash_len=32, salt_len=16)

# Verified against unknown emails so response time does not reveal whether an account exists.
_DUMMY_HASH = hasher.hash("wandersync-dummy-password")


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return hasher.check_needs_rehash(stored_hash)
