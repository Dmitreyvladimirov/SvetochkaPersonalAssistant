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
