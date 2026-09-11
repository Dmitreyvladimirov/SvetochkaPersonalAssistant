"""OAuth state binding, encrypted token storage, refresh failure → NotConnected,
calendar and mail parsing, the range phrases."""
import base64
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from sveta.core import config, crypto, google
from tests.fakedb import install

TZ = "Asia/Jerusalem"


def setup(monkeypatch):
    fake = install(monkeypatch)
    fake.add_user("111", tz=TZ)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(config, "PUBLIC_DOMAIN", "sveta.example")
    return fake


def test_state_is_bound_to_the_user_single_use_and_short_lived(monkeypatch):
    import time
    from sveta.core import oauth_state
    setup(monkeypatch)
    state = google.state_for(1)
    uid, nonce, mac = state.split(".")
    assert google.user_from_state(f"2.{nonce}.{mac}") is None      # another user's id, my mac
    assert google.user_from_state("garbage") is None and google.user_from_state("") is None
    assert google.user_from_state(state) == 1
    assert google.user_from_state(state) is None                    # consumed: a replay fails
    state = google.state_for(1)
    monkeypatch.setattr(time, "time", lambda: time.time.__wrapped__() + 601 if hasattr(time.time, "__wrapped__") else 4102444800)
    assert google.user_from_state(state) is None                    # expired after 10 minutes
    monkeypatch.undo()
    setup(monkeypatch)
    state = google.state_for(1)
    assert oauth_state.consume("notion", state) is None             # a Google state is not a Notion state
    url = google.auth_url(1)
    assert "state=1." in url and "access_type=offline" in url
    assert "redirect_uri=https%3A%2F%2Fsveta.example%2Foauth%2Fgoogle%2Fcallback" in url


def test_exchange_stores_the_refresh_token_encrypted(monkeypatch):
    fake = setup(monkeypatch)
    calls = []

    def post(url, **kw):
        calls.append((url, kw.get("data")))
        return 200, {"access_token": "at", "refresh_token": "rt-secret", "scope": "x"}
    monkeypatch.setattr(google, "_post", post)
    monkeypatch.setattr(google, "_get", lambda url, **kw: (200, {"email": "dima@example.com"}))
    assert google.exchange_code(1, "code123") == "dima@example.com"
    row = fake.oauth[(1, "google")]
    assert row["account_email"] == "dima@example.com"
    assert row["refresh_token"] != b"rt-secret" and crypto.decrypt(row["refresh_token"]) == "rt-secret"
    assert calls[0][1]["code"] == "code123" and calls[0][1]["grant_type"] == "authorization_code"


def test_refresh_failure_marks_the_row_and_says_reconnect(monkeypatch):
    fake = setup(monkeypatch)
    with pytest.raises(google.NotConnected):
        google.access_token(1)                      # no row at all
    fake.save_oauth_token(1, "google", "d@x", crypto.encrypt("rt"))
    monkeypatch.setattr(google, "_post", lambda url, **kw: (400, {"error": "invalid_grant"}))
    with pytest.raises(google.NotConnected) as exc:
        google.access_token(1)
    assert "/google" in str(exc.value)
    assert fake.oauth[(1, "google")]["last_error"].startswith("refresh failed: HTTP 400")
    monkeypatch.setattr(google, "_post", lambda url, **kw: (200, {"access_token": "fresh"}))
    assert google.access_token(1) == "fresh" and fake.oauth[(1, "google")]["last_error"] is None


def test_list_events_parses_times_and_all_day(monkeypatch):
    fake = setup(monkeypatch)
    fake.save_oauth_token(1, "google", "d@x", crypto.encrypt("rt"))
    monkeypatch.setattr(google, "_post", lambda url, **kw: (200, {"access_token": "at"}))
    seen = {}

    def get(url, **kw):
        seen.update(kw.get("params") or {})
        return 200, {"items": [
            {"id": "e1", "summary": "Созвон", "start": {"dateTime": "2026-09-14T08:00:00Z"},
             "end": {"dateTime": "2026-09-14T09:00:00Z"}, "htmlLink": "https://cal/e1", "location": "Zoom"},
            {"id": "e2", "summary": "День рождения", "start": {"date": "2026-09-14"}, "end": {"date": "2026-09-15"}},
            {"id": "e3", "start": {}},
        ]}
    monkeypatch.setattr(google, "_get", get)
    start = datetime(2026, 9, 14, 0, 0, tzinfo=ZoneInfo(TZ))
    events = google.list_events(1, start, datetime(2026, 9, 15, 0, 0, tzinfo=ZoneInfo(TZ)), query="созвон")
    assert seen["q"] == "созвон" and seen["singleEvents"] == "true" and seen["timeMin"].endswith("Z")
    assert [e["summary"] for e in events] == ["Созвон", "День рождения"]
    assert events[0]["start"].hour == 11 and events[0]["location"] == "Zoom"   # 08:00Z → 11:00 Jerusalem
    assert events[1]["all_day"] is True


def test_search_mail_decodes_bodies_and_headers(monkeypatch):
    fake = setup(monkeypatch)
    fake.save_oauth_token(1, "google", "d@x", crypto.encrypt("rt"))
    monkeypatch.setattr(google, "_post", lambda url, **kw: (200, {"access_token": "at"}))
    body_b64 = base64.urlsafe_b64encode("Ваш билет на рейс LY315, 20.09 07:40".encode()).decode().rstrip("=")

    def get(url, **kw):
        if url.endswith("/messages"):
            return 200, {"messages": [{"id": "m1"}]}
        return 200, {"id": "m1", "threadId": "t1", "snippet": "Ваш билет…", "internalDate": "1757000000000",
                     "payload": {"mimeType": "multipart/alternative", "headers": [
                         {"name": "From", "value": "El Al <noreply@elal.com>"}, {"name": "Subject", "value": "E-ticket LY315"}],
                         "parts": [{"mimeType": "text/plain", "body": {"data": body_b64}}]}}
    monkeypatch.setattr(google, "_get", get)
    hits = google.search_mail(1, "билет")
    assert hits[0]["subject"] == "E-ticket LY315" and hits[0]["sender"].startswith("El Al")
    assert hits[0]["body"].startswith("Ваш билет на рейс LY315")
    assert hits[0]["received_at"].tzinfo is timezone.utc and hits[0]["link"].endswith("/m1")


def test_range_for_phrases():
    now = datetime(2026, 9, 10, 7, 0, tzinfo=timezone.utc)   # Thursday 10:00 local
    day = lambda d: datetime(2026, 9, d, 0, 0, tzinfo=ZoneInfo(TZ))
    assert google.range_for("", now, TZ) == (day(10), day(17))
    assert google.range_for("сегодня", now, TZ) == (day(10), day(11))
    assert google.range_for("завтра", now, TZ) == (day(11), day(12))
    assert google.range_for("на этой неделе", now, TZ) == (day(7), day(14))
    assert google.range_for("на следующей неделе", now, TZ) == (day(14), day(21))
    assert google.range_for("в понедельник", now, TZ) == (day(14), day(15))
    assert google.range_for("в четверг", now, TZ) == (day(10), day(11))      # today, not next week
    assert google.range_for("25 сентября", now, TZ) == (day(25), day(26))
    assert google.range_for("когда-нибудь", now, TZ) is None


def test_missing_scopes_are_named(monkeypatch):
    fake = setup(monkeypatch)
    fake.save_oauth_token(1, "google", "d@x", crypto.encrypt("rt"),
                          scopes="https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/userinfo.email")
    assert google.missing_scopes(1) == ["почта"]
    fake.save_oauth_token(1, "google", "d@x", crypto.encrypt("rt"), scopes=" ".join(google.SCOPES))
    assert google.missing_scopes(1) == []
