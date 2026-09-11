"""The morning brief and the evening review (FR-30, FR-31, FR-32, FR-34, FR-50).

Data is gathered deterministically from what Svetochka already holds; the model
gets one call to phrase it (SPEC.md §6.1) and is not trusted with the facts: the
template below is both the fallback and the ceiling — if the model's text is
longer than ten lines or the call fails, the template is what gets sent.

Per user, per day, idempotent: `digests` has UNIQUE (user_id, kind, for_date),
so the 15-minute cron window cannot send twice."""
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sveta.core import config, db, llm, telegram

logger = logging.getLogger(__name__)

MAX_LINES = 10
# Railway cron is best-effort (a run is skipped while the previous one is still
# running), so the window is wide; at-most-once comes from the digests UNIQUE,
# not from the window.
WINDOW_MINUTES = 180
KINDS = {"brief": ("brief.time", "07:30"), "review": ("review.time", "21:00")}
HEADERS = {
    "meetings": "Встречи",
    "trips": "Поездки",
    "today": "Напоминания сегодня",
    "overdue": "Висит со вчера",
    "list_today": "Сегодня",
    "news": "Почитать",
    "open": "Не закрыто",
}


def _hhmm(value: str, default: str) -> tuple[int, int]:
    """'07:30', '7:30', '7.30', '8' all work; garbage falls back to the default."""
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*", value or "")
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h < 24 and 0 <= mi < 60:
            return h, mi
    h, m = default.split(":")
    return int(h), int(m)


def due_kinds(prefs: dict, local_now: datetime) -> list[str]:
    """Which digests fall into the window that starts at the user's set time."""
    out = []
    for kind, (key, default) in KINDS.items():
        h, m = _hhmm(prefs.get(key, default), default)
        start = local_now.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= local_now < start + timedelta(minutes=WINDOW_MINUTES):
            out.append(kind)
    return out


def gather(user_id: int, kind: str, now: datetime, tz: str) -> dict[str, list[str]]:
    """Sections → lines, in the user's tz. Empty sections are simply absent (FR-31)."""
    zone = ZoneInfo(tz)
    local_now = now.astimezone(zone)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    sections: dict[str, list[str]] = {}

    if kind == "brief":
        meetings, trips = _google_sections(user_id, day_start, day_end, zone)
        if meetings:
            sections["meetings"] = meetings
        if trips:
            sections["trips"] = trips
        today = db.reminders_between(user_id, day_start, day_end)
        if today:
            sections["today"] = [f"{r['fire_at'].astimezone(zone):%H:%M} {_one_line(r['text'])}" for r in today]
        overdue = db.reminders_overdue(user_id, day_start)
        if overdue:
            sections["overdue"] = [f"{r['sent_at'].astimezone(zone):%d.%m} {_one_line(r['text'])}" for r in overdue]
        lines = db.unchecked_list_items(user_id, "Сегодня")
        if lines:
            sections["list_today"] = [f"☐ {_one_line(i['text'])}" for i in lines]
        news = db.recent_source_items(user_id, now - timedelta(hours=24), limit=3)
        if news:
            sections["news"] = [f"{_one_line(n['title'] or n['url'])} — {n['url']}" for n in news
                                if n.get("url") or n.get("title")]
    else:  # review
        open_ = db.reminders_overdue(user_id, now)
        if open_:
            sections["open"] = [_one_line(r["text"]) for r in open_]
        lines = db.unchecked_list_items(user_id, "Сегодня")
        if lines:
            sections["list_today"] = [f"☐ {_one_line(i['text'])}" for i in lines]
    return sections


def _google_sections(user_id: int, day_start: datetime, day_end: datetime, zone) -> tuple[list[str], list[str]]:
    """FR-29/FR-30: today's meetings and the next trip. Google down or not
    connected → both empty and one log line; the brief still goes out (§9)."""
    from sveta import playbooks
    from sveta.core import google
    meetings: list[str] = []
    try:
        for e in google.list_events(user_id, day_start, day_end):
            when = "весь день" if e["all_day"] else f"{e['start']:%H:%M}"
            place = f" @ {e['location']}" if e.get("location") else ""
            meetings.append(f"{when} {_one_line(e['summary'])}{place}")
    except google.NotConnected as e:
        row = db.get_oauth_token(user_id, "google")
        if row and row.get("last_error"):
            # §9: a dead refresh is said, not swallowed — the brief is the daily place.
            meetings.append(f"Google отвалился ({_one_line(str(e), 60)}). Переподключи: /connect")
    except Exception as e:  # noqa: BLE001
        logger.warning("brief: calendar skipped for user %s: %s", user_id, e)
    trips: list[str] = []
    try:
        for m in db.recent_trip_mail(user_id, day_start - timedelta(days=30)):
            if playbooks.matching(f"{m.get('subject', '')} {m.get('sender', '')}"):
                trips.append(f"{m['received_at'].astimezone(zone):%d.%m} {_one_line(m.get('subject') or '')}")
            if len(trips) == 2:
                break
    except Exception as e:  # noqa: BLE001
        logger.warning("brief: trips skipped for user %s: %s", user_id, e)
    return meetings, trips


def _one_line(text: str, limit: int = 120) -> str:
    """One physical line per item, or the ≤10-line guarantee means nothing."""
    return " ".join((text or "").split())[:limit]


def template(kind: str, sections: dict[str, list[str]], prefs: dict) -> str:
    """Deterministic text, ≤ MAX_LINES lines. Sections are trimmed from the least
    important (news) upward until it fits; a trimmed section ends with '…и ещё N'."""
    greeting = []
    if kind == "brief" and prefs.get("persona.greeting", "yes") != "no":
        # No name unless the user set one: a second user must not be greeted as Дима.
        name = prefs.get("persona.address", "").split(",")[0].strip().strip("«»\"")
        greeting = [f"Доброе утро, {name}." if name else "Доброе утро."]
    elif kind == "review":
        greeting = ["Вечерний обзор."]

    order = ["open", "meetings", "today", "trips", "overdue", "list_today", "news"]
    budget = MAX_LINES - len(greeting)
    kept = {k: list(v) for k, v in sections.items() if v}
    shown = {k: len(v) for k, v in kept.items()}

    def cost(k):
        return 1 + shown[k] + (1 if shown[k] < len(kept[k]) else 0)

    for victim in ("news", "trips", "list_today", "overdue", "today", "meetings", "open"):
        while victim in kept and sum(cost(k) for k in kept) > budget:
            if shown[victim] > 1:
                shown[victim] -= 1
            else:
                del kept[victim], shown[victim]

    lines = list(greeting)
    for key in order:
        if key in kept:
            lines.append(f"{HEADERS[key]}:")
            lines += kept[key][:shown[key]]
            if shown[key] < len(kept[key]):
                lines.append(f"…и ещё {len(kept[key]) - shown[key]}")
    return "\n".join(lines[:MAX_LINES])


def compose(kind: str, sections: dict[str, list[str]], prefs: dict, user_id: int,
            client=None) -> tuple[str, str | None, float, str | None]:
    """(text, model or None when the template was used, cost, alert or None).
    The model may only rephrase; the code re-checks the line count."""
    base = template(kind, sections, prefs)
    if not sections:
        return base, None, 0.0, None
    try:
        llm.check_budget(user_id)
        client = client or llm.client()
        system = ("Ты — Светочка, личный ассистент. Перепиши утренний бриф живым коротким языком. "
                  "Не больше 10 строк. Ничего не добавляй и не выдумывай: каждый пункт из "
                  "исходника должен остаться, время и ссылки — дословно. Без markdown, без эмодзи "
                  "кроме ☐. Обращение: " + prefs.get("persona.address", "Дима, на ты") + ".")
        if kind == "review":
            system = system.replace("утренний бриф", "вечерний обзор: что осталось незакрытым")
        with llm.Timer() as t:
            response = client.messages.create(model=config.BRIEF_MODEL, max_tokens=600, system=system,
                                              messages=[{"role": "user", "content": base}])
        usage = llm.usage_of(response)
        cost = llm.price(config.BRIEF_MODEL, usage)
        db.record_llm_call(user_id, None, kind, config.BRIEF_MODEL, usage, cost, t.ms)
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        lines = [l for l in text.splitlines() if l.strip()]
        if not lines or len(lines) > MAX_LINES:
            logger.warning("brief: model text rejected (%d lines) — using the template", len(lines))
            return base, None, cost, None
        missing = _dropped_facts(sections, text)
        if missing:
            logger.warning("brief: model dropped %s — using the template", missing[:3])
            return base, None, cost, None
        return "\n".join(lines), config.BRIEF_MODEL, cost, None
    except llm.BudgetExceeded as e:
        logger.warning("brief: budget exceeded for user %s (%s) — template", user_id, e)
        return base, None, 0.0, None
    except Exception as e:  # noqa: BLE001 — the brief still goes out
        logger.exception("brief: model call failed — using the template")
        return base, None, 0.0, llm.classify_error(e)


_FACT_TOKEN = re.compile(r"\b\d{1,2}:\d{2}\b|https?://\S+")


def _dropped_facts(sections: dict[str, list[str]], text: str) -> list[str]:
    """Every clock time and every URL in the gathered data must survive the
    model's rephrasing verbatim — the cheap deterministic check that it did not
    drop or alter an item."""
    return [tok for lines in sections.values() for line in lines
            for tok in _FACT_TOKEN.findall(line) if tok not in text]


def reaction_keyboard(digest_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "👍", "callback_data": f"dg:up:{digest_id}"},
                                 {"text": "👎", "callback_data": f"dg:down:{digest_id}"}]]}


def send_for_user(user: dict, kind: str, now: datetime, *, dry_run: bool = False,
                  client=None) -> str:
    """One digest for one user. Returns a status word for the log:
    already | empty | sent | dry-run | failed."""
    zone = ZoneInfo(user["tz"] or config.TZ)
    for_date = now.astimezone(zone).date()
    existing = db.get_digest(user["id"], kind, for_date)
    if existing:
        payload = existing.get("payload") or {}
        if existing.get("sent_at") or payload.get("empty") or not payload.get("text"):
            return "already"
        # Composed and paid for, but Telegram did not take it: resend the same text
        # (§9 — a failure is retried, never silently dropped).
        if dry_run:
            return "dry-run"
        message_id = telegram.send_message(payload["text"], user["telegram_chat_id"],
                                           reaction_keyboard(existing["id"]))
        if message_id is None:
            return "failed"
        db.set_digest_sent(user["id"], existing["id"], message_id)
        return "resent"
    prefs = db.list_preferences(user["id"])
    sections = gather(user["id"], kind, now, user["tz"] or config.TZ)
    if not sections:
        if not dry_run:
            db.create_digest(user["id"], kind, for_date, {"sections": {}, "empty": True})
        return "empty"
    text, model, cost, alert = compose(kind, sections, prefs, user["id"], client=client)
    if dry_run:
        print(f"--- {kind} for user {user['id']} ({for_date}) ---\n{text}\n")
        return "dry-run"
    digest_id = db.create_digest(user["id"], kind, for_date,
                                 {"sections": sections, "text": text}, model=model, cost_usd=cost)
    if digest_id is None:
        return "already"
    message_id = telegram.send_message(text, user["telegram_chat_id"], reaction_keyboard(digest_id))
    if message_id is None:
        return "failed"
    db.set_digest_sent(user["id"], digest_id, message_id)
    if alert:
        _alert_once(user, for_date, alert)
    return "sent"


def _alert_once(user: dict, for_date, alert: str) -> None:
    """FR-39: a dead credential is said once a day, in the chat, not only in a log."""
    if db.create_digest(user["id"], "alert", for_date, {"text": alert}) is None:
        return
    telegram.send_message(alert, user["telegram_chat_id"])


def run(now: datetime | None = None, *, dry_run: bool = False, force: str | None = None,
        client=None) -> dict[str, str]:
    """One cron pass over every active user. Returns {f'{user_id}:{kind}': status}."""
    now = now or datetime.now(timezone.utc)
    results = {}
    for user in db.active_users():
        try:
            prefs = db.list_preferences(user["id"])
            local_now = now.astimezone(ZoneInfo(user["tz"] or config.TZ))
            kinds = [force] if force else due_kinds(prefs, local_now)
            for kind in kinds:
                results[f"{user['id']}:{kind}"] = send_for_user(user, kind, now, dry_run=dry_run, client=client)
        except Exception:  # noqa: BLE001 — one user's failure must not stop the others
            logger.exception("brief: user %s failed", user.get("id"))
            results[f"{user['id']}:?"] = "crashed"
    return results
