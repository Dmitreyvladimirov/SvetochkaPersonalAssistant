"""Field-level encryption for refresh tokens and mail bodies (SPEC.md §8).

Fernet with SVETA_TOKEN_KEY, which lives only in the environment: a database
dump without it is inert. Nothing here logs its input or output."""
from sveta.core import config


def _fernet():
    from cryptography.fernet import Fernet
    if not config.TOKEN_KEY:
        raise EnvironmentError("SVETA_TOKEN_KEY is not set")
    return Fernet(config.TOKEN_KEY.encode() if isinstance(config.TOKEN_KEY, str) else config.TOKEN_KEY)


def encrypt(text: str) -> bytes:
    return _fernet().encrypt(text.encode("utf-8"))


def decrypt(blob: bytes | memoryview) -> str:
    return _fernet().decrypt(bytes(blob)).decode("utf-8")
