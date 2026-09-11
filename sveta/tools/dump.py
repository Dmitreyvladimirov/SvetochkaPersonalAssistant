"""Splitting a dump into records (FR-13). Three or more separate thoughts in one
message → the model proposes the split, the bot shows it with one confirm
button, and nothing is written until the tap. Nothing is lost: the proposal is
kept on the inbox row, so the button works later too."""
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool

MIN_ITEMS, MAX_ITEMS = 2, 20


def _propose(scope: UserScope, ctx: ToolContext, items: list) -> str:
    kept = []
    for it in items or []:
        body = str((it or {}).get("body", "")).strip()
        project = str((it or {}).get("project", "")).strip()
        if body:
            kept.append({"kind": "note", "body": body[:2000], "project": project or None})
    if len(kept) < MIN_ITEMS:
        return f"Error: a proposal needs at least {MIN_ITEMS} records; for one thought use note_save."
    kept = (ctx.proposal + kept)[:MAX_ITEMS]   # a second call in one turn adds, never overwrites
    ctx.proposal = kept
    listing = "\n".join(f"{n}. {it['body']}" + (f" ({it['project']})" if it["project"] else "")
                        for n, it in enumerate(kept, 1))
    return (f"{len(kept)} records proposed; a 'Сохранить все' button will appear under your reply. "
            f"Do NOT save them yourself. List them for the user:\n{listing}")


NOTES_PROPOSE = Tool(
    name="notes_propose",
    description=("For a message that holds three or more separate thoughts: propose the split as "
                 "records (each in the user's own words, nothing dropped) instead of saving them. "
                 "The user confirms with one tap. For one or two thoughts call note_save directly."),
    input_schema={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "body": {"type": "string", "description": "One thought, in the user's words."},
                        "project": {"type": "string", "description": "Project if named, else empty string."},
                    },
                    "required": ["body", "project"],
                    "additionalProperties": False,
                },
            },
        },
    },
    fn=_propose,
)
