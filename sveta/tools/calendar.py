"""Calendar (FR-24, FR-25). Reading is free; writing is gated behind a card — the
loop never runs calendar_create, it shows "Создать событие: …" and the tap runs
`execute()` by code. Dates are parsed here, never by the model (§6.1)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sveta.core import google, timeparse
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool

DEFAULT_DURATION_MIN = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _query(scope: UserScope, ctx: ToolContext, period: str, query: str) -> str:
    now = _now()
    rng = google.range_for(period, now, scope.tz)
    if rng is None:
        return f"Error: could not understand the period {period!r}. Try 'сегодня', 'завтра', 'на этой неделе', 'в четверг'."
    start, end = rng
    if query:
        # "когда встреча с Артёмом": look ahead 60 days, not just the period.
        end = max(end, start + timedelta(days=60))
    try:
        events = google.list_events(scope.user_id, start, end, query=query.strip())
    except google.NotConnected as e:
        return f"Error: {e}"
    except google.GoogleError as e:
        return f"Error: calendar unavailable ({e}). Tell the user honestly."
    if not events:
        what = f" matching '{query}'" if query else ""
        return f"No events{what} between {start:%d.%m} and {end:%d.%m}."
    local_now = now.astimezone(ZoneInfo(scope.tz))
    lines = []
    for e in events[:20]:
        when = "весь день " + f"{e['start']:%d.%m}" if e["all_day"] else timeparse.fmt(e["start"], local_now)
        tail = f"–{e['end']:%H:%M}" if e.get("end") and not e["all_day"] else ""
        place = f" @ {e['location']}" if e.get("location") else ""
        lines.append(f"- {when}{tail}: {e['summary']}{place} {e.get('link') or ''}".rstrip())
    return f"{len(events)} event(s):\n" + "\n".join(lines)


CALENDAR_QUERY = Tool(
    name="calendar_query",
    description=("Read the user's Google Calendar. period is a phrase parsed by code: 'сегодня', "
                 "'завтра', 'на этой неделе', 'на следующей неделе', 'в четверг', '25 сентября', or "
                 "empty for the next 7 days. query narrows by title ('встреча с Артёмом') and looks "
                 "60 days ahead. Returns times in the user's timezone with links."),
    input_schema={
        "type": "object",
        "properties": {
            "period": {"type": "string", "description": "The period phrase as the user said it, or empty string."},
            "query": {"type": "string", "description": "Words from the event title, or empty string."},
        },
    },
    fn=_query,
)


def _plan(scope: UserScope, args: dict) -> tuple[str, datetime, datetime] | str:
    title = " ".join(str(args.get("title", "")).split())
    when = str(args.get("when", "")).strip()
    duration = int(args.get("duration_min") or DEFAULT_DURATION_MIN)
    if not title:
        return "Error: title is empty."
    if not when:
        return "Error: when is empty — ask the user for a date and time."
    tz = str(args.get("tz") or "").strip() or scope.tz    # a flight leaves at the airport's time
    try:
        ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        return f"Error: unknown time zone {tz!r}; pass an IANA name like Europe/Madrid or empty string."
    now = _now()
    start = timeparse.parse(when, now=now, tz=tz)
    if start is None:
        return f"Error: could not understand the time {when!r}; ask the user to rephrase."
    if start <= now.astimezone(ZoneInfo(tz)):
        return f"Error: {timeparse.fmt(start)} is in the past; nothing proposed."
    duration = max(5, min(duration, 24 * 60))
    return title, start, start + timedelta(minutes=duration)


def _local_now(scope: UserScope) -> datetime:
    return _now().astimezone(ZoneInfo(scope.tz))


def describe(scope: UserScope, args: dict) -> str:
    """Resolves the phrase ONCE and pins the moment into args (start_iso/end_iso):
    the card and the tap must agree even if the tap comes an hour later or
    tomorrow (review C2)."""
    plan = _plan(scope, args)
    if isinstance(plan, str):
        return plan
    title, start, end = plan
    args["start_iso"], args["end_iso"], args["title"] = start.isoformat(), end.isoformat(), title
    zone = f" ({start.tzinfo})" if str(start.tzinfo) != scope.tz else ""
    return f"Создать событие: {title} — {timeparse.fmt(start, _local_now(scope))}–{end:%H:%M}{zone}"


def execute(scope: UserScope, args: dict) -> str:
    """Runs from the tap only. The reply for the chat. Uses the moment pinned by
    describe(); the phrase is never re-parsed."""
    if args.get("start_iso") and args.get("end_iso"):
        title = str(args.get("title") or "").strip() or "(без названия)"
        start = datetime.fromisoformat(args["start_iso"])
        end = datetime.fromisoformat(args["end_iso"])
    else:
        plan = _plan(scope, args)
        if isinstance(plan, str):
            return plan.replace("Error: ", "Не получилось: ")
        title, start, end = plan
    try:
        created = google.create_event(scope.user_id, title, start, end,
                                      description=str(args.get("description") or ""))
    except google.NotConnected as e:
        return str(e)
    except google.GoogleError as e:
        return f"Календарь не ответил ({e}). Событие не создано."
    link = f"\n{created['link']}" if created.get("link") else ""
    return f"Создала событие: {title} — {timeparse.fmt(start, _local_now(scope))}–{end:%H:%M}.{link}"


CALENDAR_CREATE = Tool(
    name="calendar_create",
    description=("Propose a calendar event; it is created ONLY after the user taps the confirm "
                 "button (never say it is created). Pass the time phrase VERBATIM in `when` "
                 "('в четверг в 15', 'завтра в 10:30'); duration_min defaults to 60. Use for "
                 "'поставь встречу', 'запиши в календарь', and when the user asks to put a "
                 "reminder into the calendar too."),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Event title in the user's words."},
            "when": {"type": "string", "description": "The time phrase exactly as the user said it."},
            "duration_min": {"type": "integer", "description": "Duration in minutes; 60 if not said."},
            "description": {"type": "string", "description": "Extra details or empty string."},
            "tz": {"type": "string", "description": "IANA time zone of the event when it differs from the user's (a flight: the departure airport's zone, e.g. Europe/Madrid — mail_extract_trip gives it), else empty string."},
        },
    },
    fn=lambda scope, ctx, **kw: "Error: calendar_create runs only from the confirm button.",
    needs_confirmation=True,
    describe=describe,
)
