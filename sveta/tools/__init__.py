"""The closed set of tools (SPEC.md §6.1, FR-5).

A tool is a Python function with a JSON schema. The model can call only what is
registered here; a call to any other name is answered with an error result, never
with an action. Each tool receives a UserScope and a ToolContext and returns a plain
string the model reads back.

Adding a tool = adding a file in this package and one line in REGISTRY (NFR-9).
"""
from dataclasses import dataclass
from typing import Callable

from sveta.core.scope import ToolContext, UserScope


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., str]
    # True = the tool has an outside effect and must not run without a tap (§6.2).
    # The loop then calls `describe(scope, args)` for the card label (a string
    # starting with "Error" refuses the call) and stores the call for the button.
    needs_confirmation: bool = False
    describe: Callable[..., str] | None = None


def _strict(schema: dict) -> dict:
    """Every tool schema is strict: no extra properties, everything required. The
    model then cannot pass a field the tool does not validate."""
    out = dict(schema)
    out.setdefault("type", "object")
    out["additionalProperties"] = False
    out["required"] = list(out.get("properties", {}).keys())
    return out


# The API compiles strict schemas into one grammar and refuses the request with
# 400 "Schema is too complex" past a budget that, measured on 2026-09-12, is seven
# tools of this shape. Strict therefore goes to the tools that WRITE — a malformed
# argument there is a wrong record, not a wrong answer. Every other tool still
# gets additionalProperties=false and required in its schema (the model rarely
# strays) and validates its arguments in Python (a TypeError comes back to the
# model as text, never as an action).
# The budget shrinks as the tool set grows: measured at seven strict tools with
# 19 tools (2026-09-12 07:30 UTC) and six with 25 tools (12:40 UTC). Order of
# keeping: outside effects and dated writes first; a fact with a malformed
# argument is a wrong memory, not a wrong event. The golden run is the alarm
# when the budget moves again (400 "Schema is too complex" on the first call).
STRICT_TOOLS = frozenset({"reminder_create", "calendar_create", "list_add", "list_check",
                          "link_save", "note_save"})


def definitions(tools: list[Tool]) -> list[dict]:
    """What the API receives."""
    return [{"name": t.name, "description": t.description,
             "input_schema": _strict(t.input_schema), "strict": t.name in STRICT_TOOLS}
            for t in tools]


class UnknownTool(Exception):
    pass


def run(tools: list[Tool], name: str, scope: UserScope, ctx: ToolContext, args: dict) -> str:
    by_name = {t.name: t for t in tools}
    if name not in by_name:
        raise UnknownTool(name)
    return by_name[name].fn(scope, ctx, **args)


from sveta.tools import calendar, dump, facts, links, lists, mail, notes, preferences, reminders, suggest  # noqa: E402

REGISTRY: list[Tool] = [
    notes.NOTE_SAVE,
    notes.NOTE_SEARCH,
    notes.NOTE_RECENT,
    notes.NOTE_SEND_FILE,
    dump.NOTES_PROPOSE,
    facts.FACT_REMEMBER,
    facts.FACT_RECALL,
    links.LINK_SAVE,
    links.LINK_FETCH,
    lists.LIST_ADD,
    lists.LIST_SHOW,
    lists.LIST_CHECK,
    lists.LIST_MOVE,
    reminders.REMINDER_CREATE,
    reminders.REMINDER_LIST,
    reminders.REMINDER_CANCEL,
    calendar.CALENDAR_QUERY,
    calendar.CALENDAR_CREATE,
    mail.MAIL_SEARCH,
    mail.MAIL_READ_BODY,
    mail.MAIL_SEND_ATTACHMENT,
    mail.MAIL_EXTRACT_TRIP,
    preferences.PREFERENCE_SET,
    preferences.PREFERENCE_DELETE,
    preferences.MEMORY_SHOW,
    suggest.SUGGEST,
]
