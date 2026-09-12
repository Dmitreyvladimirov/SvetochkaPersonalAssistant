"""FR-59…FR-61: a photo or a document is intake, its text is read once by the
cheap model, and the file itself comes back on request."""
import base64
import logging
from types import SimpleNamespace

from sveta.core import bot, config, db, files, llm, telegram
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, run
from tests.fakedb import install
from tests.test_bot import msg, wire

RECEIPT = "Чек Super-Pharm 12.09.2026, 87.40 ₪, карта ****1234"


def photo(update_id=1, caption=None, size=120_000):
    m = {"message_id": 10, "chat": {"id": 111},
         "photo": [{"file_id": "small", "file_unique_id": "u1", "file_size": 900},
                   {"file_id": "big", "file_unique_id": "u2", "file_size": size}]}
    if caption:
        m["caption"] = caption
    return {"update_id": update_id, "message": m}


def document(update_id=1, name="ticket.pdf", mime="application/pdf", size=30_000, caption=None):
    m = {"message_id": 11, "chat": {"id": 111},
         "document": {"file_id": "doc1", "file_unique_id": "u3", "file_name": name,
                      "mime_type": mime, "file_size": size}}
    if caption:
        m["caption"] = caption
    return {"update_id": update_id, "message": m}


def vision(monkeypatch, text=RECEIPT, capture=None):
    """The cheap model, scripted. It carries its own provider like every double in
    these tests, so the assertions are about what the pipeline sent, not about any
    one house's wire format (those are checked in tests/test_providers.py)."""
    from tests.fakellm import FakeClient
    cheap = FakeClient([text])
    if capture is not None:
        original = cheap.messages.next_reply

        def watched(kw):
            capture.update(kw)
            return original(kw)
        cheap.messages.next_reply = watched
    monkeypatch.setattr(files, "_client", lambda: cheap)
    monkeypatch.setattr(files, "_download", lambda file_id: (b"\xff\xd8jpegbytes", "photos/1.jpg"))
    return cheap


def test_kind_of_reads_photos_and_documents():
    assert files.kind_of({"text": "x"}) is None
    kind, meta = files.kind_of(photo()["message"])
    assert kind == "photo" and meta["file_id"] == "big" and meta["mime"] == "image/jpeg"
    kind, meta = files.kind_of(document()["message"])
    assert kind == "document" and meta["name"] == "ticket.pdf" and meta["mime"] == "application/pdf"


def test_photo_without_a_caption_is_read_and_filed(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("note_save", {"body": "Чек Super-Pharm 12.09.2026, 87.40 ₪", "project": "", "url": ""})],
        "Сохранила чек Super-Pharm на 87.40 ₪.",
    ])
    seen = {}
    vision(monkeypatch, capture=seen)
    bot.handle_update(photo())
    item = list(fake.inbox.values())[0]
    assert item["kind"] == "photo" and item["file_id"] == "big" and item["transcript"] == RECEIPT
    assert seen["model"] == config.VISION_MODEL      # never the agent model
    assert config.VISION_MODEL != config.AGENT_MODEL
    block = seen["messages"][0]["content"][0]
    assert block["type"] == "file" and block["media_type"] == "image/jpeg"
    turn = client.messages.requests[0]["messages"][0]["content"]
    assert "[фото «photo.jpg»]" in turn and "<<<файл" in turn and RECEIPT in turn
    assert sent.messages[0][1] == "Смотрю файл…"
    assert sent.edits[-1][2] == "Сохранила чек Super-Pharm на 87.40 ₪."
    assert fake.llm_calls[0]["purpose"] == "vision"


def test_photo_with_a_caption_keeps_both(monkeypatch):
    fake, sent, client = wire(monkeypatch, [[("note_save", {"body": "чек из аптеки", "project": "", "url": ""})], "Ок."])
    vision(monkeypatch)
    bot.handle_update(photo(caption="вот чек из аптеки, для налоговой"))
    turn = client.messages.requests[0]["messages"][0]["content"]
    assert turn.startswith("вот чек из аптеки, для налоговой") and RECEIPT in turn


def test_pdf_goes_to_the_model_as_a_file_not_as_text(monkeypatch):
    fake, sent, client = wire(monkeypatch, [[("note_save", {"body": "билет", "project": "", "url": ""})], "Ок."])
    seen = {}
    vision(monkeypatch, text="Boarding pass LY315 20.09", capture=seen)
    bot.handle_update(document())
    block = seen["messages"][0]["content"][0]
    assert block["type"] == "file" and block["media_type"] == "application/pdf"
    assert base64.standard_b64decode(block["data"]) == b"\xff\xd8jpegbytes"


def test_a_plain_text_file_is_read_without_the_model(monkeypatch):
    fake, sent, client = wire(monkeypatch, [[("note_save", {"body": "список", "project": "", "url": ""})], "Ок."])
    monkeypatch.setattr(files, "_download", lambda fid: ("молоко\nхлеб".encode(), "docs/1.txt"))
    monkeypatch.setattr(files, "_client", lambda: (_ for _ in ()).throw(AssertionError("no model for text/plain")))
    bot.handle_update(document(name="list.txt", mime="text/plain"))
    assert "молоко" in client.messages.requests[0]["messages"][0]["content"]


def test_an_oversized_file_is_refused_without_a_download(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    monkeypatch.setattr(files, "_download", lambda fid: (_ for _ in ()).throw(AssertionError("no download")))
    bot.handle_update(document(size=40 * 1024 * 1024))
    assert "больше 20 МБ" in sent.messages[-1][1] and client.calls == 0
    assert list(fake.inbox.values())[0]["status"] == "done"


def test_an_unreadable_file_is_still_filed(monkeypatch):
    fake, sent, client = wire(monkeypatch, [[("note_save", {"body": "архив", "project": "", "url": ""})], "Сохранила."])
    monkeypatch.setattr(files, "_download", lambda fid: (_ for _ in ()).throw(AssertionError("never reached")))
    bot.handle_update(document(name="archive.zip", mime="application/zip"))
    turn = client.messages.requests[0]["messages"][0]["content"]
    assert "«archive.zip»" in turn and "не удалось" in turn
    assert sent.edits[-1][2] == "Сохранила."


def test_the_file_never_reaches_the_logs(monkeypatch, caplog):
    fake, sent, client = wire(monkeypatch, [[("note_save", {"body": "x", "project": "", "url": ""})], "Ок."])
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "123:SECRET-TOKEN")

    def boom(file_id):
        raise RuntimeError("https://api.telegram.org/file/bot123:SECRET-TOKEN/photos/1.jpg failed")
    monkeypatch.setattr(files, "_download", boom)
    with caplog.at_level(logging.DEBUG):
        bot.handle_update(photo())
    assert "SECRET-TOKEN" not in caplog.text


def test_the_file_comes_back_by_its_telegram_id(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("note_save", {"body": "чек из аптеки", "project": "", "url": ""})], "Сохранила.",
        [("note_search", {"query": "чек", "source": ""})], "Вот он.",
    ])
    vision(monkeypatch)
    sent_files = []
    monkeypatch.setattr(telegram, "send_document",
                        lambda chat_id, filename, data, caption="": sent_files.append((chat_id, filename, data)) or 5)
    bot.handle_update(photo(update_id=1))
    (note_id,) = list(fake.notes)
    # A note born from a file is marked in search results, so the model knows it can send it.
    bot.handle_update(msg("покажи чек", update_id=2))
    assert "📎" in client.messages.requests[-1]["messages"][-1]["content"][0]["content"]

    out = run(REGISTRY, "note_send_file", UserScope(user_id=1, chat_id="111", tz="UTC"),
              ToolContext(), {"note_id": note_id})
    assert out.startswith(f"Sent the file of note #{note_id}")
    assert sent_files == [("111", "photo.jpg", "big")]          # the Telegram id, not bytes
    assert run(REGISTRY, "note_send_file", UserScope(user_id=2, chat_id="222", tz="UTC"),
               ToolContext(), {"note_id": note_id}).startswith(f"Note #{note_id} has no file")


def test_a_note_typed_by_hand_has_no_file(monkeypatch):
    fake, sent, client = wire(monkeypatch, [[("note_save", {"body": "просто мысль", "project": "", "url": ""})], "Ок."])
    bot.handle_update(msg("просто мысль", update_id=1))
    (note_id,) = list(fake.notes)
    assert db.note_file(1, note_id) is None
    assert run(REGISTRY, "note_send_file", UserScope(user_id=1, chat_id="111", tz="UTC"),
               ToolContext(), {"note_id": note_id}).startswith(f"Note #{note_id} has no file")
