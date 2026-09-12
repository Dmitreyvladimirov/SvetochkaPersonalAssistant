"""One shape of request and reply, whichever model house is answering (SPEC §7.1).

The tools, the playbooks and the loop in `agent.py` are provider-neutral by
construction; only the call itself is not. Everything provider-specific lives in
one module per provider behind this interface, so switching is an environment
variable and not a rewrite — and so the same golden scenarios can be run against
both and compared, which is the only way to answer "is she smarter on OpenAI".

Two conventions the providers normalise, because getting them wrong shows up as a
wrong bill rather than a wrong answer:

- `usage["input_tokens"]` counts only tokens that were NOT served from cache.
  Anthropic already reports it that way; OpenAI includes cached tokens in its
  input count, so its provider subtracts them.
- a turn is a list of messages to extend the conversation with, never one message.
  Anthropic puts every tool result in a single user turn; OpenAI appends one item
  per call. The loop only ever extends, so both fit.
"""
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """One request from the model to run one tool."""
    id: str
    name: str
    args: dict


@dataclass
class Completion:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    # The assistant's own turn, in whatever shape its provider expects back.
    turn: list = field(default_factory=list)


@dataclass
class ToolOutput:
    """What a tool produced, on its way back to the model."""
    call_id: str
    content: str
    is_error: bool = False


class ProviderError(Exception):
    """The provider name in the environment is not one we have a module for."""


def get(name: str):
    """The provider module for `name`. Imported lazily: a deployment that uses one
    provider must not need the other's SDK installed."""
    key = (name or "").strip().lower()
    if key == "anthropic":
        from sveta.core.providers import anthropic_api
        return anthropic_api
    if key == "openai":
        from sveta.core.providers import openai_api
        return openai_api
    raise ProviderError(f"unknown provider {name!r}; expected 'openai' or 'anthropic'")
