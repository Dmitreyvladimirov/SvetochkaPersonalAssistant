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
    # True = the tool has an outside effect and must not run without a tap. None of
    # the stage-1 tools do; the flag exists so the loop already refuses correctly
    # when calendar_create arrives in stage 3.
    needs_confirmation: bool = False


def _strict(schema: dict) -> dict:
    """Every tool schema is strict: no extra properties, everything required. The
    model then cannot pass a field the tool does not validate."""
    out = dict(schema)
    out.setdefault("type", "object")
    out["additionalProperties"] = False
    out["required"] = list(out.get("properties", {}).keys())
    return out


def definitions(tools: list[Tool]) -> list[dict]:
    """What the API receives."""
    return [{"name": t.name, "description": t.description,
             "input_schema": _strict(t.input_schema), "strict": True} for t in tools]


class UnknownTool(Exception):
    pass


def run(tools: list[Tool], name: str, scope: UserScope, ctx: ToolContext, args: dict) -> str:
    by_name = {t.name: t for t in tools}
    if name not in by_name:
        raise UnknownTool(name)
    return by_name[name].fn(scope, ctx, **args)


from sveta.tools import links, lists, notes, preferences, reminders, suggest  # noqa: E402

REGISTRY: list[Tool] = [
    notes.NOTE_SAVE,
    notes.NOTE_SEARCH,
    notes.NOTE_RECENT,
    links.LINK_SAVE,
    links.LINK_FETCH,
    lists.LIST_ADD,
    lists.LIST_SHOW,
    lists.LIST_CHECK,
    lists.LIST_MOVE,
    reminders.REMINDER_CREATE,
    reminders.REMINDER_LIST,
    reminders.REMINDER_CANCEL,
    preferences.PREFERENCE_SET,
    preferences.PREFERENCE_DELETE,
    preferences.MEMORY_SHOW,
    suggest.SUGGEST,
]
