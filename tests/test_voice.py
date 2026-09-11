"""FR-16…18: a voice note is refused by duration before any download, otherwise
transcribed, stored and processed as text; the placeholder is rewritten."""
from sveta.core import bot, transcribe
from tests.test_bot import msg, wire


def voice(duration, file_id="f1", update_id=1):
    return msg("", voice={"file_id": file_id, "duration": duration}, update_id=update_id)


def test_long_voice_is_refused_with_zero_downloads(monkeypatch):
    fake, sent, client = wire(monkeypatch)

    def never(fid):
        raise AssertionError("no download for an over-limit voice note")
    monkeypatch.setattr(transcribe, "telegram_voice", never)
    monkeypatch.setattr(transcribe, "_download", never)
    bot.handle_update(voice(40 * 60))
    assert client.calls == 0
    assert "длиннее 10 минут" in sent.messages[-1][1]
    assert list(fake.inbox.values())[0]["status"] == "done"


def test_voice_is_transcribed_stored_and_processed_as_text(monkeypatch):
    fake, sent, client = wire(monkeypatch, [
        [("reminder_create", {"text": "позвонить маме", "when": "завтра в девять"})],
        "Напомню завтра в 09:00: позвонить маме.",
    ])
    bot.handle_update(voice(30))
    assert sent.messages[0][1] == "Расшифровываю…"
    item = list(fake.inbox.values())[0]
    assert item["kind"] == "voice" and item["transcript"] == "напомни завтра в девять позвонить маме"
    assert client.messages.requests[0]["messages"][0]["content"] == "напомни завтра в девять позвонить маме"
    message_id, chat, text, markup = sent.edits[-1]
    assert text.startswith("«напомни завтра в девять позвонить маме»\n\nНапомню")
    assert markup["inline_keyboard"][0][0]["callback_data"].startswith("undo:r:")


def test_empty_transcript_is_said_honestly(monkeypatch):
    fake, sent, client = wire(monkeypatch)
    monkeypatch.setattr(transcribe, "telegram_voice", lambda fid: "")
    bot.handle_update(voice(5))
    assert "Не разобрала" in sent.edits[-1][2] and client.calls == 0
    assert list(fake.inbox.values())[0]["status"] == "done"


def test_transport_failure_keeps_the_item_retryable(monkeypatch):
    fake, sent, client = wire(monkeypatch)

    def boom(fid):
        raise transcribe.TranscriptionError("whisper HTTP 500")
    monkeypatch.setattr(transcribe, "telegram_voice", boom)
    bot.handle_update(voice(5))
    assert "Не смогла расшифровать" in sent.edits[-1][2]
    item = list(fake.inbox.values())[0]
    assert item["status"] == "new" and "whisper HTTP 500" in item["error"]


def test_whisper_is_not_called_without_a_key(monkeypatch):
    monkeypatch.setattr(transcribe.config, "OPENAI_API_KEY", "")
    try:
        transcribe._whisper(b"ogg")
    except transcribe.TranscriptionError as e:
        assert "OPENAI_API_KEY" in str(e)
    else:
        raise AssertionError("must raise")


def test_telegram_voice_wraps_download_and_whisper(monkeypatch):
    monkeypatch.setattr(transcribe, "_download", lambda fid: b"audio")
    monkeypatch.setattr(transcribe, "_whisper", lambda audio, filename="voice.ogg": "  привет  ")
    assert transcribe.telegram_voice("f1") == "привет"
    monkeypatch.setattr(transcribe, "_download", lambda fid: b"")
    try:
        transcribe.telegram_voice("f1")
    except transcribe.TranscriptionError as e:
        assert "empty" in str(e)
    else:
        raise AssertionError("must raise")
