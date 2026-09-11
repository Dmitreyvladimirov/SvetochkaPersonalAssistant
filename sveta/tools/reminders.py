"""Reminders (FR-19…FR-23). The model passes the user's time phrase verbatim; the
date is computed here by code (SPEC.md §6.1). A reminder is created silently and
the reply must echo the exact parsed time (§6.2) — the tool's return string is
written so the model has nothing to compute."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sveta.core import db, timeparse
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _create(scope: UserScope, ctx: ToolContext, text: str, when: str, tz: str = "") -> str:
    text, when = " ".join((text or "").split()), (when or "").strip()
    if not text:
        return "Error: text is empty — what should the reminder say?"
    if not when:
        return "Error: when is empty — ask the user for a time."
    # A reminder about a flight from Bogotá at 09:00 means 09:00 *there* (§6.1).
    zone = (tz or "").strip() or scope.tz
    try:
        ZoneInfo(zone)
    except Exception:  # noqa: BLE001
        return f"Error: unknown time zone {tz!r}; pass an IANA name like America/Bogota or empty string."
    now = _now()
    fire_at = timeparse.parse(when, now=now, tz=zone)
    if fire_at is None:
        return (f"Error: could not understand the time {when!r}. Ask the user to say it "
                "differently (e.g. 'завтра в 11', 'через 20 минут', 'в четверг вечером').")
    local_now = now.astimezone(ZoneInfo(scope.tz))
    if fire_at <= now.astimezone(ZoneInfo(zone)):
        return (f"Error: {timeparse.fmt(fire_at)} is already in the past "
                f"(now is {local_now:%H:%M} for you); nothing created. Tell the user.")
    dedup_key = f"{text.lower()}|{fire_at.astimezone(timezone.utc):%Y-%m-%dT%H:%M}"
    reminder_id, created = db.create_reminder(scope.user_id, text, fire_at, zone, dedup_key,
                                              inbox_item_id=ctx.inbox_item_id)
    label = timeparse.fmt(fire_at.astimezone(ZoneInfo(scope.tz)), local_now)
    if zone != scope.tz:
        label += f" по-твоему ({timeparse.fmt(fire_at)} в {zone})"
    if not created:
        return (f"Already exists: reminder #{reminder_id} '{text}' at {label}. "
                "Do not create another; tell the user it is already set.")
    ctx.created_reminder_ids.append(reminder_id)
    return f"Created reminder #{reminder_id}: '{text}' at {label}. Repeat this time in the reply."


REMINDER_CREATE = Tool(
    name="reminder_create",
    description=("Set a reminder. Pass the user's time phrase VERBATIM in `when` (e.g. 'в четверг "
                 "в 11', 'через 20 минут', 'завтра утром', '25 сентября в 10') — the tool parses "
                 "it; never compute the date yourself. `text` is what to remind about, without "
                 "the time words. Refuses times in the past and exact duplicates."),
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What to remind about, in the user's words, without the time."},
            "when": {"type": "string", "description": "The time phrase exactly as the user said it."},
            "tz": {"type": "string", "description": "IANA time zone when the time is not the user's own (a flight abroad: mail_extract_trip gives it), else empty string."},
        },
    },
    fn=_create,
)


def _list(scope: UserScope, ctx: ToolContext) -> str:
    rows = db.list_reminders(scope.user_id, status="scheduled", limit=20)
    if not rows:
        return "No scheduled reminders."
    local_now = _now().astimezone(ZoneInfo(scope.tz))
    lines = []
    for r in rows:
        fire_at = r["fire_at"].astimezone(ZoneInfo(scope.tz))
        lines.append(f"#{r['id']} {timeparse.fmt(fire_at, local_now)}: {r['text']}")
    return f"{len(rows)} scheduled reminder(s):\n" + "\n".join(lines)


REMINDER_LIST = Tool(
    name="reminder_list",
    description="The user's scheduled reminders, soonest first, with ids. Use for 'что у меня по напоминаниям', 'какие напоминания стоят'.",
    input_schema={"type": "object", "properties": {}},
    fn=_list,
)


def _cancel(scope: UserScope, ctx: ToolContext, reminder_id: int) -> str:
    row = db.get_reminder(scope.user_id, int(reminder_id))
    if not row:
        return f"Error: no reminder #{reminder_id} for this user."
    if row["status"] != "scheduled":
        return f"Reminder #{reminder_id} is already {row['status']}."
    db.set_reminder_status(scope.user_id, int(reminder_id), "cancelled")
    return f"Cancelled reminder #{reminder_id} '{row['text']}'."


REMINDER_CANCEL = Tool(
    name="reminder_cancel",
    description="Cancel a scheduled reminder by id. Find the id with reminder_list first if the user named it by text.",
    input_schema={
        "type": "object",
        "properties": {"reminder_id": {"type": "integer", "description": "The reminder id."}},
    },
    fn=_cancel,
)
