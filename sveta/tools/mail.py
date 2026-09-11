"""Mail (FR-27, FR-28, FR-44). mail_search shows the model subject, sender, date and
snippet only; bodies are stored encrypted and reach the model through
mail_read_body, which is gated behind a card (§6.2, §8). When a hit looks like a
ticket or a booking, the trip playbook is appended to the result (FR-44)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sveta import playbooks
from sveta.core import crypto, db, google
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool


def _fmt(m: dict, tz: str) -> str:
    when = m["received_at"].astimezone(ZoneInfo(tz)).strftime("%d.%m %H:%M") if m.get("received_at") else "—"
    return f"- [{m['gmail_id']}] {when} · {m.get('sender') or '?'} · {m.get('subject') or '(без темы)'}\n  {(m.get('snippet') or '')[:200]}"


def _search(scope: UserScope, ctx: ToolContext, query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "Error: query is empty."
    try:
        hits = google.search_mail(scope.user_id, query, limit=5)
    except google.NotConnected as e:
        return f"Error: {e}"
    except google.GoogleError as e:
        return f"Error: mail unavailable ({e}). Tell the user honestly."
    if not hits:
        return f"No mail matches '{query}'. Say so honestly; suggest other words."
    rows = []
    for h in hits:
        rows.append({"gmail_id": h["gmail_id"], "thread_id": h.get("thread_id"), "sender": h.get("sender"),
                     "subject": h.get("subject"), "snippet": h.get("snippet"),
                     "received_at": h.get("received_at"),
                     "body_enc": crypto.encrypt(h.get("body") or "") if h.get("body") else None})
    db.save_mail_messages(scope.user_id, rows)
    text = f"{len(hits)} message(s) (id in brackets; use mail_read_body for the full text):\n" + \
           "\n".join(_fmt(h, scope.tz) + f"\n  {h.get('link', '')}" for h in hits)
    tagged = " ".join(f"{h.get('subject', '')} {h.get('sender', '')}" for h in hits)
    return text + playbooks.attach(tagged)


MAIL_SEARCH = Tool(
    name="mail_search",
    description=("Search the user's Gmail. Pass a Gmail-style query built from the user's words "
                 "(e.g. 'билет Synergy', 'from:booking.com', 'посадочный talon newer_than:30d'). "
                 "Returns subject, sender, date and snippet — never the body. Use for 'найди "
                 "письмо/билет', 'когда у меня самолёт', 'что писал X'."),
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Gmail search query."}},
    },
    fn=_search,
)


def describe(scope: UserScope, args: dict) -> str:
    gmail_id = str(args.get("gmail_id", "")).strip()
    row = db.get_mail_message(scope.user_id, gmail_id) if gmail_id else None
    if not row:
        return "Error: unknown message id; call mail_search first."
    return f"Прочитать письмо: {row.get('subject') or '(без темы)'} — {row.get('sender') or '?'}"


def execute(scope: UserScope, args: dict) -> str:
    """Runs from the tap only: decrypts the body for one agent turn. The text is
    returned to the caller, never logged."""
    gmail_id = str(args.get("gmail_id", "")).strip()
    row = db.get_mail_message(scope.user_id, gmail_id)
    if not row:
        return "Не нашла это письмо."
    if not row.get("body_enc"):
        return f"У письма «{row.get('subject')}» нет текста, только заголовки."
    try:
        body = crypto.decrypt(row["body_enc"])
    except Exception:  # noqa: BLE001
        return "Не смогла расшифровать письмо (ключ не подходит)."
    return (f"Письмо «{row.get('subject')}» от {row.get('sender')}:\n"
            f"{body[:6000]}")


MAIL_READ_BODY = Tool(
    name="mail_read_body",
    description=("Read the full text of one message found by mail_search — ONLY when the user "
                 "explicitly asks for details the snippet does not have. The text is shown only "
                 "after the user taps a confirm button."),
    input_schema={
        "type": "object",
        "properties": {"gmail_id": {"type": "string", "description": "The id in brackets from mail_search."}},
    },
    fn=lambda scope, ctx, **kw: "Error: mail_read_body runs only from the confirm button.",
    needs_confirmation=True,
    describe=describe,
)
