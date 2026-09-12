"""The only module that holds a model API key, and the only one that prices calls.

Every paid call is priced and recorded before its result is used, so the first
surprising bill is not also the first data point (FR-38). Which house answers is
`config.PROVIDER`; the call itself lives in `sveta/core/providers/` and this
module never sees a provider-shaped request or reply.
"""
import logging
import time

from sveta.core import config, db
from sveta.core import providers

logger = logging.getLogger(__name__)


def provider():
    """The module that speaks to the configured house."""
    return providers.get(config.PROVIDER)


def prices() -> dict:
    """Every provider's table, so a row recorded under the other one still prices
    correctly after a switch — the llm_call history outlives the choice."""
    from sveta.core.providers import anthropic_api, openai_api
    return {**anthropic_api.PRICES, **openai_api.PRICES}


class BudgetExceeded(Exception):
    """Today's spend for this user is over the line. Retrying makes it worse."""


# Writing the cache costs a quarter more than sending the tokens plainly; reading
# it back costs a tenth. A run that ignored this would under-report the first call
# of a conversation and over-report every one after it.
CACHE_WRITE_RATE = 1.25
CACHE_READ_RATE = 0.10


def price(model: str, usage: dict) -> float:
    rates = prices().get(model)
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
    return provider().client(config.provider_api_key(), config.ANTHROPIC_WORKSPACE_ID)


def _for(client):
    """The module that speaks to *this* client. A client may carry its own — that
    is how the scripted double in tests answers without imitating anyone's wire
    format — and otherwise it is the configured provider. The pairing matters:
    a tool result has to go back in the shape of whoever produced the call."""
    return getattr(client, "provider", None) or provider()


def complete(client, **kwargs):
    """One call, one normalised reply (`providers.Completion`), whoever answered."""
    return _for(client).complete(client, **kwargs)


def tool_results(client, outputs):
    return _for(client).tool_results(outputs)


def file_block(client, data_b64: str, mime: str) -> dict:
    """An attachment in the shape its provider expects."""
    return _for(client).file_block(data_b64, mime)


def text_block(client, text: str) -> dict:
    """Text sitting beside an attachment in the same turn."""
    return _for(client).text_block(text)


class Timer:
    def __enter__(self):
        self.started = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.monotonic() - self.started) * 1000)


def classify_error(exc: BaseException) -> str | None:
    """FR-39: a dead credential is said plainly in the chat, not hidden behind
    'модель не ответила'. Both providers are asked, not only the configured one —
    Whisper is OpenAI whoever runs the agent, so an OpenAI failure is possible on
    an Anthropic deployment and the other way round."""
    from sveta.core.providers import anthropic_api, openai_api
    for module in (provider(), anthropic_api, openai_api):
        try:
            said = module.classify_error(exc)
        except Exception:  # noqa: BLE001 — naming an error must not raise one
            logger.exception("llm: classify_error failed in %s", getattr(module, "NAME", "?"))
            continue
        if said:
            return said
    return None
