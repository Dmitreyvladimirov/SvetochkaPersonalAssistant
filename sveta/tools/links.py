"""Links (FR-11, FR-12). Every fetch leaves a `links` row; a note appears only when
the page answered (a 404 is recorded, not filed). The bare-URL cheap path in
bot.py calls save() directly, so a pasted link never costs a model call (FR-6)."""
from sveta.core import db, fetch
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool
from sveta.tools.notes import source_for_url


def save(scope: UserScope, ctx: ToolContext, url: str, comment: str = "") -> tuple[str, int | None]:
    """(message for the model / the user, note_id or None)."""
    url, comment = (url or "").strip(), (comment or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "Error: url must start with http:// or https://.", None
    result = fetch.get(url)
    db.create_link(scope.user_id, url, inbox_item_id=ctx.inbox_item_id, final_url=result.final_url,
                   http_status=result.status, title=result.title, summary=result.summary,
                   fetch_error=result.error)
    if not result.ok:
        why = result.error or f"HTTP {result.status}"
        return f"Link recorded but NOT saved as a note: could not fetch it ({why}). Tell the user.", None
    body_parts = [p for p in (comment, result.title) if p]
    body = " — ".join(body_parts) if body_parts else url
    if result.summary:
        body += f"\n{result.summary}"
    body += f"\n{url}"
    note_id = db.create_note(scope.user_id, body, title=result.title, source=source_for_url(url),
                             source_ref=url, inbox_item_id=ctx.inbox_item_id)
    ctx.created_note_ids.append(note_id)
    summary = f" — {result.summary[:160]}" if result.summary else ""
    return f"Saved note #{note_id}: {result.title or url}{summary}", note_id


def _save(scope: UserScope, ctx: ToolContext, url: str, comment: str) -> str:
    return save(scope, ctx, url, comment)[0]


LINK_SAVE = Tool(
    name="link_save",
    description=("Save a URL: fetch its title and summary and file it as a note with the source by "
                 "domain. Use when the message contains a link, with any comment the user added. "
                 "Prefer this over note_save for anything with a URL."),
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL from the message."},
            "comment": {"type": "string", "description": "The user's own words about the link, or empty string."},
        },
    },
    fn=_save,
)


def _fetch(scope: UserScope, ctx: ToolContext, url: str) -> str:
    url = (url or "").strip()
    result = fetch.get(url)
    if not result.ok:
        return f"Could not fetch {url}: {result.error or f'HTTP {result.status}'}."
    return f"Title: {result.title or '—'}\nSummary: {result.summary or '— (the page has no description)'}"


LINK_FETCH = Tool(
    name="link_fetch",
    description="Read a page's title and summary without saving anything: 'что по этой ссылке?', 'о чём статья'.",
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The URL to look at."}},
    },
    fn=_fetch,
)
