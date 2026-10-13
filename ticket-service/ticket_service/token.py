import hashlib
import secrets


def new_token() -> str:
    return secrets.token_hex(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
