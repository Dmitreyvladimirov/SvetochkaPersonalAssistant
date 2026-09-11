"""One-shot OAuth `state` values (Google and Notion share the mechanism).

A state is `<user_id>.<nonce>.<hmac>`: the HMAC (token key over provider, user
and nonce) makes it unforgeable, the nonce — stored as a preference with a
10-minute expiry and deleted on first use — makes it single-use and short-lived.
Without the nonce, the URL button that stays in the chat forever would let
anyone who sees it attach their own account to the user's row."""
import hashlib
import hmac
import secrets
import time

from sveta.core import config, db

TTL_SECONDS = 600


def _key() -> bytes:
    """A key derived from the token key with a label: the Fernet key itself is
    never used as an HMAC key."""
    return hashlib.sha256(b"svetochka-oauth-state:" + config.TOKEN_KEY.encode()).digest()


def _mac(provider: str, user_id: int, nonce: str) -> str:
    return hmac.new(_key(), f"{provider}:{user_id}:{nonce}".encode(), hashlib.sha256).hexdigest()[:32]


def issue(provider: str, user_id: int) -> str:
    nonce = secrets.token_hex(8)
    db.set_preference(user_id, f"oauth.nonce.{provider}", f"{nonce}:{int(time.time()) + TTL_SECONDS}",
                      set_via="oauth")
    return f"{user_id}.{nonce}.{_mac(provider, user_id, nonce)}"


def consume(provider: str, state: str) -> int | None:
    """The user id if the state is genuine, unexpired and unused; None otherwise.
    A genuine state is consumed even when it is expired, so it cannot be retried."""
    parts = (state or "").split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        return None
    user_id, nonce, mac = int(parts[0]), parts[1], parts[2]
    if not hmac.compare_digest(mac, _mac(provider, user_id, nonce)):
        return None
    key = f"oauth.nonce.{provider}"
    stored = db.list_preferences(user_id).get(key, "")
    db.delete_preference(user_id, key)
    stored_nonce, _, expiry = stored.partition(":")
    if stored_nonce != nonce or not expiry.isdigit() or int(expiry) < time.time():
        return None
    return user_id
