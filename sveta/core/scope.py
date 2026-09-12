"""UserScope — the only way a tool can address data (SPEC.md §6.1, NFR-10).

The agent loop builds one from the incoming item and passes it to every tool call.
A tool never sees a raw chat_id and has no other handle on the database, so it
physically cannot query another user's rows. Isolation by construction, not by
discipline; in the single-user case it costs nothing.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class UserScope:
    user_id: int
    chat_id: str
    tz: str
    preferences: dict[str, str] = field(default_factory=dict)

    def pref(self, key: str, default: str = "") -> str:
        return self.preferences.get(key, default)


# A pointer back, not a transcript: enough to name what was found, never enough
# to replace looking at it again.
MAX_REFS = 6
MAX_REF_CHARS = 160


@dataclass
class ToolContext:
    """Side channels a tool may write to during one message: what it created (so
    the reply can carry an undo button) and what it proposes (FR-43 suggestions).
    Nothing here reaches the database by itself."""
    inbox_item_id: int | None = None
    created_note_ids: list[int] = field(default_factory=list)
    created_reminder_ids: list[int] = field(default_factory=list)
    created_list_item_ids: list[int] = field(default_factory=list)
    # Set by a list tool when the reply should carry the checkbox keyboard (FR-49).
    render_list_id: int | None = None
    suggestions: list[dict] = field(default_factory=list)
    # FR-13: records proposed for one-tap confirmation; nothing is written until then.
    proposal: list[dict] = field(default_factory=list)
    # §6.2: tool calls with an outside effect, recorded instead of run. Each is
    # {"kind": "confirm", "tool": name, "args": {...}, "label": text}.
    pending: list[dict] = field(default_factory=list)
    # FR-65: what this exchange found, in a line a later message can build on —
    # "письмо [m-col] Air Europa", "рейсы UX1301, UX0091". A tool writes the short
    # form, never the whole result: this is a pointer back, not a cache, and it is
    # re-sent with every step of every following message.
    refs: list[str] = field(default_factory=list)

    def found(self, what: str) -> None:
        """Record a reference, trimmed and deduplicated; at most MAX_REFS survive."""
        what = " ".join((what or "").split())[:MAX_REF_CHARS]
        if what and what not in self.refs and len(self.refs) < MAX_REFS:
            self.refs.append(what)
