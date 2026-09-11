"""FR-13: a proposal writes nothing; the tap saves everything by code; a second
tap is idempotent; the proposal survives on the inbox row."""
from sveta.core import bot
from tests.test_bot import msg, wire

PROPOSAL = [("notes_propose", {"items": [
    {"body": "онбординг переделать под шаблоны", "project": ""},
    {"body": "реферальную программу отложить", "project": "Vespera"},
    {"body": "нанять второго саппорта", "project": ""},
]})]


def test_proposal_is_shown_with_one_button_and_nothing_is_saved(monkeypatch):
    fake, sent, client = wire(monkeypatch, [PROPOSAL, "Вот что я вижу:\n1. …\n2. …\n3. …"])
    bot.handle_update(msg("мысли после созвона: …", update_id=1))
    assert fake.notes == {}
    message_id, chat, text, markup = sent.edits[-1]
    item_id = list(fake.inbox)[0]
    assert markup["inline_keyboard"] == [[{"text": "✓ Сохранить все 3", "callback_data": f"dp:{item_id}"}]]
    stored = fake.inbox[item_id]["suggestions"]
    assert [e["kind"] for e in stored] == ["note", "note", "note"]


def test_tap_saves_all_records_once(monkeypatch):
    fake, sent, client = wire(monkeypatch, [PROPOSAL, "Список выше."])
    bot.handle_update(msg("дамп", update_id=1))
    item_id = list(fake.inbox)[0]
    tap = {"update_id": 2, "callback_query": {"id": "cb", "data": f"dp:{item_id}",
                                              "message": {"message_id": 1, "chat": {"id": 111}}}}
    bot.handle_update(tap)
    assert [n["body"] for n in fake.notes.values()] == ["онбординг переделать под шаблоны",
                                                         "реферальную программу отложить", "нанять второго саппорта"]
    assert list(fake.notes.values())[1]["project"] == "Vespera"
    chat, text, markup = sent.messages[-1]
    assert text == "Сохранила 3 заметки."
    assert markup["inline_keyboard"][0][0]["callback_data"] == f"undo:{list(fake.notes)[-1]}"
    assert client.calls == 2  # the tap cost no model call

    bot.handle_update(dict(tap, update_id=3))
    assert len(fake.notes) == 3 and sent.messages[-1][1] == "Эти заметки уже сохранила."


def test_proposal_and_suggestions_coexist(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [PROPOSAL[0], ("suggest", {"actions": [{"label": "Показать проект", "instruction": "покажи заметки по Vespera"}]})],
        "Ок.",
    ])
    bot.handle_update(msg("дамп", update_id=1))
    item_id = list(fake.inbox)[0]
    _, _, _, markup = sent.edits[-1]
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert datas == [f"dp:{item_id}", f"sg:{item_id}:0"]
    # The suggestion button still resolves to the instruction, not to a note entry.
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"sg:{item_id}:0",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert client.messages.requests[-1]["messages"][-1]["content"] == "покажи заметки по Vespera"


def test_stale_or_foreign_proposal_button(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": "dp:999",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert "устарела" in sent.messages[-1][1] and fake.notes == {}


def test_one_item_proposal_is_refused():
    from sveta.core.scope import ToolContext, UserScope
    from sveta.tools import REGISTRY, run
    ctx = ToolContext()
    out = run(REGISTRY, "notes_propose", UserScope(user_id=1, chat_id="1", tz="UTC"), ctx,
              {"items": [{"body": "одна мысль", "project": ""}, {"body": " ", "project": ""}]})
    assert out.startswith("Error") and ctx.proposal == []
