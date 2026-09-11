"""Field-level encryption for refresh tokens and mail bodies (SPEC.md §8).

Fernet with SVETA_TOKEN_KEY, which lives only in the environment: a database
dump without it is inert. Nothing here logs its input or output."""
from sveta.core import config


def _key_bytes() -> bytes:
    """The key as Fernet wants it. A key pasted without its trailing '=' padding
    (43 url-safe chars, which is how `SVETA_TOKEN_KEY` was entered in Railway on
    2026-09-03) is padded back; anything that does not decode to 32 bytes is
    refused with a clear message."""
    import base64
    raw = (config.TOKEN_KEY or "").strip()
    if not raw:
        raise EnvironmentError("SVETA_TOKEN_KEY is not set")
    padded = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode())
    except (ValueError, TypeError) as e:
        raise EnvironmentError(f"SVETA_TOKEN_KEY is not url-safe base64: {e}") from None
    if len(decoded) != 32:
        raise EnvironmentError(f"SVETA_TOKEN_KEY decodes to {len(decoded)} bytes, Fernet needs 32")
    return padded.encode()


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(_key_bytes())


def encrypt(text: str) -> bytes:
    return _fernet().encrypt(text.encode("utf-8"))


def decrypt(blob: bytes | memoryview) -> str:
    return _fernet().decrypt(bytes(blob)).decode("utf-8")


def validate() -> None:
    """Called at startup: a malformed SVETA_TOKEN_KEY must fail the boot with a
    clear message, not the first OAuth exchange days later."""
    try:
        _fernet()
    except Exception as e:  # noqa: BLE001
        raise EnvironmentError(f"SVETA_TOKEN_KEY is not a valid Fernet key: {e}") from None
