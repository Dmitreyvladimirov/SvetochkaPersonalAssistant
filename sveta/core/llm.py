"""The only module that holds a model API key, and the only one that prices calls.

Every paid call is priced and recorded before its result is used, so the first
surprising bill is not also the first data point (FR-38).
"""
import logging
import time

from sveta.core import config, db

logger = logging.getLogger(__name__)

# USD per million tokens, from the Anthropic pricing table (cache 2026-06-24). A wrong
# number here shows up as a wrong budget alert, never as a wrong answer — which is
# why an unknown model is priced at 0 and logged rather than raising.
PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}


class BudgetExceeded(Exception):
    """Today's spend for this user is over the line. Retrying makes it worse."""


def price(model: str, usage: dict) -> float:
    rates = PRICES.get(model)
    if not rates:
        logger.warning("llm: no price for model %r — recording 0", model)
        return 0.0
    in_rate, out_rate = rates
    return (usage.get("input_tokens", 0) / 1e6 * in_rate
            + usage.get("output_tokens", 0) / 1e6 * out_rate)


def check_budget(user_id: int) -> None:
    """Best-effort by design: if the accounting query fails, the assistant keeps
    working — a broken meter is a worse reason to go silent than an overspend."""
    try:
        spent = db.spend_today(user_id)
    except Exception:
        logger.exception("llm: budget check failed — allowing the call")
        return
    if spent >= config.DAILY_USD_LIMIT:
        raise BudgetExceeded(f"${spent:.2f} spent today, limit ${config.DAILY_USD_LIMIT:.2f}")


def client():
    import anthropic
    headers = {}
    if config.ANTHROPIC_WORKSPACE_ID:
        headers["anthropic-workspace-id"] = config.ANTHROPIC_WORKSPACE_ID
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, default_headers=headers)


def usage_of(response) -> dict:
    return {"input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens}


class Timer:
    def __enter__(self):
        self.started = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.monotonic() - self.started) * 1000)


def classify_error(exc: BaseException) -> str | None:
    """FR-39: a dead credential is said plainly in the chat, not hidden behind
    'модель не ответила'. Returns the user-facing sentence, or None when the
    error is something else (the generic text applies)."""
    text = str(exc)
    name = type(exc).__name__
    if name == "AuthenticationError" or "invalid x-api-key" in text:
        return ("Ключ Anthropic не принимается (401). Проверь sveta_anthropic в Railway — "
                "до тех пор я не отвечаю на свободный текст.")
    if "credit balance" in text.lower():
        return ("У аккаунта Anthropic закончился кредит. Пополни в Console → Plans & Billing — "
                "до тех пор я не отвечаю на свободный текст, но всё сохраняю.")
    if name == "PermissionDeniedError":
        return "Ключу Anthropic не хватает прав (403). Проверь workspace ключа в Console."
    return None
