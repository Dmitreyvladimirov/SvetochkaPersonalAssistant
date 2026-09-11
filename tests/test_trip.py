"""The ticket scenario: attachments into the chat, flights extracted as structure
(never the body to the agent), one button for all calendar events, airport zones."""
import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from sveta.core import bot, config, crypto, google, llm, telegram
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, run
from sveta.tools import calendar as calendar_tool
from tests.fakedb import install
from tests.fakellm import FakeClient
from tests.test_bot import msg, wire

NOW = datetime(2026, 9, 10, 7, 0, tzinfo=timezone.utc)
BODY = ("Confirmación de compra. Localizador 7PVOQO. Pasajero: DMITRY K.\n"
        "UX1301 TLV 07:40 20/09/2026 -> MAD 11:55\nUX0091 MAD 16:05 20/09/2026 -> BOG 19:50\n"
        "UX0092 BOG 21:50 04/10/2026 -> MAD 14:25 05/10/2026\nUX1300 MAD 17:15 05/10/2026 -> TLV 23:05")
EXTRACTED = {"pnr": "7PVOQO", "passengers": ["DMITRY K."], "legs": [
    {"flight": "UX1301", "from_iata": "TLV", "from_city": "Тель-Авив", "to_iata": "MAD", "to_city": "Мадрид",
     "date": "2026-09-20", "depart": "07:40", "arrive": "11:55", "arrive_date": "2026-09-20"},
    {"flight": "UX0091", "from_iata": "MAD", "from_city": "Мадрид", "to_iata": "BOG", "to_city": "Богота",
     "date": "2026-09-20", "depart": "16:05", "arrive": "19:50", "arrive_date": "2026-09-20"},
    {"flight": "UX0092", "from_iata": "BOG", "from_city": "Богота", "to_iata": "MAD", "to_city": "Мадрид",
     "date": "2026-10-04", "depart": "21:50", "arrive": "14:25", "arrive_date": "2026-10-05"},
    {"flight": "UX1300", "from_iata": "MAD", "from_city": "Мадрид", "to_iata": "TLV", "to_city": "Тель-Авив",
     "date": "2026-10-05", "depart": "17:15", "arrive": "23:05", "arrive_date": "2026-10-05"},
]}


def scope():
    return UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem")


def connect(fake, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "cs")
    fake.save_oauth_token(1, "google", "d@x", crypto.encrypt("rt"))
    monkeypatch.setattr(google, "_post", lambda url, **kw: (200, {"access_token": "at"}))
    monkeypatch.setattr(calendar_tool, "_now", lambda: NOW)


def test_search_lists_attachments_and_parser_finds_them(monkeypatch):
    fake = install(monkeypatch)
    connect(fake, monkeypatch)
    pdf_b64 = base64.urlsafe_b64encode(b"%PDF-1.4 ticket").decode().rstrip("=")

    def get(url, **kw):
        if url.endswith("/messages"):
            return 200, {"messages": [{"id": "m2"}]}
        if "/attachments/" in url:
            return 200, {"data": pdf_b64, "size": 15}
        return 200, {"id": "m2", "snippet": "Your e-ticket", "internalDate": "1757000000000",
                     "payload": {"mimeType": "multipart/mixed", "headers": [
                         {"name": "From", "value": "Air Europa <noreply@aireuropa.com>"}, {"name": "Subject", "value": "E-ticket 7PVOQO"}],
                         "parts": [{"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(BODY.encode()).decode()}},
                                   {"mimeType": "application/pdf", "filename": "eticket_7PVOQO.pdf",
                                    "body": {"attachmentId": "att-1", "size": 15}}]}}
    monkeypatch.setattr(google, "_get", get)
    out = run(REGISTRY, "mail_search", scope(), ToolContext(), {"query": "билет"})
    assert "📎 eticket_7PVOQO.pdf (1 KB)" in out and "Playbook «trip»" in out
    assert google.message_attachments(1, "m2")[0]["attachment_id"] == "att-1"
    assert google.download_attachment(1, "m2", "att-1") == b"%PDF-1.4 ticket"


def test_send_attachment_goes_to_the_chat_not_the_model(monkeypatch):
    fake = install(monkeypatch)
    connect(fake, monkeypatch)
    monkeypatch.setattr(google, "message_attachments", lambda uid, gid: [
        {"filename": "eticket_7PVOQO.pdf", "mime": "application/pdf", "size": 15, "attachment_id": "att-1"},
        {"filename": "invoice.pdf", "mime": "application/pdf", "size": 9, "attachment_id": "att-2"}])
    monkeypatch.setattr(google, "download_attachment", lambda uid, gid, aid: b"%PDF-1.4 " + aid.encode())
    sent = []
    monkeypatch.setattr(telegram, "send_document", lambda chat_id, filename, data, caption="": sent.append((chat_id, filename, data, caption)) or 77)
    out = run(REGISTRY, "mail_send_attachment", scope(), ToolContext(), {"gmail_id": "m2", "filename": ""})
    assert out.startswith("Which one?") and sent == []
    out = run(REGISTRY, "mail_send_attachment", scope(), ToolContext(), {"gmail_id": "m2", "filename": "eticket"})
    assert out.startswith("Sent eticket_7PVOQO.pdf") and "%PDF" not in out
    assert sent == [("111", "eticket_7PVOQO.pdf", b"%PDF-1.4 att-1", "📎 eticket_7PVOQO.pdf")]
    monkeypatch.setattr(telegram, "send_document", lambda *a, **k: None)
    assert run(REGISTRY, "mail_send_attachment", scope(), ToolContext(), {"gmail_id": "m2", "filename": "invoice"}).startswith("Error: Telegram")


def test_extract_trip_returns_structure_with_verbatim_phrases(monkeypatch):
    fake = install(monkeypatch)
    fake.add_user("111")
    fake.save_mail_messages(1, [{"gmail_id": "m2", "subject": "E-ticket", "sender": "Air Europa",
                                 "body_enc": crypto.encrypt(BODY), "received_at": NOW}])
    seen = {}

    class Haiku:
        class messages:
            @staticmethod
            def create(**kw):
                seen.update(kw)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="```json\n" + json.dumps(EXTRACTED) + "\n```")],
                                       usage=SimpleNamespace(input_tokens=500, output_tokens=200))
    monkeypatch.setattr(llm, "client", lambda: Haiku())
    out = run(REGISTRY, "mail_extract_trip", scope(), ToolContext(inbox_item_id=5), {"gmail_id": "m2"})
    assert seen["model"] == config.CHEAP_MODEL and BODY[:40] in seen["messages"][0]["content"]
    assert out.startswith("4 flight(s), PNR 7PVOQO:")
    assert 'calendar_create(title="✈️ UX1301 Тель-Авив → Мадрид", when="20.09.2026 в 07:40", duration_min=255, tz="Asia/Jerusalem")' in out
    assert 'calendar_create(title="✈️ UX0091 Мадрид → Богота", when="20.09.2026 в 16:05", duration_min=225, tz="Europe/Madrid")' in out
    assert 'calendar_create(title="✈️ UX0092 Богота → Мадрид", when="04.10.2026 в 21:50", duration_min=995, tz="America/Bogota")' in out
    assert 'reminder_create(text="завтра вылет UX1301 TLV→MAD", when="19.09.2026 в 09:00")' in out
    assert fake.llm_calls[-1]["purpose"] == "extract"
    assert run(REGISTRY, "mail_extract_trip", scope(), ToolContext(), {"gmail_id": "nope"}).startswith("Error: unknown")


def test_extract_trip_without_flights_says_so(monkeypatch):
    fake = install(monkeypatch)
    fake.save_mail_messages(1, [{"gmail_id": "m1", "subject": "Receipt", "sender": "x", "body_enc": crypto.encrypt("Paid 500 EUR"), "received_at": NOW}])

    class Haiku:
        class messages:
            @staticmethod
            def create(**kw):
                return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"pnr": null, "legs": []}')],
                                       usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    monkeypatch.setattr(llm, "client", lambda: Haiku())
    assert run(REGISTRY, "mail_extract_trip", scope(), ToolContext(), {"gmail_id": "m1"}).startswith("No flights found")


def test_calendar_create_with_an_airport_zone_and_a_dated_phrase(monkeypatch):
    fake = install(monkeypatch)
    connect(fake, monkeypatch)
    label = calendar_tool.describe(scope(), {"title": "✈️ UX0091 Мадрид → Богота", "when": "20.09.2026 в 16:05",
                                             "duration_min": 225, "description": "", "tz": "Europe/Madrid"})
    assert label == "Создать событие: ✈️ UX0091 Мадрид → Богота — вс 20.09 в 16:05–19:50 (Europe/Madrid)"
    assert calendar_tool.describe(scope(), {"title": "x", "when": "завтра в 10", "duration_min": 60, "description": "", "tz": "Mars/Olympus"}).startswith("Error: unknown time zone")


def test_four_cards_and_one_button_for_all(monkeypatch):
    calls = [("calendar_create", {"title": f"✈️ leg {i}", "when": f"{20 + i}.09.2026 в 10:00", "duration_min": 60,
                                  "description": "", "tz": "Europe/Madrid"}) for i in range(4)]
    fake, sent, client = wire(monkeypatch, [calls, "Четыре сегмента, подтверди."])
    connect(fake, monkeypatch)
    inserted = []
    monkeypatch.setattr(google, "create_event", lambda uid, title, start, end, description="": inserted.append(title) or {"id": "e", "link": ""})
    bot.handle_update(msg("занеси перелёты в календарь", update_id=1))
    item_id = list(fake.inbox)[0]
    _, _, _, markup = sent.edits[-1]
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert datas[:4] == [f"cf:{item_id}:{n}" for n in range(4)] and datas[4] == f"cfa:{item_id}"
    assert markup["inline_keyboard"][4][0]["text"] == "✓✓ Всё сразу (4)"

    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"cfa:{item_id}",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert inserted == [f"✈️ leg {i}" for i in range(4)]
    assert sum(1 for m in sent.messages if m[1].startswith("Создала событие")) == 4
    bot.handle_update({"update_id": 3, "callback_query": {"id": "cb", "data": f"cfa:{item_id}",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert len(inserted) == 4                       # a second "all" creates nothing more
    bot.handle_update({"update_id": 4, "callback_query": {"id": "cb", "data": f"cf:{item_id}:2",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert sent.messages[-1][1] == "Это уже сделано."
