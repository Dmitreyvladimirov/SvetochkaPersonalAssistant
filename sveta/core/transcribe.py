"""Voice → text (FR-16). Telegram file download plus one Whisper call over plain
HTTPS; no OpenAI SDK for a single multipart POST.

The duration check happens in bot.py *before* this module is called (FR-17): a
voice note over the limit must cost zero downloads and zero API spend. Errors
are raised — the caller decides what to tell the user and leaves the inbox row
retryable."""
import logging

from sveta.core import config

logger = logging.getLogger(__name__)

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
TIMEOUT = 120.0


class TranscriptionError(Exception):
    pass


def _download(file_id: str) -> bytes:
    """getFile → file_path → bytes. Patched in tests."""
    import httpx
    token = config.TELEGRAM_TOKEN
    with httpx.Client(timeout=60.0) as client:
        meta = client.post(f"https://api.telegram.org/bot{token}/getFile",
                           json={"file_id": file_id}).json()
        path = (meta.get("result") or {}).get("file_path")
        if not path:
            raise TranscriptionError(f"getFile returned no file_path: {meta.get('description', '?')}")
        r = client.get(f"https://api.telegram.org/file/bot{token}/{path}")
        # No raise_for_status(): httpx puts the full URL — token included — into
        # the exception text, and that text would reach the log and inbox_items.error (§8).
        if r.status_code != 200:
            raise TranscriptionError(f"file download HTTP {r.status_code}")
        return r.content


def _whisper(audio: bytes, filename: str = "voice.ogg") -> str:
    """Patched in tests."""
    import httpx
    if not config.OPENAI_API_KEY:
        raise TranscriptionError("OPENAI_API_KEY is not set")
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(WHISPER_URL,
                        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                        data={"model": config.TRANSCRIBE_MODEL, "language": "ru",
                              "response_format": "json"},
                        files={"file": (filename, audio, "audio/ogg")})
    if r.status_code == 401:
        raise TranscriptionError("Ключ OpenAI не принимается (401). Проверь sveta_openai_api в Railway.")
    if r.status_code != 200:
        raise TranscriptionError(f"whisper HTTP {r.status_code}: {r.text[:200]}")
    return (r.json().get("text") or "").strip()


def _redact(text: str) -> str:
    """Belt and braces for §8: whatever an HTTP library puts in an error message,
    the bot token and the OpenAI key never leave this module inside one."""
    for secret in (config.TELEGRAM_TOKEN, config.OPENAI_API_KEY):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def telegram_voice(file_id: str) -> str:
    """The transcript, possibly empty (the caller treats empty as 'не разобрала').
    Raises TranscriptionError on transport trouble; its text is safe to log."""
    try:
        audio = _download(file_id)
    except TranscriptionError as e:
        raise TranscriptionError(_redact(str(e))) from None
    except Exception as e:  # noqa: BLE001
        raise TranscriptionError(_redact(f"download failed: {type(e).__name__}: {str(e)[:200]}")) from None
    if not audio:
        raise TranscriptionError("downloaded file is empty")
    try:
        return _whisper(audio).strip()
    except TranscriptionError as e:
        raise TranscriptionError(_redact(str(e))) from None
    except Exception as e:  # noqa: BLE001
        raise TranscriptionError(_redact(f"whisper failed: {type(e).__name__}: {str(e)[:200]}")) from None
