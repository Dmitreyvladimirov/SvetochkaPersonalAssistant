"""Notes: save, search, recent. FR-9, FR-10, FR-46."""
from urllib.parse import urlparse

from sveta.core import db
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool

# Domains that identify a source for FR-46/FR-53. Anything else is 'web'.
_SOURCE_BY_HOST = {
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "t.me": "telegram",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
}


def source_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for suffix, source in _SOURCE_BY_HOST.items():
        if host == suffix or host.endswith("." + suffix):
            return source
    return "web"


def _save(scope: UserScope, ctx: ToolContext, body: str, project: str, url: str) -> str:
    body = (body or "").strip()
    if not body and not url:
        return "Error: nothing to save — body is empty."
    source, source_ref = "telegram", None
    if url:
        source, source_ref = source_for_url(url), url
        body = body or url
    note_id = db.create_note(scope.user_id, body, project=project or None,
                             source=source, source_ref=source_ref,
                             inbox_item_id=ctx.inbox_item_id)
    ctx.created_note_ids.append(note_id)
    where = f" (project: {project})" if project else ""
    return f"Saved note #{note_id}{where}, source={source}."


NOTE_SAVE = Tool(
    name="note_save",
    description=("Save a thought, idea, fact or link as a note. Use for anything the user "
                 "wants remembered. Pass the user's own wording as body; do not rewrite it. "
                 "If the message is or contains a URL, pass it as url so the source is recorded."),
    input_schema={
        "type": "object",
        "properties": {
            "body": {"type": "string", "description": "The note text in the user's words. May be empty only when url is given."},
            "project": {"type": "string", "description": "Project name if the user named one explicitly, else empty string."},
            "url": {"type": "string", "description": "A URL contained in the message, else empty string."},
        },
    },
    fn=_save,
)


def _fmt(rows: list[dict], user_id: int | None = None) -> str:
    from sveta.core import db as _db
    with_files = _db.notes_with_files(user_id, [r["id"] for r in rows]) if user_id else set()
    lines = []
    for r in rows:
        when = r["created_at"].strftime("%d.%m") if r.get("created_at") else "—"
        body = (r.get("body") or "").replace("\n", " ")
        src = f" [{r['source']}]" if r.get("source") and r["source"] != "telegram" else ""
        proj = f" ({r['project']})" if r.get("project") else ""
        clip = " 📎" if r["id"] in with_files else ""
        lines.append(f"#{r['id']} {when}{proj}{src}{clip}: {body[:200]}")
    return "\n".join(lines)


def _search(scope: UserScope, ctx: ToolContext, query: str, source: str) -> str:
    query = (query or "").strip()
    if not query:
        return "Error: query is empty."
    rows = db.search_notes(scope.user_id, query, source=source or None)
    broadened = False
    if not rows and len(query.split()) > 1:
        # All the words, then any of them: a paraphrase shares the idea, not the
        # phrasing, and one extra word used to be enough to find nothing (FR-62).
        rows = db.search_notes(scope.user_id, query, source=source or None, broad=True)
        broadened = bool(rows)
    if not rows:
        return ("No notes match '" + query + "', neither all the words together nor any of them"
                + (f" (source={source})" if source else "") + ". Say so plainly. Do NOT ask the "
                "user to guess other words for you — note_recent shows what is there.")
    head = f"{len(rows)} note(s)"
    if broadened:
        head += " (no note has all of those words; these have some of them, so check they are the right ones)"
    # FR-65: "пришли её файлом" has to resolve to the note just shown.
    for r in rows[:3]:
        ctx.found(f"заметка #{r['id']}: {(r.get('title') or r['body'])[:60]}")
    return head + ":\n" + _fmt(rows, scope.user_id)


NOTE_SEARCH = Tool(
    name="note_search",
    description=("Full-text search over the user's own notes. Use for 'что я писал про X', "
                 "'что я сохранял про X'. Returns id, date, project, source and text."),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search phrase in the user's language."},
            "source": {"type": "string", "description": "Restrict to one source (instagram, linkedin, web, telegram) or empty string for all."},
        },
    },
    fn=_search,
)


def _recent(scope: UserScope, ctx: ToolContext, project: str, limit: int) -> str:
    rows = db.recent_notes(scope.user_id, limit=max(1, min(limit, 20)), project=project or None)
    if not rows:
        return "No notes yet."
    return f"{len(rows)} most recent note(s):\n" + _fmt(rows, scope.user_id)


NOTE_RECENT = Tool(
    name="note_recent",
    description=("The user's most recent notes, optionally for one project. Use for "
                 "'что у меня по проекту X', 'что я записывал на этой неделе'."),
    input_schema={
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project name or empty string."},
            "limit": {"type": "integer", "description": "How many, 1–20."},
        },
    },
    fn=_recent,
)


def _send_file(scope: UserScope, ctx: ToolContext, note_id: int) -> str:
    """FR-61: the file a note came from, back into the user's own chat. Telegram
    keeps the bytes; the stored file_id is enough to send them again."""
    from sveta.core import db as _db, telegram
    row = _db.note_file(scope.user_id, int(note_id))
    if not row:
        return f"Note #{note_id} has no file attached (or is not this user's)."
    name = row.get("file_name") or "файл"
    message_id = telegram.send_document(scope.chat_id, name, row["file_id"],
                                        caption=f"📎 {name}")
    if message_id is None:
        return f"Error: Telegram did not accept the file of note #{note_id}."
    return f"Sent the file of note #{note_id} ({name}) to the chat. Do not describe it; say it is above."


NOTE_SEND_FILE = Tool(
    name="note_send_file",
    description=("Send the photo or document a note came from back into the chat — for "
                 "'пришли тот чек', 'скинь скан посадочного'. Find the note with note_search "
                 "first; notes that carry a file are marked 📎 in the results."),
    input_schema={
        "type": "object",
        "properties": {"note_id": {"type": "integer", "description": "The note id from note_search or note_recent."}},
    },
    fn=_send_file,
)
