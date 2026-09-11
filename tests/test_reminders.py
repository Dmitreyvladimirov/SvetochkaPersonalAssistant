"""reminder_create parses by code, refuses the past and duplicates; list and cancel
are scoped to the user."""
from datetime import datetime, timezone

from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, run, reminders
from tests.fakedb import install

NOW = datetime(2026, 9, 10, 7, 0, tzinfo=timezone.utc)  # 10:00 in Jerusalem, Thursday


def scope(uid=1):
    return UserScope(user_id=uid, chat_id="111", tz="Asia/Jerusalem")


def freeze(monkeypatch):
    monkeypatch.setattr(reminders, "_now", lambda: NOW)


def test_create_parses_the_phrase_and_echoes_local_time(monkeypatch):
    fake = install(monkeypatch)
    freeze(monkeypatch)
    ctx = ToolContext(inbox_item_id=3)
    out = run(REGISTRY, "reminder_create", scope(), ctx, {"text": "позвонить в банк", "when": "в четверг в 11"})
    assert out.startswith("Created reminder #")
    assert "сегодня в 11:00" in out and "Asia/Jerusalem" in out
    row = list(fake.reminders.values())[0]
    assert row["fire_at"].astimezone(timezone.utc) == datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
    assert row["text"] == "позвонить в банк" and ctx.created_reminder_ids == [row["id"]]


def test_past_is_refused_and_nothing_created(monkeypatch):
    fake = install(monkeypatch)
    freeze(monkeypatch)
    out = run(REGISTRY, "reminder_create", scope(), ToolContext(), {"text": "x", "when": "сегодня в 9"})
    assert out.startswith("Error") and "past" in out
    assert fake.reminders == {}


def test_unparseable_time_asks_to_rephrase(monkeypatch):
    fake = install(monkeypatch)
    freeze(monkeypatch)
    out = run(REGISTRY, "reminder_create", scope(), ToolContext(), {"text": "x", "when": "когда-нибудь"})
    assert out.startswith("Error: could not understand")
    assert fake.reminders == {}


def test_duplicate_is_reported_not_doubled(monkeypatch):
    fake = install(monkeypatch)
    freeze(monkeypatch)
    ctx = ToolContext()
    run(REGISTRY, "reminder_create", scope(), ctx, {"text": "Позвонить в банк", "when": "завтра в 11"})
    out = run(REGISTRY, "reminder_create", scope(), ctx, {"text": "позвонить в банк", "when": "завтра в 11:00"})
    assert out.startswith("Already exists")
    assert len(fake.reminders) == 1 and len(ctx.created_reminder_ids) == 1


def test_list_and_cancel(monkeypatch):
    install(monkeypatch)
    freeze(monkeypatch)
    ctx = ToolContext()
    run(REGISTRY, "reminder_create", scope(), ctx, {"text": "б", "when": "завтра в 11"})
    run(REGISTRY, "reminder_create", scope(), ctx, {"text": "а", "when": "через час"})
    out = run(REGISTRY, "reminder_list", scope(), ctx, {})
    assert out.startswith("2 scheduled") and out.index(": а") < out.index(": б")
    rid = ctx.created_reminder_ids[0]
    assert run(REGISTRY, "reminder_cancel", scope(), ctx, {"reminder_id": rid}).startswith("Cancelled")
    assert run(REGISTRY, "reminder_cancel", scope(), ctx, {"reminder_id": rid}) == f"Reminder #{rid} is already cancelled."
    assert "1 scheduled" in run(REGISTRY, "reminder_list", scope(), ctx, {})


def test_reminders_never_cross_users(monkeypatch):
    install(monkeypatch)
    freeze(monkeypatch)
    ctx = ToolContext()
    run(REGISTRY, "reminder_create", scope(1), ctx, {"text": "secret", "when": "завтра в 11"})
    rid = ctx.created_reminder_ids[0]
    assert run(REGISTRY, "reminder_list", scope(2), ToolContext(), {}) == "No scheduled reminders."
    assert run(REGISTRY, "reminder_cancel", scope(2), ToolContext(), {"reminder_id": rid}).startswith("Error")
    assert "1 scheduled" in run(REGISTRY, "reminder_list", scope(1), ToolContext(), {})
