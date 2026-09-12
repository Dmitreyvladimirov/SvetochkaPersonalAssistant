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


# Writing the cache costs a quarter more than sending the tokens plainly; reading
# it back costs a tenth. A run that ignored this would under-report the first call
# of a conversation and over-report every one after it.
CACHE_WRITE_RATE = 1.25
CACHE_READ_RATE = 0.10


def price(model: str, usage: dict) -> float:
    rates = PRICES.get(model)
    if not rates:
        logger.warning("llm: no price for model %r — recording 0", model)
        return 0.0
    in_rate, out_rate = rates
    return ((usage.get("input_tokens", 0)
             + usage.get("cache_write_tokens", 0) * CACHE_WRITE_RATE
             + usage.get("cache_read_tokens", 0) * CACHE_READ_RATE) / 1e6 * in_rate
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
    """`input_tokens` from the API already excludes what was cached, so the three
    counts add up rather than overlap. getattr, not attribute access: a response
    from a model or a stub without caching has neither field."""
    u = response.usage
    return {"input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}


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
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return None
    if not isinstance(exc, anthropic.APIStatusError):
        return None
    text = str(exc).lower()
    if exc.status_code == 401:
        return ("Ключ Anthropic не принимается (401). Проверь sveta_anthropic в Railway — "
                "до тех пор я не отвечаю на свободный текст.")
    if exc.status_code == 400 and "credit balance" in text:
        return ("У аккаунта Anthropic закончился кредит. Пополни в Console → Plans & Billing — "
                "до тех пор я не отвечаю на свободный текст, но всё сохраняю.")
    if exc.status_code == 403:
        return "Ключу Anthropic не хватает прав (403). Проверь workspace ключа в Console."
    return None
