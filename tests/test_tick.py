"""FR-20/21: the tick sends what is due, once, and leaves the rest; a failed send
stays scheduled; the buttons under a delivered reminder change its state."""
from datetime import datetime, timedelta, timezone

from sveta.core import bot, config, telegram
from sveta.jobs import reminder_tick
from tests.fakedb import install
from tests.test_bot import Sent


def setup(monkeypatch):
    fake = install(monkeypatch)
    fake.add_user("111")
    sent = Sent()
    for name in ("send_message", "edit_message", "answer_callback", "edit_reply_markup"):
        monkeypatch.setattr(telegram, name, getattr(sent, name))
    now = datetime.now(timezone.utc)
    fake.create_reminder(1, "позвонить в банк", now - timedelta(seconds=5), "Asia/Jerusalem", "k1")
    fake.create_reminder(1, "потом", now + timedelta(hours=1), "Asia/Jerusalem", "k2")
    # add_user took id 1; the two reminders are 2 and 3 — tests refer to them by name
    fake.due, fake.later = 2, 3
    return fake, sent


def test_due_reminder_is_sent_once_with_buttons(monkeypatch):
    fake, sent = setup(monkeypatch)
    assert reminder_tick.tick() == 1
    assert reminder_tick.tick() == 0
    chat, text, markup = sent.messages[-1]
    assert chat == "111" and text == "⏰ позвонить в банк"
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == ["rm:done:2", "rm:snooze:2", "rm:tomorrow:2"]
    assert fake.reminders[fake.due]["status"] == "sent" and fake.reminders[fake.later]["status"] == "scheduled"


def test_failed_send_stays_scheduled(monkeypatch):
    fake, sent = setup(monkeypatch)
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: None)
    assert reminder_tick.tick() == 0
    assert fake.reminders[fake.due]["status"] == "scheduled"


def test_start_is_disabled_by_zero_period(monkeypatch):
    monkeypatch.setattr(config, "TICK_SECONDS", 0)
    assert reminder_tick.start() is False and reminder_tick.alive() is False


def test_buttons_under_a_delivered_reminder(monkeypatch):
    fake, sent = setup(monkeypatch)
    reminder_tick.tick()

    def tap(data):
        bot.handle_update({"update_id": 99, "callback_query": {"id": "cb", "data": data,
                           "message": {"message_id": 1, "chat": {"id": 111}, "text": "⏰ позвонить в банк"}}})

    tap("rm:snooze:2")
    assert fake.reminders[fake.due]["status"] == "scheduled"
    assert fake.reminders[fake.due]["fire_at"] > datetime.now(timezone.utc) + timedelta(minutes=55)
    assert "напомню" in sent.edits[-1][2]

    tap("rm:tomorrow:2")
    fire_at = fake.reminders[fake.due]["fire_at"]
    assert fire_at.hour == 9 and fire_at.minute == 0 and fire_at.date() > datetime.now(fire_at.tzinfo).date()

    tap("rm:done:2")
    assert fake.reminders[fake.due]["status"] == "done" and sent.edits[-1][2] == "✓ позвонить в банк"

    tap("rm:done:3")  # another reminder, still scheduled — done is allowed too
    assert fake.reminders[fake.later]["status"] == "done"


def test_reminder_buttons_from_another_chat_are_ignored(monkeypatch):
    fake, sent = setup(monkeypatch)
    bot.handle_update({"update_id": 99, "callback_query": {"id": "cb", "data": "rm:done:2",
                       "message": {"message_id": 1, "chat": {"id": 999}, "text": "x"}}})
    assert fake.reminders[fake.due]["status"] == "scheduled" and sent.messages == []


def test_reminder_buttons_cannot_touch_another_registered_users_row(monkeypatch):
    """NFR-10 at the bot level: user 222 owns the row, user 111 taps."""
    fake, sent = setup(monkeypatch)
    fake.add_user("222")
    rid, _ = fake.create_reminder(2, "их напоминание", datetime.now(timezone.utc), "UTC", "theirs")
    for data in (f"rm:done:{rid}", f"rm:snooze:{rid}", f"undo:r:{rid}"):
        bot.handle_update({"update_id": 99, "callback_query": {"id": "cb", "data": data,
                           "message": {"message_id": 1, "chat": {"id": 111}, "text": "x"}}})
    assert fake.reminders[rid]["status"] == "scheduled"
    assert all("не найдено" in m[1] or "не активно" in m[1] for m in sent.messages)


def test_finished_reminder_is_not_resurrected_by_snooze(monkeypatch):
    fake, sent = setup(monkeypatch)
    fake.reminders[fake.due]["status"] = "done"
    bot.handle_update({"update_id": 99, "callback_query": {"id": "cb", "data": f"rm:snooze:{fake.due}",
                       "message": {"message_id": 1, "chat": {"id": 111}, "text": "x"}}})
    assert fake.reminders[fake.due]["status"] == "done" and "закрыто" in sent.messages[-1][1]
