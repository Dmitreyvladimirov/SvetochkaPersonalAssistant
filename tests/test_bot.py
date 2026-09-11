"""The update handler: access, idempotency, the cheap path, failure replies,
buttons. Telegram and the model are faked; the handler runs inline."""
from sveta.core import agent, bot, fetch, llm, telegram, transcribe
from tests.fakedb import install
from tests.fakellm import FakeClient


class Sent:
    def __init__(self):
        self.messages = []
        self.edits = []
        self.callbacks = []
        self.markups = []

    def send_message(self, text, chat_id, reply_markup=None):
        self.messages.append((str(chat_id), text, reply_markup))
        return len(self.messages)

    def edit_message(self, message_id, text, chat_id, reply_markup=None):
        self.edits.append((message_id, str(chat_id), text, reply_markup))

    def answer_callback(self, callback_id, text=""):
        self.callbacks.append((callback_id, text))

    def edit_reply_markup(self, message_id, chat_id, reply_markup):
        self.markups.append((message_id, str(chat_id), reply_markup))


def wire(monkeypatch, script=None):
    fake = install(monkeypatch)
    fake.add_user("111")
    sent = Sent()
    for name in ("send_message", "edit_message", "answer_callback", "edit_reply_markup"):
        monkeypatch.setattr(telegram, name, getattr(sent, name))
    client = FakeClient(script or ["ок"])
    monkeypatch.setattr(llm, "client", lambda: client)
    # No network in tests: every URL "fetches" as a titled page unless a test says otherwise.
    monkeypatch.setattr(fetch, "get", lambda url: fetch.FetchResult(url=url, final_url=url, status=200,
                                                                    title="Page title", summary="A summary."))
    monkeypatch.setattr(transcribe, "telegram_voice", lambda file_id: "напомни завтра в девять позвонить маме")
    # Stage-3 integrations are off unless a test turns them on, whatever the shell holds.
    from sveta.core import config as _config
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "NOTION_TOKEN", "NOTION_CLIENT_ID", "NOTION_CLIENT_SECRET"):
        monkeypatch.setattr(_config, name, "")
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
    _, text, markup = sent.messages[-1]
    assert text.startswith("Сохранила ссылку: Page title")
    assert markup["inline_keyboard"][0][0]["callback_data"] == f"undo:{note['id']}"
    assert list(fake.links.values())[0]["http_status"] == 200


def test_bare_url_that_does_not_open_is_recorded_without_a_note(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    monkeypatch.setattr(fetch, "get", lambda url: fetch.FetchResult(url=url, final_url=url, status=404))
    bot.handle_update(msg("https://example.com/gone"))
    assert fake.notes == {} and len(fake.links) == 1
    assert "не открылась" in sent.messages[-1][1] and sent.messages[-1][2] is None


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


def test_undo_button_soft_deletes_and_records_a_correction(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    from sveta.core import db
    nid = db.create_note(1, "wrong place")
    bot.handle_update({"update_id": 9, "callback_query": {"id": "cb1", "data": f"undo:{nid}",
                                                          "message": {"chat": {"id": 111}}}})
    assert fake.notes[nid]["deleted_at"] is not None
    assert fake.corrections[-1]["did"] == "saved as a note: «wrong place»"
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


# --- Stage 2: lists and reminders in the chat --------------------------------

def test_list_reply_carries_checkboxes_and_a_tap_toggles_in_place(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("list_add", {"list_name": "Покупки", "items": ["молоко", "батарейки"]}),
         ("suggest", {"actions": [{"label": "Показать все списки", "instruction": "покажи все списки"}]})],
        "Добавила в покупки.",
    ])
    bot.handle_update(msg("добавь в покупки молоко и батарейки", update_id=1))
    message_id, chat, text, markup = sent.edits[-1]
    rows = markup["inline_keyboard"]
    ids = list(fake.list_items)
    assert rows[0][0] == {"text": "☐ молоко", "callback_data": f"li:{ids[0]}"}
    assert rows[1][0] == {"text": "☐ батарейки", "callback_data": f"li:{ids[1]}"}
    assert rows[2][0]["callback_data"] == f"undo:l:{ids[0]},{ids[1]}"
    assert rows[3][0]["callback_data"].startswith("sg:")

    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"li:{ids[0]}",
                       "message": {"message_id": message_id, "chat": {"id": 111}, "reply_markup": markup}}})
    assert fake.list_items[ids[0]]["checked_at"] is not None
    assert sent.callbacks[-1] == ("cb", "✓")
    _, _, new_markup = sent.markups[-1]
    new_rows = new_markup["inline_keyboard"]
    assert new_rows[0][0]["text"] == "☑ молоко" and new_rows[1][0]["text"] == "☐ батарейки"
    assert [r[0]["callback_data"] for r in new_rows[2:]] == rows[2][0]["callback_data"].split() + [rows[3][0]["callback_data"]]
    assert client.calls == 2  # the tap cost no model call


def test_undo_removes_the_added_list_lines(monkeypatch):
    fake, sent, client = wire(monkeypatch, [[("list_add", {"list_name": "Покупки", "items": ["молоко"]})], "Ок."])
    bot.handle_update(msg("добавь в покупки молоко", update_id=1))
    (iid,) = list(fake.list_items)
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"undo:l:{iid}",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert fake.list_items == {} and "Убрала из списка" in sent.messages[-1][1]
    assert fake.corrections[-1]["did"] == "added 1 list line(s)"


def test_reminder_reply_has_a_cancel_button_that_works(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("reminder_create", {"text": "позвонить в банк", "when": "через 2 часа"})],
        "Напомню через 2 часа.",
    ])
    bot.handle_update(msg("напомни через 2 часа позвонить в банк", update_id=1))
    (rid,) = list(fake.reminders)
    _, _, _, markup = sent.edits[-1]
    assert markup["inline_keyboard"][0][0]["callback_data"] == f"undo:r:{rid}"
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"undo:r:{rid}",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert fake.reminders[rid]["status"] == "cancelled"
    assert sent.messages[-1][1] == "Отменила напоминание: позвонить в банк."
    bot.handle_update({"update_id": 3, "callback_query": {"id": "cb", "data": f"undo:r:{rid}",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert "уже не активно" in sent.messages[-1][1]


def test_checkbox_tap_on_another_users_line_changes_nothing(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    fake.add_user("222")
    lid = fake.get_or_create_list(2, "Секрет")["id"]
    (iid,) = fake.add_list_items(2, lid, ["их строка"])
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"li:{iid}",
                       "message": {"message_id": 1, "chat": {"id": 111}, "reply_markup": {"inline_keyboard": []}}}})
    assert fake.list_items[iid]["checked_at"] is None and sent.markups == []
    assert sent.callbacks[-1][1].startswith("Эта строка")


def test_help_names_the_new_abilities(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    bot.handle_update(msg("/help"))
    text = sent.messages[-1][1]
    assert "голосовые" in text and "напомнить" in text and "списки" in text
    assert "голосовые, напоминания" not in text  # no longer in the "не умею" line


def test_list_undo_cannot_remove_another_registered_users_line(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    fake.add_user("222")
    lid = fake.get_or_create_list(2, "Их список")["id"]
    (iid,) = fake.add_list_items(2, lid, ["их строка"])
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": f"undo:l:{iid}",
                       "message": {"message_id": 1, "chat": {"id": 111}}}})
    assert iid in fake.list_items and "уже убрала" in sent.messages[-1][1]


def test_command_replies_stay_out_of_the_agent_history(monkeypatch):
    """A /connect screen said 'не подключён'; the next agent turn must not see it
    and must call the tool instead of answering from memory."""
    fake, sent, client = wire(monkeypatch, ["ок"])
    bot.handle_update(msg("/connect", update_id=1))
    bot.handle_update(msg("привет", update_id=2))
    bot.handle_update(msg("найди письмо", update_id=3))
    history = client.messages.requests[-1]["messages"]
    assert all("Подключения" not in m["content"] for m in history if isinstance(m["content"], str))
    assert history[0]["content"] == "привет"
