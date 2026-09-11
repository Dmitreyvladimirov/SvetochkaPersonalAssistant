"""Facts with a validity window (FR-45, SPEC.md §6.3). "Света сменила врача" closes
the old fact and opens a new one; nothing is erased, and the agent answers with
what is current. Retrieval is by topic, so only the relevant facts ever reach
the prompt."""
from sveta.core import db
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool


def _fmt(f: dict) -> str:
    line = f"#{f['id']} {f['subject']} — {f['predicate']} — {f['object']}"
    if f.get("valid_to"):
        line += f" (до {f['valid_to']:%d.%m.%Y})"
    return line


def _remember(scope: UserScope, ctx: ToolContext, subject: str, predicate: str, object: str) -> str:
    subject, predicate, object = (subject or "").strip(), (predicate or "").strip(), (object or "").strip()
    if not (subject and predicate and object):
        return "Error: subject, predicate and object are all required."
    fact_id, closed = db.remember_fact(scope.user_id, subject, predicate, object)
    tail = f"; closed {closed} older fact(s) about the same thing" if closed else ""
    return f"Remembered fact #{fact_id}: {subject} — {predicate} — {object}{tail}."


FACT_REMEMBER = Tool(
    name="fact_remember",
    description=("Remember a fact about a person, place or thing as subject — predicate — object: "
                 "'Костя отвечает за инфру' → ('Костя', 'отвечает за', 'инфру'); 'врач Светы — Иванова' "
                 "→ ('Света', 'врач', 'Иванова'). A newer fact with the same subject and predicate "
                 "replaces the old one (the old one is kept with an end date). Use for 'запомни, что…' "
                 "statements about who/what/where; use note_save for thoughts and ideas."),
    input_schema={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Who or what the fact is about."},
            "predicate": {"type": "string", "description": "The relation, short: 'отвечает за', 'врач', 'живёт в', 'день рождения'."},
            "object": {"type": "string", "description": "The value."},
        },
    },
    fn=_remember,
)


def _recall(scope: UserScope, ctx: ToolContext, topic: str, include_history: bool) -> str:
    topic = (topic or "").strip()
    if not topic:
        return "Error: topic is empty."
    rows = db.recall_facts(scope.user_id, topic, include_closed=bool(include_history))
    if not rows:
        return f"No facts about '{topic}'. Say so honestly; do not invent."
    return f"{len(rows)} fact(s) about '{topic}':\n" + "\n".join(_fmt(r) for r in rows)


FACT_RECALL = Tool(
    name="fact_recall",
    description=("What is known about a person, place or thing. Call it BEFORE answering a question "
                 "about someone or something ('кто отвечает за инфру', 'какой у Светы врач'). "
                 "include_history=true also returns superseded facts with their end dates."),
    input_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "A name or a word from the subject or the value."},
            "include_history": {"type": "boolean", "description": "true to include facts that are no longer current."},
        },
    },
    fn=_recall,
)
