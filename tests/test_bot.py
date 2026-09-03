"""The update handler: access, idempotency, the cheap path, failure replies,
buttons. Telegram and the model are faked; the handler runs inline."""
from sveta.core import agent, bot, llm, telegram
from tests.fakedb import install
from tests.fakellm import FakeClient


class Sent:
    def __init__(self):
        self.messages = []
        self.edits = []
        self.callbacks = []

    def send_message(self, text, chat_id, reply_markup=None):
        self.messages.append((str(chat_id), text, reply_markup))
        return len(self.messages)

    def edit_message(self, message_id, text, chat_id, reply_markup=None):
        self.edits.append((message_id, str(chat_id), text, reply_markup))

    def answer_callback(self, callback_id, text=""):
        self.callbacks.append(callback_id)


def wire(monkeypatch, script=None):
    fake = install(monkeypatch)
    fake.add_user("111")
    sent = Sent()
    for name in ("send_message", "edit_message", "answer_callback"):
        monkeypatch.setattr(telegram, name, getattr(sent, name))
    client = FakeClient(script or ["ок"])
    monkeypatch.setattr(llm, "client", lambda: client)
    return fake, sent, client


def msg(text, chat="111", update_id=1, **extra):
    m = {"message_id": 10, "chat": {"id": int(chat)}, "text": text}
    m.update(extra)
    return {"update_id": update_id, "message": m}


def test_unregistered_chat_gets_silence(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    bot.handle_update(msg("привет", chat="999"))
    assert sent.messages == [] and fake.inbox == {} and client.calls == 0


def test_duplicate_update_costs_nothing_and_answers_once(monkeypatch):
    fake, sent, client = wire(monkeypatch, [[("note_save", {"body": "мысль", "project": "", "url": ""})], "Записала."])
    bot.handle_update(msg("запиши мысль", update_id=42))
    bot.handle_update(msg("запиши мысль", update_id=42))
    assert len(fake.inbox) == 1
    assert client.calls == 2  # one agent turn, not two
    assert len(sent.edits) == 1


def test_command_takes_the_cheap_path(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    bot.handle_update(msg("/ping"))
    assert sent.messages[-1][1] == "Жива."
    assert client.calls == 0
    assert list(fake.inbox.values())[0]["reply_text"] == "Жива."


def test_bare_url_is_saved_without_the_model(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    bot.handle_update(msg("https://www.instagram.com/p/xyz/"))
    assert client.calls == 0
    note = list(fake.notes.values())[0]
    assert note["source"] == "instagram" and note["source_ref"] == "https://www.instagram.com/p/xyz/"
    _, _, markup = sent.messages[-1]
    assert markup["inline_keyboard"][0][0]["callback_data"] == f"undo:{note['id']}"


def test_agent_reply_rewrites_the_placeholder_with_undo_and_suggestions(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("note_save", {"body": "идея", "project": "", "url": ""}),
         ("suggest", {"actions": [{"label": "Показать похожие", "instruction": "покажи похожие заметки"}]})],
        "Записала: идея.",
    ])
    bot.handle_update(msg("запиши: идея", update_id=5))
    assert sent.messages[0][1] == "Приняла, разбираю…"
    message_id, chat, text, markup = sent.edits[-1]
    assert text == "Записала: идея."
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    item_id = list(fake.inbox)[0]
    assert datas == [f"undo:{list(fake.notes)[0]}", f"sg:{item_id}:0"]
    assert fake.inbox[item_id]["suggestions"][0]["label"] == "Показать похожие"


def test_model_failure_still_answers_and_keeps_the_item(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    monkeypatch.setattr(agent, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("anthropic down")))
    bot.handle_update(msg("что-нибудь"))
    assert "модель не ответила" in sent.edits[-1][2]
    assert list(fake.inbox.values())[0]["status"] == "failed"


def test_budget_exceeded_is_said_plainly(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    monkeypatch.setattr(agent, "run", lambda *a, **k: (_ for _ in ()).throw(llm.BudgetExceeded("$1.50")))
    bot.handle_update(msg("что-нибудь"))
    assert "лимит" in sent.edits[-1][2]
    assert list(fake.inbox.values())[0]["status"] == "new"


def test_voice_is_stored_and_refused_honestly(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    bot.handle_update(msg("", voice={"file_id": "f1", "duration": 30}))
    assert list(fake.inbox.values())[0]["kind"] == "voice"
    assert "Голосовые ещё не расшифровываю" in sent.messages[-1][1]
    assert client.calls == 0


def test_undo_button_soft_deletes_and_records_a_correction(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    from sveta.core import db
    nid = db.create_note(1, "wrong place")
    bot.handle_update({"update_id": 9, "callback_query": {"id": "cb1", "data": f"undo:{nid}",
                                                          "message": {"chat": {"id": 111}}}})
    assert fake.notes[nid]["deleted_at"] is not None
    assert fake.corrections[-1]["did"] == f"saved note #{nid}"
    assert "Убрала" in sent.messages[-1][1]


def test_undo_from_another_chat_is_ignored(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    from sveta.core import db
    nid = db.create_note(1, "mine")
    bot.handle_update({"update_id": 9, "callback_query": {"id": "cb1", "data": f"undo:{nid}",
                                                          "message": {"chat": {"id": 999}}}})
    assert fake.notes[nid]["deleted_at"] is None and sent.messages == []


def test_suggestion_tap_runs_the_instruction_as_a_new_item(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("suggest", {"actions": [{"label": "Список мест", "instruction": "составь список мест для поездки"}]})],
        "Рейс в 07:40.",
        "Вот список мест.",
    ])
    bot.handle_update(msg("когда самолёт?", update_id=1))
    item_id = list(fake.inbox)[0]
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb2", "data": f"sg:{item_id}:0",
                                                          "message": {"chat": {"id": 111}}}})
    assert client.messages.requests[-1]["messages"][-1]["content"] == "составь список мест для поездки"
    assert list(fake.inbox.values())[1]["kind"] == "suggestion"
    assert sent.edits[-1][2] == "Вот список мест."


def test_stale_suggestion_button_is_explained(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb2", "data": "sg:12345:0",
                                                          "message": {"chat": {"id": 111}}}})
    assert "устарела" in sent.messages[-1][1]
