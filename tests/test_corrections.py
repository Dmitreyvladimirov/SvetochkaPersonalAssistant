"""FR-15: after "Не туда" the next message is the 'should have' half, the
message is still processed, and completed pairs reach the prompt."""
from datetime import timedelta

from sveta.core import agent, bot
from sveta.core.scope import UserScope
from tests.test_bot import msg, wire


def test_next_message_fills_the_correction_and_is_still_processed(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("note_save", {"body": "молоко и батарейки", "project": "", "url": ""})], "Записала.",
        [("list_add", {"list_name": "Покупки", "items": ["молоко", "батарейки"]})], "Добавила в покупки.",
    ])
    bot.handle_update(msg("молоко и батарейки", update_id=1))
    (nid,) = list(fake.notes)
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"undo:{nid}",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert fake.corrections[-1]["should_have"] is None

    bot.handle_update(msg("это был список покупок", update_id=3))
    assert fake.corrections[-1]["should_have"] == "это был список покупок"
    assert len(fake.list_items) == 2                       # the message was also acted on
    assert client.calls == 4

    # A later message does not overwrite a filled correction.
    bot.handle_update(msg("ещё что-то", update_id=4))
    assert fake.corrections[-1]["should_have"] == "это был список покупок"


def test_open_correction_expires(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    fake.add_correction(1, None, "saved note #1")
    fake.corrections[-1]["created_at"] -= timedelta(minutes=20)
    bot.handle_update(msg("поздно", update_id=1))
    assert fake.corrections[-1]["should_have"] is None


def test_completed_pairs_reach_the_prompt_and_incomplete_do_not(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    fake.add_correction(1, None, "saved note #1", "should have been a list line")
    fake.add_correction(1, None, "saved note #2")
    prompt = agent.system_prompt(UserScope(user_id=1, chat_id="111", tz="UTC"))
    assert "сделала: saved note #1 → надо было: should have been a list line" in prompt
    assert "saved note #2" not in prompt
    assert "поправки" not in agent.system_prompt(UserScope(user_id=2, chat_id="222", tz="UTC"))


def test_commands_links_and_later_messages_do_not_fill_the_correction(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    fake.add_correction(1, None, "saved as a note: «x»")
    bot.handle_update(msg("/ping", update_id=1))
    bot.handle_update(msg("https://example.com/", update_id=2))
    assert fake.corrections[-1]["should_have"] is None
    # A normal message now: another item (the URL) already arrived since the undo,
    # so this one is not "the next message" and must not be taken as the answer.
    bot.handle_update(msg("это был список", update_id=3))
    assert fake.corrections[-1]["should_have"] is None


def test_undo_records_the_note_body_not_an_id(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    from sveta.core import db
    nid = db.create_note(1, "молоко и батарейки, много\nстрок")
    bot.handle_update({"update_id": 9, "callback_query": {"id": "cb1", "data": f"undo:{nid}",
                                                          "message": {"chat": {"id": 111}}}})
    assert fake.corrections[-1]["did"] == "saved as a note: «молоко и батарейки, много строк»"


def test_a_voice_answer_fills_the_correction_too(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    fake.add_correction(1, None, "saved as a note: «x»")
    from tests.test_voice import voice
    bot.handle_update(voice(5))   # wire() transcribes to a reminder phrase
    assert fake.corrections[-1]["should_have"] == "напомни завтра в девять позвонить маме"
