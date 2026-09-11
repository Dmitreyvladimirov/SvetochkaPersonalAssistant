"""The world the golden scenarios run in: a connected Google account with a small,
deliberately awkward mailbox and calendar, and the notes, facts and lists a user
of three months would have.

Without this the run measured less than it looked like it did — every
`mail_search` and `calendar_query` answered "Google не подключён" before reaching
any query logic, and every `note_search`, `fact_recall` and `list_show` answered
into an empty database, so a whole class of failures (searching once and giving
up, offering the wrong ticket, asking the user for better words) was invisible to
65 scenarios by construction.

The mailbox is modelled on the real one that broke it on 2026-09-12: the Colombia
ticket never says "Colombia", the party ticket is in Hebrew, and an old London
ticket sits there to be mistaken for a current one.
"""
from datetime import datetime, timedelta, timezone

from sveta.core import crypto, google

NOW = datetime.now(timezone.utc)


def _at(days: int) -> datetime:
    return NOW - timedelta(days=days)


# (gmail_id, sender, subject, snippet, body, attachments, received)
MAILBOX = [
    ("m-col", "Air Europa <noreply@aireuropa.com>",
     "Confirmación de compra — Localizador 7PVOQO",
     "UX1301 TLV-MAD 20/09, UX0091 MAD-BOG 20/09, vuelta 04/10",
     "Localizador 7PVOQO. Pasajero DMITRY K.\n"
     "UX1301 TLV 07:40 20/09/2026 -> MAD 11:55\nUX0091 MAD 16:05 20/09/2026 -> BOG 19:50\n"
     "UX0092 BOG 21:50 04/10/2026 -> MAD 14:25 05/10/2026\nUX1300 MAD 17:15 05/10/2026 -> TLV 23:05",
     [{"filename": "eticket_7PVOQO.pdf", "mime": "application/pdf", "size": 240_000, "attachment_id": "a-col"}],
     _at(9)),
    ("m-party", "Tickets <no-reply@eventim.co.il>",
     "כרטיס לאירוע — Shesh Besh Party, תל אביב",
     "הכרטיס שלך למסיבה ביום שבת, 19:00, הכניסה עם קוד QR",
     "כרטיס כניסה. Shesh Besh Party. שבת, 21:00. רחוב לילינבלום 12, תל אביב.",
     [{"filename": "ticket_qr.pdf", "mime": "application/pdf", "size": 90_000, "attachment_id": "a-party"}],
     _at(4)),
    ("m-london", "Wizz Air <noreply@wizzair.com>",
     "Your booking DDDK6P — Tel Aviv to London",
     "W6 2314, 13 May 2023, TLV-LTN",
     "Booking DDDK6P. W6 2314 TLV 06:20 13/05/2023 -> LTN 09:55.",
     [], datetime(2023, 5, 1, tzinfo=timezone.utc)),
    ("m-power", "חברת החשמל <noreply@iec.co.il>",
     "חשבון חשמל — 312.60 ₪",
     "התשלום יבוצע ב-25 לחודש",
     "חשבון חשמל לתקופה 07-08/2026, סכום 312.60 ₪.", [], _at(20)),
    ("m-kostya", "Костя <kostya@example.com>", "Re: бэкапы и инфра",
     "Скинул доступы, проверь до пятницы", "Доступы в личке, проверь до пятницы.", [], _at(2)),
]

EVENTS = [
    {"id": "e1", "summary": "Созвон с Костей", "start": NOW + timedelta(hours=4),
     "end": NOW + timedelta(hours=5), "link": "https://cal/e1", "all_day": False, "location": "Zoom"},
    {"id": "e2", "summary": "Встреча с Артёмом", "start": NOW + timedelta(days=3, hours=2),
     "end": NOW + timedelta(days=3, hours=3), "link": "https://cal/e2", "all_day": False, "location": None},
    # Already happened, and written with "е" where the question will say "ё":
    # "когда я был у врача" is answered by silence unless the search looks behind.
    {"id": "e3", "summary": "Приём у врача, Петрова", "start": NOW - timedelta(days=26),
     "end": NOW - timedelta(days=26) + timedelta(hours=1), "link": "https://cal/e3",
     "all_day": False, "location": "Клалит, Дизенгоф 50"},
]


def _hit(row: tuple) -> dict:
    gmail_id, sender, subject, snippet, body, attachments, received = row
    return {"gmail_id": gmail_id, "thread_id": gmail_id, "sender": sender, "subject": subject,
            "snippet": snippet, "received_at": received, "body": body, "attachments": attachments,
            "link": f"https://mail.google.com/mail/u/0/#all/{gmail_id}"}


def _terms(query: str) -> list[str]:
    """The words of a Gmail query that actually constrain it: operators, quotes and
    boolean noise dropped. A query matches a message when any of them appears in
    it — deliberately generous, because what is under test is whether Svetochka
    keeps trying, not whether this stands in for Gmail's ranking."""
    out = []
    for raw in query.replace("(", " ").replace(")", " ").replace('"', " ").split():
        token = raw.strip().lower()
        if not token or token in ("or", "and"):
            continue
        if token.startswith(("after:", "before:", "newer_than:", "older_than:", "has:", "in:", "label:")):
            continue
        if token.startswith(("from:", "subject:")):
            token = token.split(":", 1)[1]
        if len(token) >= 3:
            out.append(token)
    return out


def search_mail(user_id: int, query: str, *, limit: int = 5) -> list[dict]:
    terms = _terms(query)
    if not terms:
        return []
    hits = []
    for row in MAILBOX:
        haystack = " ".join(str(x) for x in row[:5]).lower()
        if any(term in haystack for term in terms):
            hits.append(_hit(row))
    return hits[:limit]


# What the scenarios ask about, worded so a literal search misses and a broadened
# one finds: the note says "через шаблоны", the question says "для новых".
NOTES = [
    ("онбординг через шаблоны, а не через пустой холст", "Vespera"),
    ("продукт без онбординга не продаётся", ""),
    ("Turilin подключил Claude Code к телеграму — посмотреть, как сделан цикл", ""),
    ("реферальную программу отложить до весны, сначала биллинг", "Vespera"),
    ("врач Иванова принимает по вторникам, Клалит на Дизенгоф", ""),
    ("бюджет на квартал: инфра ~$120, модели ~$60", "Vespera"),
]
FACTS = [
    ("Костя", "отвечает за", "инфру и бэкапы"),
    ("врач Светы", "это", "Иванова"),
]
LISTS = {
    "Покупки": ["молоко", "батарейки", "кофе"],
    "Сегодня": ["позвонить маме", "оплатить счёт"],
}


def install(monkeypatch) -> None:
    """A connected Google with its mailbox and calendar, and the user's own notes,
    facts and lists — without them a read tool answers into nothing and the reply
    proves nothing about how Svetochka searches."""
    from sveta.core import db
    db.save_oauth_token(1, "google", "dima@example.com", crypto.encrypt("rt"),
                        scopes=" ".join(google.SCOPES))
    for body, project in NOTES:
        db.create_note(1, body, project=project or None)
    for subject, predicate, obj in FACTS:
        db.remember_fact(1, subject, predicate, obj)
    for name, items in LISTS.items():
        db.add_list_items(1, db.get_or_create_list(1, name)["id"], items)
    monkeypatch.setattr(google, "search_mail", lambda uid, q, limit=5: search_mail(uid, q, limit=limit))
    monkeypatch.setattr(google, "list_events",
                        lambda uid, start, end, query="", limit=20: [
                            e for e in EVENTS
                            if start <= e["start"].astimezone(start.tzinfo) < end
                            and (not query or query.lower() in e["summary"].lower())])
    monkeypatch.setattr(google, "message_attachments",
                        lambda uid, gid: next((_hit(r)["attachments"] for r in MAILBOX if r[0] == gid), []))
    monkeypatch.setattr(google, "download_attachment", lambda uid, gid, aid, size=0: b"%PDF-1.4 golden")
    monkeypatch.setattr(google, "create_event", lambda *a, **k: {"id": "new", "link": "https://cal/new"})
