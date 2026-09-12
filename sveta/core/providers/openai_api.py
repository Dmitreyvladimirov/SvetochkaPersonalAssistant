"""OpenAI: the Responses API, because that is where tools live on this generation.

Chat Completions refuses them outright (measured 2026-09-12):

    Function tools with reasoning_effort are not supported for gpt-5.6-terra
    in /v1/chat/completions. To use function tools, use /v1/responses

The shape differs from Anthropic in three ways that matter to the loop, and all
three are absorbed here: the system prompt is a string (`instructions`) rather
than blocks, the reply is a flat `output` list rather than content blocks, and a
tool result is its own item rather than a block inside one user turn.

Prices are $ per million tokens, input / output, from SPEC §7.1. **The table there
came from search snippets and the spec itself says to verify it before a final
decision** — a wrong number here is a wrong budget alert, never a wrong answer.
"""
import json
import logging

from sveta.core.providers import Completion, ToolCall, ToolOutput

logger = logging.getLogger(__name__)

NAME = "openai"

PRICES = {
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-sol": (5.00, 30.00),
}

# Luna, not nano, for reading attachments: both read a ticket PDF correctly in
# testing, but Luna's price is the one SPEC §7.1 records. Move vision to
# gpt-5.4-nano once its rate is confirmed — it is cheaper again.
DEFAULT_MODELS = {"agent": "gpt-5.6-terra", "cheap": "gpt-5.6-luna",
                  "vision": "gpt-5.6-luna", "brief": "gpt-5.6-terra"}


def client(api_key: str, workspace_id: str = ""):
    import openai
    return openai.OpenAI(api_key=api_key)


def _tools(tools: list[dict]) -> list[dict]:
    """Our canonical definition, flattened the way the Responses API wants it.
    The schemas are already strict-shaped (additionalProperties false, every
    property required), which is exactly what `strict` requires here too."""
    return [{"type": "function", "name": t["name"], "description": t["description"],
             "parameters": t["input_schema"], "strict": bool(t.get("strict"))}
            for t in tools]


def _instructions(system: list[dict]) -> str:
    """Caching here is automatic on the prefix, so there is nothing to mark: the
    blocks are simply joined in the order that keeps the constant part first."""
    return "\n\n".join(b["text"] for b in system if b.get("text"))


def _usage(response) -> dict:
    """OpenAI counts cached tokens inside `input_tokens`; our convention (and
    Anthropic's) is that the two do not overlap. Subtracting here keeps `price()`
    correct for both providers instead of silently double-charging the prefix."""
    u = getattr(response, "usage", None)
    if u is None:
        return {"input_tokens": 0, "output_tokens": 0,
                "cache_write_tokens": 0, "cache_read_tokens": 0}
    details = getattr(u, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    written = getattr(details, "cache_write_tokens", 0) or 0
    total_in = getattr(u, "input_tokens", 0) or 0
    return {"input_tokens": max(0, total_in - cached - written),
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_write_tokens": written,
            "cache_read_tokens": cached}


def _args(raw) -> dict:
    """Arguments arrive as a JSON string. A malformed one is the model's mistake,
    and it comes back to it as a tool error rather than crashing the loop."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (ValueError, TypeError):
        logger.warning("openai: tool arguments were not valid JSON")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_dict(item):
    """Output items go back into the next request verbatim, so they have to be
    plain JSON, not SDK objects."""
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    return item


def complete(client, *, model: str, system: list[dict], messages: list,
             tools: list[dict] | None = None, max_tokens: int = 1500,
             effort: str = "low") -> Completion:
    kwargs = {"model": model, "instructions": _instructions(system),
              "input": messages, "max_output_tokens": max_tokens}
    if tools:
        kwargs["tools"] = _tools(tools)
        kwargs["reasoning"] = {"effort": effort}
    response = client.responses.create(**kwargs)

    text_parts, calls, turn = [], [], []
    for item in response.output:
        kind = getattr(item, "type", None)
        if kind == "message":
            for block in getattr(item, "content", []) or []:
                said = getattr(block, "text", "")
                if said and said.strip():
                    text_parts.append(said.strip())
        elif kind == "function_call":
            calls.append(ToolCall(id=item.call_id, name=item.name, args=_args(item.arguments)))
    if calls:
        # A reasoning model needs its own reasoning items handed back with the
        # calls, so the whole output goes back, not only the calls.
        turn = [_as_dict(item) for item in response.output]
    return Completion(text="\n".join(text_parts).strip(), tool_calls=calls,
                      usage=_usage(response), turn=turn)


def tool_results(outputs: list[ToolOutput]) -> list:
    """One item per call, not one turn holding all of them."""
    return [{"type": "function_call_output", "call_id": o.call_id, "output": o.content}
            for o in outputs]


def file_block(data_b64: str, mime: str) -> dict:
    if mime == "application/pdf":
        return {"type": "input_file", "filename": "file.pdf",
                "file_data": f"data:{mime};base64,{data_b64}"}
    return {"type": "input_image", "image_url": f"data:{mime};base64,{data_b64}"}


def text_block(text: str) -> dict:
    return {"type": "input_text", "text": text}

def classify_error(exc: BaseException) -> str | None:
    """FR-39: a dead credential is named in the chat, not hidden behind
    'модель не ответила'."""
    try:
        import openai
    except ImportError:  # pragma: no cover
        return None
    if not isinstance(exc, openai.APIStatusError):
        return None
    text = str(exc).lower()
    if exc.status_code == 401:
        return ("Ключ OpenAI не принимается (401). Проверь sveta_openai_api в Railway — "
                "до тех пор я не отвечаю на свободный текст.")
    if exc.status_code == 429 and "quota" in text:
        return ("У проекта OpenAI кончилась квота. Пополни в Platform → Billing — "
                "до тех пор я не отвечаю на свободный текст, но всё сохраняю.")
    if exc.status_code == 429:
        return None                       # an ordinary rate limit: retrying helps
    if exc.status_code == 403:
        return "Ключу OpenAI не хватает прав (403). Проверь проект ключа в Platform."
    return None
