"""Preferences and what Svetochka remembers about the user. FR-58, §6.3, §6.4.

Persona knobs are preferences with a `persona.` prefix; the system prompt reads them
back on every message, so "будь короче" changes the next reply without a deploy.
"""
from sveta.core import db
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool

PERSONA_KNOBS = {
    "persona.address": "how to address the user",
    "persona.warmth": "0 dry … 3 close friend",
    "persona.brevity": "0 expansive … 3 telegraphic",
    "persona.emoji": "none | rare | frequent",
    "persona.proactivity": "how many suggestions under a reply, 0–3",
    "persona.greeting": "yes | no — greet in the brief",
}


def _set(scope: UserScope, ctx: ToolContext, key: str, value: str) -> str:
    key, value = (key or "").strip(), (value or "").strip()
    if not key or not value:
        return "Error: key and value are both required."
    db.set_preference(scope.user_id, key, value)
    return f"Remembered: {key} = {value}."


PREFERENCE_SET = Tool(
    name="preference_set",
    description=("Remember a standing preference or habit of the user: 'будь короче' → "
                 "persona.brevity=3; 'бриф в 8' → brief.time=08:00; 'покупки — это список' → "
                 "filing.shopping=list. Persona knobs: " + "; ".join(f"{k} ({v})" for k, v in PERSONA_KNOBS.items())),
    input_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Dotted key, e.g. persona.brevity, brief.time."},
            "value": {"type": "string", "description": "The value as a short string."},
        },
    },
    fn=_set,
)


def _delete(scope: UserScope, ctx: ToolContext, key: str) -> str:
    if db.delete_preference(scope.user_id, (key or "").strip()):
        return f"Forgot {key}."
    return f"Nothing stored under {key}."


PREFERENCE_DELETE = Tool(
    name="preference_delete",
    description="Forget a preference: 'забудь про …', 'не надо больше …'.",
    input_schema={"type": "object",
                  "properties": {"key": {"type": "string", "description": "The key to forget."}}},
    fn=_delete,
)


def _memory_show(scope: UserScope, ctx: ToolContext) -> str:
    prefs = db.list_preferences(scope.user_id)
    corrections = db.recent_corrections(scope.user_id)
    lines = []
    if prefs:
        lines.append("Preferences:")
        lines += [f"  {k} = {v}" for k, v in prefs.items()]
    else:
        lines.append("No preferences stored yet.")
    if corrections:
        lines.append("Recent corrections (did → should have):")
        lines += [f"  {c['did']} → {c.get('should_have') or '?'}" for c in corrections]
    facts = db.open_facts(scope.user_id)
    if facts:
        lines.append("Facts (current):")
        lines += [f"  {f['subject']} — {f['predicate']} — {f['object']}" for f in facts]
    return "\n".join(lines)


MEMORY_SHOW = Tool(
    name="memory_show",
    description="Everything Svetochka remembers about the user: preferences, current facts and recent corrections. Use for 'что ты про меня помнишь?'.",
    input_schema={"type": "object", "properties": {}},
    fn=_memory_show,
)
