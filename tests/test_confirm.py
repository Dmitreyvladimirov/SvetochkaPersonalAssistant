"""§6.2: a gated tool never runs from the loop; the card's tap runs it once by
code. calendar_create and mail_read_body are the two gated tools."""
from datetime import datetime, timezone

from sveta.core import agent, bot, config, crypto, google
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, calendar as calendar_tool
from tests.fakedb import install
from tests.fakellm import FakeClient
from tests.test_bot import msg, wire

NOW = datetime(2026, 9, 10, 7, 0, tzinfo=timezone.utc)


def connect(fake, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "cs")
    fake.save_oauth_token(1, "google", "d@x", crypto.encrypt("rt"))
    monkeypatch.setattr(google, "_post", lambda url, **kw: (200, {"access_token": "at"}))
    monkeypatch.setattr(calendar_tool, "_now", lambda: NOW)


def test_loop_records_the_call_and_does_not_run_it(monkeypatch):
    fake = install(monkeypatch)
    connect(fake, monkeypatch)
    inserted = []
    monkeypatch.setattr(google, "create_event", lambda *a, **k: inserted.append(a) or {"id": "x", "link": "l"})
    client = FakeClient([[("calendar_create", {"title": "Встреча с Костей", "when": "в четверг в 15",
                                               "duration_min": 60, "description": ""})],
                         "Предлагаю встречу в четверг в 15:00 — подтверди кнопкой."])
    scope = UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem")
    result = agent.run(scope, "поставь встречу с Костей в четверг в 15", client=client)
    assert inserted == []
    assert result.ctx.pending == [{"kind": "confirm", "tool": "calendar_create",
                                   "args": {"title": "Встреча с Костей", "when": "в четверг в 15", "duration_min": 60, "description": ""},
                                   "label": "Создать событие: Встреча с Костей — сегодня в 15:00–16:00"}]
    tool_result = client.messages.requests[1]["messages"][-1]["content"][0]["content"]
    assert tool_result.startswith("Proposed, waiting for the user's tap")


def test_bad_arguments_are_refused_before_the_card(monkeypatch):
    fake = install(monkeypatch)
    connect(fake, monkeypatch)
    client = FakeClient([[("calendar_create", {"title": "x", "when": "вчера в 10", "duration_min": 60, "description": ""})], "ок"])
    result = agent.run(UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem"), "…", client=client)
    assert result.ctx.pending == []
    assert "in the past" in client.messages.requests[1]["messages"][-1]["content"][0]["content"]


def test_tap_creates_the_event_once(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("calendar_create", {"title": "Встреча с Костей", "when": "в четверг в 15", "duration_min": 30, "description": ""})],
        "Подтверди кнопкой.",
    ])
    connect(fake, monkeypatch)
    inserted = []
    monkeypatch.setattr(google, "create_event", lambda uid, title, start, end, description="": inserted.append((title, start, end)) or {"id": "ev1", "link": "https://cal/ev1"})
    bot.handle_update(msg("поставь встречу с Костей в четверг в 15", update_id=1))
    item_id = list(fake.inbox)[0]
    _, _, _, markup = sent.edits[-1]
    assert markup["inline_keyboard"][0][0] == {"text": "✓ Создать событие: Встреча с Костей — сегодня в 15:00–15:30",
                                               "callback_data": f"cf:{item_id}:0"}
    assert inserted == []

    tap = {"update_id": 2, "callback_query": {"id": "cb", "data": f"cf:{item_id}:0",
                                              "message": {"message_id": 1, "chat": {"id": 111}}}}
    bot.handle_update(tap)
    assert len(inserted) == 1 and inserted[0][0] == "Встреча с Костей"
    assert sent.messages[-1][1].startswith("Создала событие: Встреча с Костей") and "https://cal/ev1" in sent.messages[-1][1]
    bot.handle_update(tap)                                   # redelivery: claimed update_id
    bot.handle_update(dict(tap, update_id=3))                # a real second tap
    assert len(inserted) == 1 and sent.messages[-1][1] == "Это уже сделано."


def test_tap_from_another_user_does_nothing(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("calendar_create", {"title": "x", "when": "завтра в 10", "duration_min": 60, "description": ""})], "ок"])
    connect(fake, monkeypatch)
    fake.add_user("222")
    inserted = []
    monkeypatch.setattr(google, "create_event", lambda *a, **k: inserted.append(1) or {})
    bot.handle_update(msg("поставь x завтра в 10", update_id=1))
    item_id = list(fake.inbox)[0]
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"cf:{item_id}:0",
                       "message": {"message_id": 1, "chat": {"id": 222}}}})
    assert inserted == [] and "устарела" in sent.messages[-1][1]


def test_not_connected_is_said_plainly(monkeypatch):
    install(monkeypatch)
    monkeypatch.setattr(calendar_tool, "_now", lambda: NOW)
    from sveta.tools import run
    out = run(REGISTRY, "calendar_query", UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem"),
              ToolContext(), {"period": "завтра", "query": ""})
    assert out.startswith("Error: Google не подключён — /google")
