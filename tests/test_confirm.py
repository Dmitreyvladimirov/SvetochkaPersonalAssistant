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
    (entry,) = result.ctx.pending
    assert entry["kind"] == "confirm" and entry["tool"] == "calendar_create" and len(entry["pid"]) == 12
    assert entry["label"] == "Создать событие: Встреча с Костей — сегодня в 15:00–16:00"
    assert entry["args"]["when"] == "в четверг в 15"
    assert entry["args"]["start_iso"].startswith("2026-09-10T15:00:00")   # pinned once, in describe
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


def test_tap_uses_the_pinned_moment_not_a_reparse(monkeypatch):
    """Review C2: proposed on Thursday 14:00 as 'в четверг в 15', tapped at 15:01 —
    the event still lands today at 15:00, not next Thursday."""
    fake, sent, client = wire(monkeypatch, [
        [("calendar_create", {"title": "Созвон", "when": "в четверг в 15", "duration_min": 60, "description": ""})], "ок"])
    connect(fake, monkeypatch)
    monkeypatch.setattr(calendar_tool, "_now", lambda: datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc))   # 14:00 local
    inserted = []
    monkeypatch.setattr(google, "create_event", lambda uid, title, start, end, description="": inserted.append(start) or {"id": "e"})
    bot.handle_update(msg("поставь созвон в четверг в 15", update_id=1))
    item_id = list(fake.inbox)[0]
    monkeypatch.setattr(calendar_tool, "_now", lambda: datetime(2026, 9, 10, 12, 1, tzinfo=timezone.utc))   # 15:01 local
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"cf:{item_id}:0",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert inserted[0].isoformat().startswith("2026-09-10T15:00:00")


def test_failed_tap_answers_and_stays_tappable(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("calendar_create", {"title": "x", "when": "завтра в 10", "duration_min": 60, "description": ""})], "ок"])
    connect(fake, monkeypatch)
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("network")
        return {"id": "e", "link": ""}
    monkeypatch.setattr(google, "create_event", flaky)
    bot.handle_update(msg("поставь x завтра в 10", update_id=1))
    item_id = list(fake.inbox)[0]
    tap = lambda uid: bot.handle_update({"update_id": uid, "callback_query": {"id": "cb", "data": f"cf:{item_id}:0",
                                         "message": {"message_id": 1, "chat": {"id": 111}}}})
    tap(2)
    assert "Не получилось выполнить" in sent.messages[-1][1]
    tap(3)
    assert sent.messages[-1][1].startswith("Создала событие") and len(calls) == 2
    tap(4)
    assert sent.messages[-1][1] == "Это уже сделано."


def test_two_concurrent_taps_run_the_tool_once(monkeypatch):
    import threading
    fake, sent, client = wire(monkeypatch, [
        [("calendar_create", {"title": "x", "when": "завтра в 10", "duration_min": 60, "description": ""})], "ок"])
    connect(fake, monkeypatch)
    inserted = []
    gate = threading.Barrier(2)

    def slow(*a, **k):
        inserted.append(1)
        return {"id": "e", "link": ""}
    monkeypatch.setattr(google, "create_event", slow)
    bot.handle_update(msg("поставь x завтра в 10", update_id=1))
    item_id = list(fake.inbox)[0]

    def tap(uid):
        gate.wait()
        bot.handle_update({"update_id": uid, "callback_query": {"id": "cb", "data": f"cf:{item_id}:0",
                           "message": {"message_id": 1, "chat": {"id": 111}}}})
    threads = [threading.Thread(target=tap, args=(uid,)) for uid in (2, 3)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(inserted) == 1


def test_calendar_tries_both_spellings_and_then_the_past(monkeypatch):
    """The same one-shot miss mail_search had: an event saved as "Артем" was
    invisible to a search for "Артём", and a question about the past ("когда я был
    у врача") was answered by a forward-only window."""
    from datetime import timedelta
    from sveta.tools import run
    fake = install(monkeypatch)
    connect(fake, monkeypatch)
    monkeypatch.setattr(calendar_tool, "_now", lambda: NOW)
    asked = []

    def list_events(uid, start, end, query="", limit=20):
        behind = end <= NOW          # the past window ends where the forward one starts
        asked.append((query, behind))
        if query == "Артем" and behind:
            return [{"id": "e", "summary": "Обед с Артемом", "start": NOW - timedelta(days=30),
                     "end": None, "link": "", "all_day": False, "location": None}]
        return []
    monkeypatch.setattr(google, "list_events", list_events)
    out = run(REGISTRY, "calendar_query", UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem"),
              ToolContext(), {"period": "", "query": "Артём"})
    assert "Обед с Артемом" in out
    assert asked[0] == ("Артём", False) and ("Артем", False) in asked   # ahead first, both spellings
    assert ("Артём", True) in asked                                     # then behind


def test_calendar_finding_nothing_says_what_it_tried(monkeypatch):
    from sveta.tools import run
    fake = install(monkeypatch)
    connect(fake, monkeypatch)
    monkeypatch.setattr(calendar_tool, "_now", lambda: NOW)
    monkeypatch.setattr(google, "list_events", lambda *a, **k: [])
    out = run(REGISTRY, "calendar_query", UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem"),
              ToolContext(), {"period": "", "query": "стоматолог"})
    assert "No events matching 'стоматолог'" in out and "Tried: стоматолог" in out
    assert "do NOT offer the user a list of guesses" in out


def test_a_plain_period_is_still_one_call(monkeypatch):
    """Broadening is for a named query; "что у меня завтра" must not silently show
    events from six months ago."""
    from sveta.tools import run
    fake = install(monkeypatch)
    connect(fake, monkeypatch)
    monkeypatch.setattr(calendar_tool, "_now", lambda: NOW)
    calls = []
    monkeypatch.setattr(google, "list_events",
                        lambda uid, start, end, query="", limit=20: calls.append(query) or [])
    out = run(REGISTRY, "calendar_query", UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem"),
              ToolContext(), {"period": "завтра", "query": ""})
    assert calls == [""] and "Tried:" not in out


def test_a_period_the_parser_cannot_pin_down_widens_instead_of_erroring(monkeypatch):
    """Reading is free (§6.2): "на этой или на следующей, не помню" used to come
    back as an error and leave the user holding the question (FR-62)."""
    from datetime import timedelta
    from sveta.tools import run
    fake = install(monkeypatch)
    connect(fake, monkeypatch)
    monkeypatch.setattr(calendar_tool, "_now", lambda: NOW)
    monkeypatch.setattr(google, "list_events",
                        lambda uid, start, end, query="", limit=20: [
                            {"id": "e", "summary": "Техосмотр", "start": NOW + timedelta(days=11),
                             "end": None, "link": "", "all_day": False, "location": None}])
    out = run(REGISTRY, "calendar_query", UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem"),
              ToolContext(), {"period": "то ли на этой, то ли на следующей", "query": ""})
    assert not out.startswith("Error")
    assert "Техосмотр" in out and "could not pin down" in out
