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


def test_bot_token_never_appears_in_errors_or_logs(monkeypatch, caplog):
    """§8: an httpx error message carries the full URL, token included."""
    import httpx
    from sveta.core import config
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "123456:SECRET-TOKEN-ABC")

    def failing_download(fid):
        url = f"https://api.telegram.org/file/bot{config.TELEGRAM_TOKEN}/voice/file_1.oga"
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("Client error '404 Not Found' for url '" + url + "'",
                                    request=req, response=httpx.Response(404, request=req))
    monkeypatch.setattr(transcribe, "_download", failing_download)
    try:
        transcribe.telegram_voice("f1")
    except transcribe.TranscriptionError as e:
        assert "SECRET-TOKEN-ABC" not in str(e) and "[redacted]" in str(e)
    else:
        raise AssertionError("must raise")

    fake, sent, client = wire(monkeypatch)
    monkeypatch.setattr(transcribe, "_download", failing_download)
    monkeypatch.setattr(transcribe, "telegram_voice", transcribe.telegram_voice)  # real wrapper
    import importlib
    real = importlib.import_module("sveta.core.transcribe").telegram_voice
    monkeypatch.setattr(transcribe, "telegram_voice", real)
    with caplog.at_level("ERROR"):
        bot.handle_update(voice(5))
    assert "SECRET-TOKEN-ABC" not in caplog.text
    assert "SECRET-TOKEN-ABC" not in (list(fake.inbox.values())[0]["error"] or "")


def test_download_status_is_reported_without_the_url(monkeypatch):
    class R:
        status_code = 500
        content = b""
    class C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, json=None): return type("M", (), {"json": lambda self: {"result": {"file_path": "voice/1.oga"}}})()
        def get(self, url): return R()
    import httpx
    monkeypatch.setattr(httpx, "Client", C)
    try:
        transcribe._download("f1")
    except transcribe.TranscriptionError as e:
        assert str(e) == "file download HTTP 500"
    else:
        raise AssertionError("must raise")
