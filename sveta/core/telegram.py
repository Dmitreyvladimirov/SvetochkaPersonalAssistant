"""Bot API client. Plain text, no parse_mode.

Markdown is off on purpose: replies echo the user's own words back, and a stray '*'
or '_' makes Telegram reject the whole message with a 400. Every call swallows its
errors and returns None — a raised exception inside a webhook handler becomes a 500,
which makes Telegram redeliver the update, which reprocesses it.
"""
import json
import logging
import urllib.request

from sveta.core import config

logger = logging.getLogger(__name__)


def _api() -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"


def send_message(text: str, chat_id, reply_markup: dict | None = None) -> int | None:
    """Returns the message_id so a later call can edit this message in place."""
    body = {"chat_id": chat_id, "text": text[:config.MAX_MESSAGE_CHARS],
            "disable_web_page_preview": True}
    if reply_markup:
        body["reply_markup"] = reply_markup
    result = _call("sendMessage", body)
    return (result or {}).get("message_id")


def edit_message(message_id: int, text: str, chat_id, reply_markup: dict | None = None) -> None:
    body = {"chat_id": chat_id, "message_id": message_id, "text": text[:config.MAX_MESSAGE_CHARS],
            "disable_web_page_preview": True}
    if reply_markup:
        body["reply_markup"] = reply_markup
    _call("editMessageText", body)


def send_document(chat_id, filename: str, data: bytes | str, caption: str = "") -> int | None:
    """A file into the user's own chat: bytes to upload (a ticket PDF from mail),
    or a Telegram file_id string to re-send something Telegram already holds
    (FR-61), which costs no upload. Returns the message_id or None."""
    try:
        import httpx
        body = {"chat_id": str(chat_id), "caption": caption[:1000]}
        if isinstance(data, str):
            body["document"] = data
            r = httpx.post(f"{_api()}/sendDocument", data=body, timeout=60.0)
        else:
            r = httpx.post(f"{_api()}/sendDocument", data=body,
                           files={"document": (filename, data)}, timeout=60.0)
        result = r.json().get("result") if r.status_code == 200 else None
        if result is None:
            logger.error("telegram: sendDocument failed: HTTP %s", r.status_code)
        return (result or {}).get("message_id")
    except Exception as e:  # noqa: BLE001 — the type only: the URL carries the bot token
        logger.error("telegram: sendDocument failed: %s", type(e).__name__)
        return None


def edit_reply_markup(message_id: int, chat_id, reply_markup: dict | None) -> None:
    """Swap only the buttons under a message — what a checkbox tap does (FR-49)."""
    _call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                     "reply_markup": reply_markup or {"inline_keyboard": []}})


def answer_callback(callback_id: str, text: str = "") -> None:
    """Stop the spinner on a tapped button; without it the button looks broken."""
    _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def set_webhook(url: str, secret: str) -> bool:
    result = _call("setWebhook", {
        "url": url, "secret_token": secret,
        "allowed_updates": ["message", "edited_message", "callback_query"],
        "drop_pending_updates": False,
    })
    return bool(result)


def _call(method: str, body: dict) -> dict | None:
    try:
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"{_api()}/{method}", data=payload)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")).get("result")
    except Exception as e:  # noqa: BLE001
        logger.error("telegram: %s failed: %s", method, e)
        return None
