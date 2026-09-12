"""Anthropic: Messages API, content blocks, explicit cache breakpoints.

Prices are $ per million tokens, input / output, from the reference in SPEC §7.1.
A wrong number here shows up as a wrong budget alert, never as a wrong answer.
"""
import logging

from sveta.core.providers import Completion, ToolCall, ToolOutput

logger = logging.getLogger(__name__)

NAME = "anthropic"

PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}

DEFAULT_MODELS = {"agent": "claude-sonnet-5", "cheap": "claude-haiku-4-5",
                  "vision": "claude-haiku-4-5", "brief": "claude-sonnet-5"}

# Anthropic caches only what is marked. The marker goes on the last element of the
# prefix that never changes; everything before it is covered.
CACHE = {"type": "ephemeral"}


def client(api_key: str, workspace_id: str = ""):
    import anthropic
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    return anthropic.Anthropic(api_key=api_key, default_headers=headers) if headers \
        else anthropic.Anthropic(api_key=api_key)


def _tools(tools: list[dict]) -> list[dict]:
    """Our canonical definitions are already Anthropic's shape; the last one
    carries the cache breakpoint, which covers all of them because tools precede
    the system prompt in the cached prefix."""
    out = [dict(t) for t in tools]
    if out:
        out[-1]["cache_control"] = CACHE
    return out


def _system(system: list[dict]) -> list[dict]:
    """The first block is the constant persona and is cached; the rest is this
    user's, and changing it invalidates nothing."""
    blocks = [dict(b) for b in system if b.get("text")]
    if blocks:
        blocks[0]["cache_control"] = CACHE
    return blocks


def _usage(response) -> dict:
    u = response.usage
    return {"input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}


def complete(client, *, model: str, system: list[dict], messages: list,
             tools: list[dict] | None = None, max_tokens: int = 1500,
             effort: str = "low") -> Completion:
    kwargs = {"model": model, "max_tokens": max_tokens,
              "system": _system(system), "messages": messages}
    if tools:
        kwargs["tools"] = _tools(tools)
        kwargs["output_config"] = {"effort": effort}
    response = client.messages.create(**kwargs)
    text = "\n".join(b.text for b in response.content
                     if b.type == "text" and b.text.strip()).strip()
    calls = [ToolCall(id=b.id, name=b.name, args=b.input if isinstance(b.input, dict) else {})
             for b in response.content if b.type == "tool_use"]
    turn = [{"role": "assistant", "content": response.content}] if calls else []
    return Completion(text=text, tool_calls=calls, usage=_usage(response), turn=turn)


def tool_results(outputs: list[ToolOutput]) -> list:
    """Every result of one step goes back as a single user turn."""
    blocks = []
    for o in outputs:
        block = {"type": "tool_result", "tool_use_id": o.call_id, "content": o.content}
        if o.is_error:
            block["is_error"] = True
        blocks.append(block)
    return [{"role": "user", "content": blocks}] if blocks else []


def file_block(data_b64: str, mime: str) -> dict:
    if mime == "application/pdf":
        return {"type": "document",
                "source": {"type": "base64", "media_type": mime, "data": data_b64}}
    return {"type": "image",
            "source": {"type": "base64", "media_type": mime, "data": data_b64}}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}

def classify_error(exc: BaseException) -> str | None:
    """FR-39: a dead credential is said plainly in the chat, not hidden behind
    'модель не ответила'."""
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
    if exc.status_code == 400 and "usage limits" in text:
        # A spend cap is not an empty balance and not a bad key: the account is
        # fine, the workspace is simply out of budget until a date the API names.
        import re
        m = re.search(r"regain access on (\d{4}-\d{2}-\d{2})", str(exc))
        until = f" Доступ вернётся {m.group(1)}." if m else ""
        return ("Упёрлась в лимит расходов воркспейса Anthropic — это не кончившийся счёт "
                f"и не сломанный ключ, а потолок трат.{until} Подними его в Console → "
                "Settings → Limits. До тех пор я не отвечаю на свободный текст, но всё сохраняю.")
    if exc.status_code == 403:
        return "Ключу Anthropic не хватает прав (403). Проверь workspace ключа в Console."
    return None
