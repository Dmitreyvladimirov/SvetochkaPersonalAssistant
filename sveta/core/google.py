"""Google OAuth, Calendar and Gmail over plain REST (stage 3). No Google SDK: three
endpoints and a token refresh are less code than the client library's setup.

Tokens: the refresh token is stored encrypted in `oauth_tokens` (one row per
user, provider and account); an access token is minted per call and never
stored. A 401/400 on refresh marks the row with last_error so the chat can say
"переавторизуйся" (FR-39) instead of failing silently.

Scopes are the minimum §8 allows: calendar.events and gmail.readonly."""
import base64
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from sveta.core import config, crypto, db, oauth_state

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CALENDAR_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GMAIL_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = ["https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/userinfo.email"]
TIMEOUT = 20.0


class GoogleError(Exception):
    pass


class NotConnected(GoogleError):
    """No usable token for this user: the reply says /google."""


# --- HTTP (patched in tests) ---------------------------------------------------

def _post(url: str, *, data: dict | None = None, json: dict | None = None,
          headers: dict | None = None) -> tuple[int, dict]:
    import httpx
    r = httpx.post(url, data=data, json=json, headers=headers or {}, timeout=TIMEOUT)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:200]}
    return r.status_code, body


def _get(url: str, *, params: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    import httpx
    r = httpx.get(url, params=params, headers=headers or {}, timeout=TIMEOUT)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:200]}
    return r.status_code, body


# --- OAuth ---------------------------------------------------------------------

def configured() -> bool:
    return bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET)


def redirect_uri() -> str:
    return f"https://{config.PUBLIC_DOMAIN}/oauth/google/callback"


def state_for(user_id: int) -> str:
    """`state` binds the callback to the user who asked AND to one consent link:
    an HMAC over user id + a fresh nonce; the nonce is stored with a 10-minute
    expiry and consumed on first use, so a link left in the chat is not a
    permanent capability (review I4)."""
    return oauth_state.issue("google", user_id)


def user_from_state(state: str) -> int | None:
    return oauth_state.consume("google", state)


def auth_url(user_id: int) -> str:
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",          # a refresh token comes only with consent
        "include_granted_scopes": "true",
        "state": state_for(user_id),
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(user_id: int, code: str) -> str:
    """Code → refresh token → encrypted row. Returns the account email."""
    status, body = _post(TOKEN_URL, data={
        "code": code, "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
    })
    if status != 200 or "refresh_token" not in body:
        raise GoogleError(f"token exchange failed: HTTP {status} {body.get('error', '')}")
    access = body["access_token"]
    status, info = _get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
    email = (info.get("email") if status == 200 else None) or "unknown"
    granted = body.get("scope", " ".join(SCOPES))
    db.save_oauth_token(user_id, "google", email, crypto.encrypt(body["refresh_token"]), scopes=granted)
    return email


def missing_scopes(user_id: int) -> list[str]:
    """Scopes the consent screen let the user untick: named in the chat instead of
    a later 403 (review, QA)."""
    row = db.get_oauth_token(user_id, "google")
    granted = set((row or {}).get("scopes", "").split())
    names = {"https://www.googleapis.com/auth/calendar.events": "календарь",
             "https://www.googleapis.com/auth/gmail.readonly": "почта"}
    return [label for scope, label in names.items() if granted and scope not in granted]


def access_token(user_id: int) -> str:
    """Mint an access token from the stored refresh token. Raises NotConnected
    when there is no row or the refresh is refused (the row gets last_error)."""
    row = db.get_oauth_token(user_id, "google")
    if not row:
        raise NotConnected("Google не подключён — /google")
    refresh = crypto.decrypt(row["refresh_token"])
    status, body = _post(TOKEN_URL, data={
        "client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh, "grant_type": "refresh_token",
    })
    if status != 200 or "access_token" not in body:
        error = f"refresh failed: HTTP {status} {body.get('error', '')}"
        db.mark_oauth_error(user_id, "google", error)
        raise NotConnected("Google отвалился (токен не обновляется) — переавторизуйся: /google")
    db.mark_oauth_error(user_id, "google", None)
    return body["access_token"]


# --- Calendar ------------------------------------------------------------------

def _parse_when(value: dict, zone) -> datetime | None:
    raw = value.get("dateTime") or value.get("date")
    if not raw:
        return None
    if len(raw) == 10:  # all-day
        return datetime.fromisoformat(raw).replace(tzinfo=zone)
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(zone)


def list_events(user_id: int, start: datetime, end: datetime, *, query: str = "",
                limit: int = 20) -> list[dict]:
    """Events in [start, end), ordered by start. Each: summary, start, end, link,
    all_day. Times are aware datetimes in the caller's tz (that of `start`)."""
    token = access_token(user_id)
    params = {"timeMin": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
              "timeMax": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
              "singleEvents": "true", "orderBy": "startTime", "maxResults": str(limit)}
    if query:
        params["q"] = query
    status, body = _get(CALENDAR_URL, params=params, headers={"Authorization": f"Bearer {token}"})
    if status != 200:
        raise GoogleError(f"calendar list: HTTP {status} {body.get('error', {}).get('message', '') if isinstance(body.get('error'), dict) else body.get('error', '')}")
    zone = start.tzinfo
    out = []
    for e in body.get("items", []):
        s, en = _parse_when(e.get("start", {}), zone), _parse_when(e.get("end", {}), zone)
        if not s:
            continue
        out.append({"id": e.get("id"), "summary": e.get("summary") or "(без названия)", "start": s,
                    "end": en, "link": e.get("htmlLink"), "all_day": "date" in e.get("start", {}),
                    "location": e.get("location")})
    return out


def create_event(user_id: int, title: str, start: datetime, end: datetime,
                 description: str = "") -> dict:
    token = access_token(user_id)
    payload = {"summary": title,
               "start": {"dateTime": start.isoformat(), "timeZone": str(start.tzinfo)},
               "end": {"dateTime": end.isoformat(), "timeZone": str(end.tzinfo)}}
    if description:
        payload["description"] = description
    status, body = _post(CALENDAR_URL, json=payload, headers={"Authorization": f"Bearer {token}"})
    if status not in (200, 201):
        raise GoogleError(f"calendar insert: HTTP {status}")
    return {"id": body.get("id"), "link": body.get("htmlLink")}


# --- Gmail ---------------------------------------------------------------------

def _header(headers: list[dict], name: str) -> str:
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _attachments(payload: dict) -> list[dict]:
    """Parts that carry a file: name, mime type, size, attachmentId (Gmail serves
    the bytes separately). Recursive over multipart parts."""
    out = []

    def walk(part):
        body = part.get("body") or {}
        if part.get("filename") and body.get("attachmentId"):
            out.append({"filename": part["filename"], "mime": part.get("mimeType", "application/octet-stream"),
                        "size": int(body.get("size") or 0), "attachment_id": body["attachmentId"]})
        for p in part.get("parts", []) or []:
            walk(p)
    walk(payload or {})
    return out


def _body_text(payload: dict) -> str:
    """text/plain first, then text/html stripped; recursive over parts."""
    from html import unescape
    import re
    texts, htmls = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mime.startswith("text/"):
            try:
                text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text = ""
            (texts if mime == "text/plain" else htmls).append(text)
        for p in part.get("parts", []) or []:
            walk(p)
    walk(payload or {})
    if texts:
        return "\n".join(texts)
    if htmls:
        return " ".join(unescape(re.sub(r"<[^>]+>", " ", "\n".join(htmls))).split())
    return ""


def search_mail(user_id: int, query: str, *, limit: int = 5) -> list[dict]:
    """Gmail search → headers, snippet and the full body for each hit. The caller
    stores the body encrypted and shows the model only the headers (§8)."""
    token = access_token(user_id)
    auth = {"Authorization": f"Bearer {token}"}
    status, body = _get(f"{GMAIL_URL}/messages", params={"q": query, "maxResults": str(limit)}, headers=auth)
    if status != 200:
        raise GoogleError(f"gmail search: HTTP {status}")
    out = []
    for m in body.get("messages", []) or []:
        status, msg = _get(f"{GMAIL_URL}/messages/{m['id']}", params={"format": "full"}, headers=auth)
        if status != 200:
            continue
        headers = (msg.get("payload") or {}).get("headers", [])
        received = None
        if msg.get("internalDate"):
            received = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
        out.append({"gmail_id": msg["id"], "thread_id": msg.get("threadId"),
                    "sender": _header(headers, "From"), "subject": _header(headers, "Subject"),
                    "snippet": msg.get("snippet", ""), "received_at": received,
                    "body": _body_text(msg.get("payload") or {}),
                    "attachments": _attachments(msg.get("payload") or {}),
                    "link": f"https://mail.google.com/mail/u/0/#all/{msg['id']}"})
    return out


MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024   # Telegram's sendDocument limit for bots is 50 MB; 20 is plenty


def message_attachments(user_id: int, gmail_id: str) -> list[dict]:
    """The attachment list of one message, fetched fresh (nothing about
    attachments is stored)."""
    token = access_token(user_id)
    status, msg = _get(f"{GMAIL_URL}/messages/{quote(gmail_id, safe='')}", params={"format": "full"},
                       headers={"Authorization": f"Bearer {token}"})
    if status != 200:
        raise GoogleError(f"gmail get: HTTP {status}")
    return _attachments(msg.get("payload") or {})


def download_attachment(user_id: int, gmail_id: str, attachment_id: str, *, size: int = 0) -> bytes:
    if size and size > MAX_ATTACHMENT_BYTES:
        raise GoogleError(f"attachment too large ({size // 1024 // 1024} MB)")
    token = access_token(user_id)
    status, body = _get(f"{GMAIL_URL}/messages/{quote(gmail_id, safe='')}/attachments/{quote(attachment_id, safe='')}",
                        headers={"Authorization": f"Bearer {token}"})
    if status != 200 or not body.get("data"):
        raise GoogleError(f"gmail attachment: HTTP {status}")
    data = body["data"]
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise GoogleError(f"attachment too large ({len(raw) // 1024 // 1024} MB)")
    return raw


def range_for(phrase: str, now: datetime, tz: str) -> tuple[datetime, datetime] | None:
    """A calendar range from a phrase, parsed by code: сегодня, завтра, на этой
    неделе, на следующей неделе, в четверг, 25 сентября, or empty = next 7 days."""
    from zoneinfo import ZoneInfo
    from sveta.core import timeparse
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
    text = (phrase or "").lower().replace("ё", "е").strip()
    if not text or text in ("ближайшие дни", "на неделю", "неделя"):
        return day0, day0 + timedelta(days=7)
    import re
    week = re.search(r"\bнедел", text)      # not "понедельник"
    if week and "следующ" in text:
        start = day0 + timedelta(days=7 - day0.weekday())
        return start, start + timedelta(days=7)
    if week:
        start = day0 - timedelta(days=day0.weekday())
        return start, start + timedelta(days=7)
    if "месяц" in text:
        return day0, day0 + timedelta(days=31)
    m = timeparse._WEEKDAY.search(" " + text)
    if m:   # "в четверг" on a Thursday is today for a calendar range, not next week
        ahead = (timeparse.WEEKDAYS[m.group(1)] - day0.weekday()) % 7
        start = day0 + timedelta(days=ahead)
        return start, start + timedelta(days=1)
    parsed = timeparse.parse(text, now=now, tz=tz)
    if parsed is None:
        return None
    start = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)
