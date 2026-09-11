"""FR-14: the showcase mirrors notes and never blocks the Postgres save."""
import threading

from sveta.core import bot, config, notion
from tests.test_bot import msg, wire


class InlineThread:
    """Runs the mirror synchronously so the test can assert on it."""
    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def test_database_id_from_link():
    assert notion.database_id_from_link("https://www.notion.so/dima/604162747642431eac01284c1faa855c?v=abc") == \
        "60416274-7642-431e-ac01-284c1faa855c"
    assert notion.database_id_from_link("60416274-7642-431e-ac01-284c1faa855c") == "60416274-7642-431e-ac01-284c1faa855c"
    assert notion.database_id_from_link("https://www.notion.so/dima/Page-title") is None


def test_notion_command_oauth_flow_and_database_pick(monkeypatch):
    from sveta.core import crypto
    fake, sent, client = wire(monkeypatch)
    for name in ("NOTION_TOKEN", "NOTION_CLIENT_ID", "NOTION_CLIENT_SECRET"):
        monkeypatch.setattr(config, name, "")          # whatever the developer's shell holds
    bot.handle_update(msg("/notion", update_id=1))
    assert "не настроен" in sent.messages[-1][1]

    monkeypatch.setattr(config, "NOTION_CLIENT_ID", "nid")
    monkeypatch.setattr(config, "NOTION_CLIENT_SECRET", "nsecret")
    monkeypatch.setattr(config, "PUBLIC_DOMAIN", "sveta.example")
    bot.handle_update(msg("/notion", update_id=2))
    link = sent.messages[-1][1]
    assert "api.notion.com/v1/oauth/authorize" in link and "state=1." in link
    assert "redirect_uri=https%3A%2F%2Fsveta.example%2Foauth%2Fnotion%2Fcallback" in link

    # The consent lands a token; then /notion <link> checks the database with that token.
    fake.save_oauth_token(1, "notion", "Dima's workspace", crypto.encrypt("ntn_secret"))
    seen = {}

    def get(path, token):
        seen["token"] = token
        return (404, {}) if "bad" in seen else (200, {"title": [{"plain_text": "Светочка · Заметки"}]})
    monkeypatch.setattr(notion, "_get", get)
    seen["bad"] = True
    bot.handle_update(msg("/notion https://www.notion.so/d/604162747642431eac01284c1faa855c", update_id=3))
    assert "не расшарена" in sent.messages[-1][1] and (1, "notion.notes_db") not in fake.prefs
    del seen["bad"]
    bot.handle_update(msg("/notion https://www.notion.so/d/604162747642431eac01284c1faa855c", update_id=4))
    assert "Витрина подключена: «Светочка · Заметки»" in sent.messages[-1][1]
    assert fake.prefs[(1, "notion.notes_db")] == "60416274-7642-431e-ac01-284c1faa855c"
    assert seen["token"] == "ntn_secret"                     # the user's own token, decrypted


def test_oauth_callback_picks_the_single_shared_database(monkeypatch):
    from fastapi.testclient import TestClient
    from sveta.core import app as app_module, crypto, db, telegram
    from tests.fakedb import install
    fake = install(monkeypatch)
    fake.add_user("111")
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "seed_users", lambda chat_ids, tz: 0)
    monkeypatch.setattr(config, "NOTION_CLIENT_ID", "nid")
    monkeypatch.setattr(config, "NOTION_CLIENT_SECRET", "nsecret")
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda text, chat_id, reply_markup=None: sent.append(text) or 1)
    monkeypatch.setattr(notion, "_post_basic", lambda path, payload: (200, {"access_token": "ntn_x", "workspace_name": "Dima"}))
    monkeypatch.setattr(notion, "_post", lambda path, payload, token: (200, {"results": [
        {"id": "60416274-7642-431e-ac01-284c1faa855c", "title": [{"plain_text": "Светочка · Заметки"}]}]}))
    with TestClient(app_module.app) as client:
        r = client.get("/oauth/notion/callback", params={"state": notion.state_for(1), "code": "c"})
    assert r.status_code == 200
    assert crypto.decrypt(fake.oauth[(1, "notion")]["refresh_token"]) == "ntn_x"
    assert fake.prefs[(1, "notion.notes_db")] == "60416274-7642-431e-ac01-284c1faa855c"
    assert sent[-1].startswith("Notion подключён (Dima). Витрина: «Светочка · Заметки»")


def test_note_is_mirrored_and_a_notion_failure_keeps_the_note(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("note_save", {"body": "мысль про онбординг", "project": "Vespera", "url": ""})], "Записала.",
        [("note_save", {"body": "вторая", "project": "", "url": ""})], "Записала.",
    ])
    monkeypatch.setattr(config, "NOTION_TOKEN", "secret_x")
    fake.set_preference(1, "notion.notes_db", "60416274-7642-431e-ac01-284c1faa855c")
    monkeypatch.setattr(bot.threading, "Thread", InlineThread)
    posted = []
    monkeypatch.setattr(notion, "_post", lambda path, payload, token: posted.append((path, payload, token)) or (200, {"id": "page-1"}))
    bot.handle_update(msg("запиши мысль про онбординг для Vespera", update_id=1))
    (nid,) = list(fake.notes)
    assert fake.notes[nid]["notion_page_id"] == "page-1"
    path, payload, token = posted[0]
    assert path == "/pages" and token == "secret_x"      # the fallback token when no OAuth row exists
    assert payload["parent"] == {"database_id": "60416274-7642-431e-ac01-284c1faa855c"}
    props = payload["properties"]
    assert props["Name"]["title"][0]["text"]["content"] == "мысль про онбординг"
    assert props["Project"]["rich_text"][0]["text"]["content"] == "Vespera" and props["Note ID"]["number"] == nid

    monkeypatch.setattr(notion, "_post", lambda path, payload, token: (500, {"message": "boom"}))
    bot.handle_update(msg("вторая", update_id=2))
    assert len(fake.notes) == 2 and fake.notes[list(fake.notes)[1]].get("notion_page_id") is None
    assert sent.edits[-1][2] == "Записала."


def test_no_mirror_without_a_database(monkeypatch):
    fake, sent, client = wire(monkeypatch, [[("note_save", {"body": "x", "project": "", "url": ""})], "Записала."])
    monkeypatch.setattr(config, "NOTION_TOKEN", "secret_x")
    monkeypatch.setattr(bot.threading, "Thread", InlineThread)
    monkeypatch.setattr(notion, "_post", lambda path, payload, token: (_ for _ in ()).throw(AssertionError("must not post")))
    bot.handle_update(msg("x", update_id=1))
    assert len(fake.notes) == 1


def test_connect_screen_has_url_buttons_and_states(monkeypatch):
    from sveta.core import crypto, google
    fake, sent, client = wire(monkeypatch)
    bot.handle_update(msg("/connect", update_id=1))
    text, markup = sent.messages[-1][1], sent.messages[-1][2]
    assert "Google (календарь, почта) — не настроен на сервере" in text and markup is None

    for name, value in (("GOOGLE_CLIENT_ID", "g"), ("GOOGLE_CLIENT_SECRET", "s"),
                        ("NOTION_CLIENT_ID", "n"), ("NOTION_CLIENT_SECRET", "s"), ("PUBLIC_DOMAIN", "sveta.example")):
        monkeypatch.setattr(config, name, value)
    bot.handle_update(msg("/connect", update_id=2))
    text, markup = sent.messages[-1][1], sent.messages[-1][2]
    assert "Google (календарь, почта) — не подключён" in text and "Notion (копии заметок) — не подключён" in text
    buttons = [b for row in markup["inline_keyboard"] for b in row]
    assert buttons[0]["text"] == "Подключить Google" and buttons[0]["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert buttons[1]["text"] == "Подключить Notion" and buttons[1]["url"].startswith("https://api.notion.com/v1/oauth/authorize?")
    assert "callback_data" not in buttons[0]

    fake.save_oauth_token(1, "google", "dima@example.com", crypto.encrypt("rt"))
    bot.handle_update(msg("/start", update_id=3))
    text = sent.messages[-1][1]
    assert "Я Светочка" in text and "Google — подключён (dima@example.com)" in text
