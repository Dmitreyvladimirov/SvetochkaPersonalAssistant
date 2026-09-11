"""The brief: gathered deterministically, ≤10 lines, empty sections absent, one
per user per day, the model only rephrases, a dead credential becomes an alert."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sveta.core import bot, telegram
from sveta.jobs import brief, digest
from tests.fakedb import install
from tests.fakellm import FakeClient
from tests.test_bot import Sent

TZ = "Asia/Jerusalem"
NOW = datetime(2026, 9, 12, 4, 32, tzinfo=timezone.utc)   # 07:32 in Jerusalem (Saturday)


def setup(monkeypatch, *, prefs=None):
    fake = install(monkeypatch)
    fake.add_user("111", tz=TZ)
    for k, v in (prefs or {}).items():
        fake.set_preference(1, k, v)
    sent = Sent()
    for name in ("send_message", "edit_message", "answer_callback", "edit_reply_markup"):
        monkeypatch.setattr(telegram, name, getattr(sent, name))
    return fake, sent


def local(h, m=0, day=12):
    return datetime(2026, 9, day, h, m, tzinfo=ZoneInfo(TZ))


def fill(fake):
    fake.create_reminder(1, "позвонить в банк", local(11), TZ, "k1")
    fake.create_reminder(1, "вечером спорт", local(19), TZ, "k2")
    fake.create_reminder(1, "завтрашнее", local(9, day=13), TZ, "k3")
    rid, _ = fake.create_reminder(1, "оплатить счёт", local(15, day=11), TZ, "k4")
    fake.reminders[rid].update(status="sent", sent_at=local(15, day=11))
    lid = fake.get_or_create_list(1, "Сегодня")["id"]
    fake.add_list_items(1, lid, ["позвонить маме", "забрать посылку"])
    (done,) = fake.add_list_items(1, lid, ["уже сделано"])
    fake.set_list_item_checked(1, done, True)
    sid, _ = fake.add_source(1, "https://blog.example/feed", title="Blog")
    fake.upsert_source_items(1, sid, [
        {"external_id": "n1", "url": "https://blog.example/1", "title": "Fresh", "published_at": NOW - timedelta(hours=2)},
        {"external_id": "n2", "url": "https://blog.example/2", "title": "Old", "published_at": NOW - timedelta(days=3)},
    ])


def test_due_kinds_window():
    prefs = {"brief.time": "07:30", "review.time": "21:00"}
    assert brief.due_kinds(prefs, local(7, 29)) == []
    assert brief.due_kinds(prefs, local(7, 30)) == ["brief"]
    assert brief.due_kinds(prefs, local(7, 44)) == ["brief"]
    assert brief.due_kinds(prefs, local(7, 45)) == []
    assert brief.due_kinds(prefs, local(21, 5)) == ["review"]
    assert brief.due_kinds({"brief.time": "nonsense"}, local(7, 31)) == ["brief"]   # default survives garbage


def test_gather_brief_sections_in_local_time(monkeypatch):
    fake, _ = setup(monkeypatch)
    fill(fake)
    sections = brief.gather(1, "brief", NOW, TZ)
    assert sections["today"] == ["11:00 позвонить в банк", "19:00 вечером спорт"]
    assert sections["overdue"] == ["11.09 оплатить счёт"]
    assert sections["list_today"] == ["☐ позвонить маме", "☐ забрать посылку"]
    assert sections["news"] == ["Fresh — https://blog.example/1"]


def test_empty_sections_are_absent(monkeypatch):
    fake, _ = setup(monkeypatch)
    assert brief.gather(1, "brief", NOW, TZ) == {}
    fake.create_reminder(1, "x", local(11), TZ, "k")
    assert list(brief.gather(1, "brief", NOW, TZ)) == ["today"]
    text = brief.template("brief", {"today": ["11:00 x"]}, {})
    assert text == "Доброе утро, Дима.\nНапоминания сегодня:\n11:00 x"
    assert "Встреч" not in text and "Почитать" not in text


def test_template_never_exceeds_ten_lines():
    sections = {"today": [f"{h:02d}:00 дело {h}" for h in range(8, 20)],
                "overdue": ["11.09 a", "10.09 b"],
                "list_today": [f"☐ {i}" for i in range(6)],
                "news": ["n1 — u1", "n2 — u2", "n3 — u3"]}
    text = brief.template("brief", sections, {"persona.address": "Дима, на ты"})
    lines = text.splitlines()
    assert len(lines) <= 10 and lines[0] == "Доброе утро, Дима."
    assert "Напоминания сегодня:" in lines and any(l.startswith("…и ещё") for l in lines)
    assert brief.template("brief", sections, {"persona.greeting": "no"}).splitlines()[0] == "Напоминания сегодня:"


def test_model_may_only_rephrase_within_ten_lines(monkeypatch):
    fake, _ = setup(monkeypatch)
    sections = {"today": ["11:00 банк"]}
    text, model, cost, alert = brief.compose("brief", sections, {}, 1, client=FakeClient(["Утро! В 11 — банк."]))
    assert text == "Утро! В 11 — банк." and model and cost > 0 and alert is None
    assert fake.llm_calls[-1]["purpose"] == "brief"
    long = "\n".join(f"line {i}" for i in range(12))
    text, model, _, _ = brief.compose("brief", sections, {}, 1, client=FakeClient([long]))
    assert model is None and text.startswith("Доброе утро")


def test_model_failure_falls_back_and_names_a_dead_credential(monkeypatch):
    fake, _ = setup(monkeypatch)

    class Dead:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("Error code: 400 - Your credit balance is too low to access the Anthropic API.")
    text, model, cost, alert = brief.compose("brief", {"today": ["11:00 банк"]}, {}, 1, client=Dead())
    assert model is None and text.startswith("Доброе утро") and "кредит" in alert


def test_send_for_user_is_once_per_day_and_carries_reactions(monkeypatch):
    fake, sent = setup(monkeypatch)
    fill(fake)
    client = FakeClient(["Бриф."])
    assert brief.send_for_user(fake.active_users()[0], "brief", NOW, client=client) == "sent"
    assert brief.send_for_user(fake.active_users()[0], "brief", NOW, client=client) == "already"
    chat, text, markup = sent.messages[-1]
    assert chat == "111" and text == "Бриф."
    (digest_id,) = list(fake.digests)
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == [f"dg:up:{digest_id}", f"dg:down:{digest_id}"]
    assert fake.digests[digest_id]["tg_message_id"] == 1 and fake.digests[digest_id]["payload"]["sections"]["today"]

    bot.handle_update({"update_id": 5, "callback_query": {"id": "cb", "data": f"dg:down:{digest_id}",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert fake.digests[digest_id]["payload"]["reaction"] == "down"


def test_empty_day_sends_nothing_but_closes_the_window(monkeypatch):
    fake, sent = setup(monkeypatch)
    assert brief.send_for_user(fake.active_users()[0], "brief", NOW) == "empty"
    assert sent.messages == [] and list(fake.digests.values())[0]["payload"]["empty"] is True
    assert brief.send_for_user(fake.active_users()[0], "brief", NOW) == "already"


def test_run_respects_each_users_time_and_tz(monkeypatch):
    fake, sent = setup(monkeypatch, prefs={"brief.time": "07:30"})
    fake.add_user("222", tz="Europe/Berlin")   # 06:32 there — not due
    fill(fake)
    results = brief.run(NOW, client=FakeClient(["Бриф."]))
    assert results == {"1:brief": "sent"}
    assert brief.run(NOW, client=FakeClient(["Бриф."])) == {"1:brief": "already"}


def test_review_lists_only_what_is_open(monkeypatch):
    fake, sent = setup(monkeypatch)
    fill(fake)
    evening = datetime(2026, 9, 12, 18, 3, tzinfo=timezone.utc)   # 21:03 local
    sections = brief.gather(1, "review", evening, TZ)
    assert sections["open"] == ["оплатить счёт"] and sections["list_today"] == ["☐ позвонить маме", "☐ забрать посылку"]
    assert brief.run(evening, client=FakeClient(["Обзор."])) == {"1:review": "sent"}
    assert brief.template("review", sections, {}).startswith("Вечерний обзор.\nНе закрыто:")


def test_dry_run_prints_and_writes_nothing(monkeypatch, capsys):
    fake, sent = setup(monkeypatch)
    fill(fake)
    monkeypatch.setattr(digest.config, "validate_secrets", lambda: None)
    assert digest.main(["--dry-run", "--now", NOW.isoformat(), "--force", "brief"]) == 0
    out = capsys.readouterr().out
    assert "--- brief for user 1" in out and "позвонить в банк" in out
    assert fake.digests == {} and sent.messages == []


def test_rss_commands(monkeypatch):
    from sveta.core import fetch
    from tests.test_rss import RSS
    from tests.test_bot import msg, wire
    fake, sent, client = wire(monkeypatch)
    bot.handle_update(msg("/rss", update_id=1))
    assert "Лент пока нет" in sent.messages[-1][1]

    monkeypatch.setattr(fetch, "get_bytes", lambda url: (None, {}, "refused: 10.0.0.1 is not public"))
    bot.handle_update(msg("/rss add http://internal/feed", update_id=2))
    assert "Не смогла открыть" in sent.messages[-1][1] and fake.sources == {}

    monkeypatch.setattr(fetch, "get_bytes", lambda url: (RSS, {}, None))
    bot.handle_update(msg("/rss add https://blog.example/feed", update_id=3))
    assert sent.messages[-1][1].startswith("Добавила ленту «Blog», записей сейчас: 2")
    bot.handle_update(msg("/rss add https://blog.example/feed", update_id=4))
    assert "уже есть" in sent.messages[-1][1]
    bot.handle_update(msg("/rss", update_id=5))
    assert "1. Blog — опрошена" in sent.messages[-1][1]
    bot.handle_update(msg("/rss rm 1", update_id=6))
    assert "Убрала ленту Blog" in sent.messages[-1][1] and fake.sources == {} and fake.source_items == {}
    assert client.calls == 0


def test_dead_credential_is_named_in_the_chat(monkeypatch):
    from sveta.core import agent
    from tests.test_bot import msg, wire
    fake, sent, client = wire(monkeypatch)

    def dead(*a, **k):
        raise RuntimeError("Error code: 400 - Your credit balance is too low to access the Anthropic API.")
    monkeypatch.setattr(agent, "run", dead)
    bot.handle_update(msg("привет"))
    assert "закончился кредит" in sent.edits[-1][2]
