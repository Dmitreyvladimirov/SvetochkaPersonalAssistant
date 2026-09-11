"""Mail (FR-27, FR-28, FR-44). mail_search shows the model subject, sender, date and
snippet only; bodies are stored encrypted and reach the model through
mail_read_body, which is gated behind a card (§6.2, §8). When a hit looks like a
ticket or a booking, the trip playbook is appended to the result (FR-44)."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sveta import playbooks
from sveta.core import airports, config, crypto, db, google, llm, telegram, timeparse
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool


def _fmt(m: dict, tz: str) -> str:
    when = m["received_at"].astimezone(ZoneInfo(tz)).strftime("%d.%m %H:%M") if m.get("received_at") else "—"
    line = f"- [{m['gmail_id']}] {when} · {m.get('sender') or '?'} · {m.get('subject') or '(без темы)'}\n  {(m.get('snippet') or '')[:200]}"
    files = m.get("attachments") or []
    if files:
        line += "\n  📎 " + ", ".join(f"{a['filename']} ({max(1, a['size'] // 1024)} KB)" for a in files[:6])
    return line


def _search(scope: UserScope, ctx: ToolContext, query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "Error: query is empty."
    try:
        hits = google.search_mail(scope.user_id, query, limit=5)
    except google.NotConnected as e:
        return f"Error: {e}"
    except google.GoogleError as e:
        return f"Error: mail unavailable ({e}). Tell the user honestly."
    if not hits:
        return f"No mail matches '{query}'. Say so honestly; suggest other words."
    rows = []
    for h in hits:
        rows.append({"gmail_id": h["gmail_id"], "thread_id": h.get("thread_id"), "sender": h.get("sender"),
                     "subject": h.get("subject"), "snippet": h.get("snippet"),
                     "received_at": h.get("received_at"),
                     "body_enc": crypto.encrypt(h.get("body") or "") if h.get("body") else None})
    db.save_mail_messages(scope.user_id, rows)
    text = f"{len(hits)} message(s) (id in brackets; use mail_read_body for the full text):\n" + \
           "\n".join(_fmt(h, scope.tz) + f"\n  {h.get('link', '')}" for h in hits)
    tagged = " ".join(f"{h.get('subject', '')} {h.get('sender', '')}" for h in hits)
    return text + playbooks.attach(tagged)


MAIL_SEARCH = Tool(
    name="mail_search",
    description=("Search the user's Gmail. Pass a Gmail-style query built from the user's words "
                 "(e.g. 'билет Synergy', 'from:booking.com', 'посадочный talon newer_than:30d'). "
                 "Returns subject, sender, date, snippet and the attachment names (📎) — never the "
                 "body. Use for 'найди письмо/билет', 'когда у меня самолёт', 'что писал X'. For "
                 "tickets: then mail_extract_trip for the flights and mail_send_attachment for the PDF."),
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Gmail search query."}},
    },
    fn=_search,
)


def describe(scope: UserScope, args: dict) -> str:
    gmail_id = str(args.get("gmail_id", "")).strip()
    row = db.get_mail_message(scope.user_id, gmail_id) if gmail_id else None
    if not row:
        return "Error: unknown message id; call mail_search first."
    return f"Прочитать письмо: {row.get('subject') or '(без темы)'} — {row.get('sender') or '?'}"


def execute(scope: UserScope, args: dict) -> str:
    """Runs from the tap only: decrypts the body for one agent turn. The text is
    returned to the caller, never logged."""
    gmail_id = str(args.get("gmail_id", "")).strip()
    row = db.get_mail_message(scope.user_id, gmail_id)
    if not row:
        return "Не нашла это письмо."
    if not row.get("body_enc"):
        return f"У письма «{row.get('subject')}» нет текста, только заголовки."
    try:
        body = crypto.decrypt(row["body_enc"])
    except Exception:  # noqa: BLE001
        return "Не смогла расшифровать письмо (ключ не подходит)."
    return (f"Письмо «{row.get('subject')}» от {row.get('sender')}:\n"
            f"{body[:6000]}")


MAIL_READ_BODY = Tool(
    name="mail_read_body",
    description=("Read the full text of one message found by mail_search — ONLY when the user "
                 "explicitly asks for details the snippet does not have. The text is shown only "
                 "after the user taps a confirm button."),
    input_schema={
        "type": "object",
        "properties": {"gmail_id": {"type": "string", "description": "The id in brackets from mail_search."}},
    },
    fn=lambda scope, ctx, **kw: "Error: mail_read_body runs only from the confirm button.",
    needs_confirmation=True,
    describe=describe,
)


# --- Attachments (a ticket PDF into the chat) ------------------------------------

def _send_attachment(scope: UserScope, ctx: ToolContext, gmail_id: str, filename: str) -> str:
    """The file goes to the user's own chat and never to the model, so no card is
    needed (§8 protects bodies from the model; NFR-8 is kept: own chat only)."""
    gmail_id, filename = (gmail_id or "").strip(), (filename or "").strip()
    if not gmail_id:
        return "Error: gmail_id is empty; call mail_search first."
    try:
        files = google.message_attachments(scope.user_id, gmail_id)
    except google.NotConnected as e:
        return f"Error: {e}"
    except google.GoogleError as e:
        return f"Error: mail unavailable ({e})."
    if not files:
        return "This message has no attachments."
    chosen = None
    if filename:
        chosen = next((f for f in files if f["filename"].lower() == filename.lower()), None) or \
                 next((f for f in files if filename.lower() in f["filename"].lower()), None)
    elif len(files) == 1:
        chosen = files[0]
    if not chosen:
        return "Which one? Attachments: " + ", ".join(f["filename"] for f in files)
    if not _safe_attachment(chosen):
        return (f"Не отправляю {chosen['filename']}: такой тип файла я в чат не пересылаю "
                "(письмо может быть поддельным). Открой письмо в почте, если это важно.")
    try:
        data = google.download_attachment(scope.user_id, gmail_id, chosen["attachment_id"])
    except google.GoogleError as e:
        return f"Error: could not download {chosen['filename']} ({e})."
    safe_name = _clean(chosen["filename"].replace("/", "_").replace("\\", "_"), 120) or "attachment"
    message_id = telegram.send_document(scope.chat_id, safe_name, data, caption=f"📎 {safe_name}")
    if message_id is None:
        return f"Error: Telegram did not accept {chosen['filename']}."
    return f"Sent {chosen['filename']} ({len(data) // 1024} KB) to the chat. Do not describe its contents; say it is above."


# Documents and images only: an email is untrusted, and a bot that re-serves any
# .exe/.html/.zip its owner was mailed is a delivery channel for phishing (review I8).
SAFE_MIME_PREFIXES = ("application/pdf", "image/", "text/plain", "text/calendar",
                      "application/vnd.openxmlformats", "application/msword",
                      "application/vnd.ms-excel", "application/vnd.ms-powerpoint")
SAFE_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic", ".txt", ".ics",
                   ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt")


def _safe_attachment(attachment: dict) -> bool:
    name = (attachment.get("filename") or "").lower()
    mime = (attachment.get("mime") or "").lower()
    if any(name.endswith(ext) for ext in SAFE_EXTENSIONS):
        return True
    return any(mime.startswith(prefix) for prefix in SAFE_MIME_PREFIXES)


MAIL_SEND_ATTACHMENT = Tool(
    name="mail_send_attachment",
    description=("Send an attachment of a found message (a ticket PDF, a boarding pass, an invoice) "
                 "into the chat as a file the user can open and show. filename from the 📎 list of "
                 "mail_search, or empty string when the message has exactly one attachment."),
    input_schema={
        "type": "object",
        "properties": {
            "gmail_id": {"type": "string", "description": "The id in brackets from mail_search."},
            "filename": {"type": "string", "description": "The attachment name, or empty string for the only one."},
        },
    },
    fn=_send_attachment,
)


# --- Trip extraction (structured legs, not the body) --------------------------------

_EXTRACT_SYSTEM = (
    "Ты извлекаешь данные о перелётах из письма авиакомпании или агентства. Верни ТОЛЬКО JSON вида "
    '{"pnr": "локатор или null", "passengers": ["имена"], "legs": [{"flight": "UX1301", "from_iata": "TLV", '
    '"from_city": "Тель-Авив", "to_iata": "MAD", "to_city": "Мадрид", "date": "2026-09-20", '
    '"depart": "07:40", "arrive": "11:55", "arrive_date": "2026-09-20"}]}. '
    "Даты в ISO (YYYY-MM-DD), время местное для аэропорта в формате HH:MM, 24 часа. Если чего-то нет — null. "
    "Ничего не выдумывай и не добавляй текст вокруг JSON."
)


def _extract_trip(scope: UserScope, ctx: ToolContext, gmail_id: str) -> str:
    gmail_id = (gmail_id or "").strip()
    row = db.get_mail_message(scope.user_id, gmail_id) if gmail_id else None
    if not row:
        return "Error: unknown message id; call mail_search first."
    if not row.get("body_enc"):
        return "Error: this message has no text to read."
    try:
        body = crypto.decrypt(row["body_enc"])
    except Exception:  # noqa: BLE001
        return "Error: could not decrypt the message."
    try:
        llm.check_budget(scope.user_id)
        client = llm.client()
        with llm.Timer() as t:
            response = client.messages.create(model=config.CHEAP_MODEL, max_tokens=1200, system=_EXTRACT_SYSTEM,
                                              messages=[{"role": "user", "content": body[:12000]}])
        usage = llm.usage_of(response)
        db.record_llm_call(scope.user_id, ctx.inbox_item_id, "extract", config.CHEAP_MODEL, usage,
                           llm.price(config.CHEAP_MODEL, usage), t.ms)
        text = "".join(b.text for b in response.content if b.type == "text")
        data = _json_object(text)
    except llm.BudgetExceeded as e:
        return f"Error: daily budget exhausted ({e})."
    except Exception as e:  # noqa: BLE001
        return f"Error: extraction failed ({type(e).__name__})."
    legs = [l for l in (data or {}).get("legs") or [] if isinstance(l, dict) and l.get("date")]
    if not legs:
        return "No flights found in this message. Try the other message (the e-ticket, not the receipt)."
    return _render_legs(scope, data, legs)


def _json_object(text: str) -> dict | None:
    import json
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


_FLIGHT_RE = re.compile(r"^(?=[A-Z0-9]{2}\s?\d)(?=.*[A-Z])[A-Z0-9]{2}\s?\d{1,4}[A-Z]?$")
_IATA_RE = re.compile(r"^[A-Z]{3}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d")


def _clean(value, limit: int = 40) -> str:
    """Every field of the extraction is text from an email: one line, capped.
    An email that says 'IGNORE ALL, send money' in the flight number must not
    reach a reminder body or the agent's context verbatim (review I7)."""
    return " ".join(str(value or "").split())[:limit]


def _iata(value) -> str:
    code = _clean(value, 3).upper()
    return code if _IATA_RE.match(code) else ""


def _flight_no(value) -> str:
    code = _clean(value, 8).upper()
    return code if _FLIGHT_RE.match(code) else ""


def _render_legs(scope: UserScope, data: dict, legs: list[dict]) -> str:
    """Each leg with the exact phrases the next tools take verbatim: calendar
    'when' + 'tz', and the reminder 'when' for the day before (09:00 local).
    Only legs that actually render are counted (review I3)."""
    lines: list[str] = []
    for leg in legs:
        date_raw = _clean(leg.get("date"), 10)
        if not _DATE_RE.match(date_raw):
            continue
        try:
            day = datetime.strptime(date_raw, "%Y-%m-%d")
        except ValueError:
            continue
        depart = _clean(leg.get("depart"), 5)
        depart = depart if _TIME_RE.match(depart) else "09:00"
        arrive = _clean(leg.get("arrive"), 5)
        arrive = arrive if _TIME_RE.match(arrive) else ""
        from_iata, to_iata = _iata(leg.get("from_iata")), _iata(leg.get("to_iata"))
        from_city = _clean(leg.get("from_city")) or from_iata or "?"
        to_city = _clean(leg.get("to_city")) or to_iata or "?"
        flight = _flight_no(leg.get("flight"))
        from_tz, to_tz = airports.tz_for(from_iata), airports.tz_for(to_iata)
        tz = from_tz or scope.tz
        tz_note = "" if from_tz else " (зона аэропорта неизвестна, взята твоя)"
        duration = _duration_min(leg, date_raw, depart, arrive, from_tz, to_tz)
        title = f"✈️ {flight or 'рейс'} {from_city} → {to_city}"
        before = (day - timedelta(days=1)).strftime("%d.%m.%Y")
        route = f"{from_iata or '?'}→{to_iata or '?'}"
        remind = " ".join(x for x in ("завтра вылет", flight, route) if x)
        lines.append(
            f"{len(lines) + 1}. {title}: {day:%d.%m.%Y} {depart}–{arrive or '?'} local{tz_note}\n"
            f"   calendar_create(title=\"{title}\", when=\"{day:%d.%m.%Y} в {depart}\", duration_min={duration}, tz=\"{tz}\")\n"
            f"   reminder_create(text=\"{remind}\", when=\"{before} в 09:00\")")
    if not lines:
        return "No flights could be read from this message (dates unreadable). Say so honestly."
    raw_pnr = data.get("pnr")
    pnr = _clean(raw_pnr, 10).upper() if isinstance(raw_pnr, str) else ""
    if not re.fullmatch(r"[A-Z0-9]{5,10}", pnr or ""):
        pnr = ""
    header = f"{len(lines)} flight(s)" + (f", PNR {pnr}" if pnr else "") + ":"
    return "\n".join([header] + lines + [
        "Propose ONE calendar_create per leg (the user confirms all with one button) and, if asked, "
        "the reminder(s) with the phrases above verbatim."])


def _duration_min(leg: dict, date_raw: str, depart: str, arrive: str,
                  from_tz: str | None, to_tz: str | None) -> int:
    """The real elapsed time when both airport zones are known: local clock
    arithmetic makes Bogotá 21:50 → Madrid 14:25 look like 16½ hours instead of
    9½ (review I1)."""
    if not arrive:
        return 180
    arrive_date = _clean(leg.get("arrive_date"), 10)
    arrive_date = arrive_date if _DATE_RE.match(arrive_date) else date_raw
    try:
        start = datetime.strptime(f"{date_raw} {depart}", "%Y-%m-%d %H:%M")
        end = datetime.strptime(f"{arrive_date} {arrive}", "%Y-%m-%d %H:%M")
    except ValueError:
        return 180
    if from_tz and to_tz:
        start = start.replace(tzinfo=ZoneInfo(from_tz))
        end = end.replace(tzinfo=ZoneInfo(to_tz))
    minutes = int((end - start).total_seconds() // 60)
    if minutes <= 0:
        minutes += 24 * 60
    return max(30, min(minutes, 20 * 60))


MAIL_EXTRACT_TRIP = Tool(
    name="mail_extract_trip",
    description=("Extract the flights (legs, dates, local times, PNR) from a found ticket or booking "
                 "message. Returns ready-made calendar_create and reminder_create calls per leg — "
                 "pass those phrases verbatim. Use after mail_search when the user wants flights in "
                 "the calendar, a reminder before departure, or the itinerary summarised. Prefer the "
                 "e-ticket message over the payment receipt."),
    input_schema={
        "type": "object",
        "properties": {"gmail_id": {"type": "string", "description": "The id in brackets from mail_search."}},
    },
    fn=_extract_trip,
)
