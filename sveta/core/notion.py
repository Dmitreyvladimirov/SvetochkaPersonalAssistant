"""The Notion showcase (FR-14): a copy of each note as a page in a database the
user chose. Truth stays in Postgres; a Notion failure is a log line and
`notion_page_id` stays NULL.

Access is OAuth per user (a public integration; the user picks the pages on
Notion's consent screen and the token lands encrypted in oauth_tokens), with
NOTION_TOKEN as a deployment-wide fallback. The target database is a per-user
preference, set automatically when the consent shared exactly one database."""
import base64
import hashlib
import hmac
import logging
from datetime import datetime
from urllib.parse import urlencode

from sveta.core import config, crypto, db, oauth_state

logger = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
TIMEOUT = 15.0


class NotionError(Exception):
    pass


def _post(path: str, payload: dict, token: str) -> tuple[int, dict]:
    """Patched in tests."""
    import httpx
    r = httpx.post(f"{API}{path}", json=payload, timeout=TIMEOUT,
                   headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION,
                            "Content-Type": "application/json"})
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:200]}


def _get(path: str, token: str) -> tuple[int, dict]:
    import httpx
    r = httpx.get(f"{API}{path}", timeout=TIMEOUT,
                  headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION})
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:200]}


def _post_basic(path: str, payload: dict) -> tuple[int, dict]:
    """The token exchange authenticates with the client credentials. Patched in tests."""
    import httpx
    basic = base64.b64encode(f"{config.NOTION_CLIENT_ID}:{config.NOTION_CLIENT_SECRET}".encode()).decode()
    r = httpx.post(f"{API}{path}", json=payload, timeout=TIMEOUT,
                   headers={"Authorization": f"Basic {basic}", "Notion-Version": VERSION,
                            "Content-Type": "application/json"})
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:200]}


# --- OAuth (a public integration) -----------------------------------------------

def oauth_configured() -> bool:
    return bool(config.NOTION_CLIENT_ID and config.NOTION_CLIENT_SECRET)


def configured() -> bool:
    """Any way to reach Notion: OAuth per user, or the fallback token."""
    return oauth_configured() or bool(config.NOTION_TOKEN)


def redirect_uri() -> str:
    return f"https://{config.PUBLIC_DOMAIN}/oauth/notion/callback"


def state_for(user_id: int) -> str:
    return oauth_state.issue("notion", user_id)


def user_from_state(state: str) -> int | None:
    return oauth_state.consume("notion", state)


def auth_url(user_id: int) -> str:
    params = {"client_id": config.NOTION_CLIENT_ID, "response_type": "code", "owner": "user",
              "redirect_uri": redirect_uri(), "state": state_for(user_id)}
    return f"https://api.notion.com/v1/oauth/authorize?{urlencode(params)}"


def exchange_code(user_id: int, code: str) -> str:
    """Code → access token (Notion tokens do not expire) → encrypted row. Returns
    the workspace name."""
    status, body = _post_basic("/oauth/token", {"grant_type": "authorization_code", "code": code,
                                                "redirect_uri": redirect_uri()})
    if status != 200 or "access_token" not in body:
        raise NotionError(f"token exchange failed: HTTP {status} {body.get('error', '')}")
    workspace = body.get("workspace_name") or body.get("workspace_id") or "workspace"
    db.save_oauth_token(user_id, "notion", workspace, crypto.encrypt(body["access_token"]),
                        scopes="pages")
    return workspace


def token_for(user_id: int) -> str | None:
    row = db.get_oauth_token(user_id, "notion")
    if row:
        return crypto.decrypt(row["refresh_token"])
    return config.NOTION_TOKEN or None


def shared_databases(token: str) -> list[dict]:
    """The databases the consent screen shared: [{id, title}]."""
    status, body = _post("/search", {"filter": {"property": "object", "value": "database"},
                                     "page_size": 20}, token)
    if status != 200:
        raise NotionError(f"search: HTTP {status}")
    out = []
    for r in body.get("results", []):
        title = "".join(t.get("plain_text", "") for t in r.get("title", [])) or "(без названия)"
        out.append({"id": r["id"], "title": title})
    return out


def database_id_from_link(link: str) -> str | None:
    """A 32-hex id from a Notion URL or a bare id, formatted with dashes."""
    import re
    m = re.search(r"([0-9a-f]{32})(?![0-9a-f])", (link or "").replace("-", "").lower())
    if not m:
        return None
    h = m.group(1)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def check_database(database_id: str, token: str) -> str:
    """The database's title, or a NotionError naming what is wrong (not shared
    with the integration, wrong id, bad token)."""
    status, body = _get(f"/databases/{database_id}", token)
    if status == 200:
        title = "".join(t.get("plain_text", "") for t in body.get("title", [])) or "(без названия)"
        return title
    if status in (401, 403):
        raise NotionError("токен не принят (проверь NOTION_TOKEN)")
    if status == 404:
        raise NotionError("база не найдена или не расшарена интеграции Svetochka (… → Connections)")
    raise NotionError(f"HTTP {status}")


def _rich(text: str) -> list[dict]:
    text = (text or "")[:1900]
    return [{"type": "text", "text": {"content": text}}] if text else []


def create_note_page(database_id: str, token: str, *, note_id: int, title: str, body: str,
                     project: str | None, source: str, url: str | None,
                     created_at: datetime | None) -> str:
    """Returns the page id. Property names match the database created on
    2026-09-12 ("Светочка · Заметки"); a database without a property is fine —
    Notion ignores unknown ones only if we do not send them, so we probe once."""
    props = {
        "Name": {"title": _rich(title or body[:80] or f"note {note_id}")},
        "Body": {"rich_text": _rich(body)},
        "Source": {"select": {"name": source or "telegram"}},
        "Note ID": {"number": note_id},
    }
    if project:
        props["Project"] = {"rich_text": _rich(project)}
    if url:
        props["URL"] = {"url": url[:2000]}
    if created_at:
        props["Created"] = {"date": {"start": created_at.isoformat()}}
    status, resp = _post("/pages", {"parent": {"database_id": database_id}, "properties": props}, token)
    if status != 200:
        msg = resp.get("message", "") if isinstance(resp, dict) else ""
        raise NotionError(f"HTTP {status} {msg[:160]}")
    return resp["id"]
