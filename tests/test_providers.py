"""Each provider's translation, which is the only part of the stack that knows a
wire format. The loop, the tools and the playbooks are tested against the
normalised shape; these tests are what keeps that normalisation honest.

Shapes here were taken from live calls on 2026-09-12, not from memory.
"""
import json
from types import SimpleNamespace

import pytest

from sveta.core import providers
from sveta.core.providers import ToolOutput, anthropic_api, openai_api

SYSTEM = [{"type": "text", "text": "персона"}, {"type": "text", "text": "предпочтения"}]
TOOLS = [{"name": "note_search", "description": "Find notes.",
          "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                           "required": ["query"], "additionalProperties": False},
          "strict": False},
         {"name": "note_save", "description": "Save a note.",
          "input_schema": {"type": "object", "properties": {"body": {"type": "string"}},
                           "required": ["body"], "additionalProperties": False},
          "strict": True}]


def test_an_unknown_provider_is_refused_by_name():
    with pytest.raises(providers.ProviderError) as exc:
        providers.get("gemini")
    assert "gemini" in str(exc.value)
    assert providers.get("OpenAI ").NAME == "openai"      # case and spacing forgiven


# --- Anthropic ---------------------------------------------------------------

class AnthropicStub:
    def __init__(self, content):
        self.sent = {}
        self.content = content
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.sent = kw
        return SimpleNamespace(
            content=self.content,
            usage=SimpleNamespace(input_tokens=600, output_tokens=250,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=5000))


def test_anthropic_marks_the_cache_and_leaves_the_originals_alone():
    """The breakpoint goes on the last tool, which covers all of them because tools
    precede the system prompt in the cached prefix, and on the constant first
    system block. Both are marked on copies: the registry's schemas are checked by
    their own test and must not carry a provider's marker."""
    stub = AnthropicStub([SimpleNamespace(type="text", text="готово")])
    anthropic_api.complete(stub, model="claude-sonnet-5", system=SYSTEM,
                           messages=[{"role": "user", "content": "привет"}], tools=TOOLS)
    assert stub.sent["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert sum("cache_control" in t for t in stub.sent["tools"]) == 1
    assert stub.sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in stub.sent["system"][1]
    assert all("cache_control" not in t for t in TOOLS)
    assert all("cache_control" not in b for b in SYSTEM)
    assert stub.sent["output_config"] == {"effort": "low"}


def test_anthropic_reads_text_and_calls_out_of_content_blocks():
    stub = AnthropicStub([
        SimpleNamespace(type="text", text="  секунду  "),
        SimpleNamespace(type="tool_use", id="tu_1", name="note_save", input={"body": "x"}),
        SimpleNamespace(type="tool_use", id="tu_2", name="note_search", input=None),
    ])
    answer = anthropic_api.complete(stub, model="claude-sonnet-5", system=SYSTEM,
                                    messages=[], tools=TOOLS)
    assert answer.text == "секунду"
    assert [(c.id, c.name, c.args) for c in answer.tool_calls] == [
        ("tu_1", "note_save", {"body": "x"}), ("tu_2", "note_search", {})]
    assert answer.usage == {"input_tokens": 600, "output_tokens": 250,
                            "cache_write_tokens": 0, "cache_read_tokens": 5000}
    assert len(answer.turn) == 1 and answer.turn[0]["role"] == "assistant"


def test_anthropic_sends_every_result_back_in_one_turn():
    turns = anthropic_api.tool_results([ToolOutput("tu_1", "Saved note #1"),
                                        ToolOutput("tu_2", "Error: nope", is_error=True)])
    assert len(turns) == 1 and turns[0]["role"] == "user"
    blocks = turns[0]["content"]
    assert [b["tool_use_id"] for b in blocks] == ["tu_1", "tu_2"]
    assert "is_error" not in blocks[0] and blocks[1]["is_error"] is True
    assert anthropic_api.tool_results([]) == []


def test_anthropic_file_blocks():
    assert anthropic_api.file_block("Zm9v", "application/pdf")["type"] == "document"
    image = anthropic_api.file_block("Zm9v", "image/jpeg")
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"


# --- OpenAI ------------------------------------------------------------------

class OpenAIStub:
    def __init__(self, output):
        self.sent = {}
        self.responses = SimpleNamespace(create=self._create)
        self._output = output

    def _create(self, **kw):
        self.sent = kw
        return SimpleNamespace(
            output=self._output,
            usage=SimpleNamespace(input_tokens=5600, output_tokens=250,
                                  input_tokens_details=SimpleNamespace(cached_tokens=5000,
                                                                       cache_write_tokens=0)))


def item(**kw):
    obj = SimpleNamespace(**kw)
    obj.model_dump = lambda exclude_none=False: dict(kw)
    return obj


def test_openai_flattens_tools_and_joins_the_system_prompt():
    """The Responses API takes a string for the system prompt and a flat function
    shape for tools; `strict` survives per tool, because the write tools are the
    ones that must not receive a malformed argument."""
    stub = OpenAIStub([item(type="message", content=[SimpleNamespace(text="готово")])])
    openai_api.complete(stub, model="gpt-5.6-terra", system=SYSTEM,
                        messages=[{"role": "user", "content": "привет"}], tools=TOOLS)
    assert stub.sent["instructions"] == "персона\n\nпредпочтения"
    assert [t["type"] for t in stub.sent["tools"]] == ["function", "function"]
    assert stub.sent["tools"][0]["name"] == "note_search"
    assert stub.sent["tools"][0]["parameters"]["additionalProperties"] is False
    assert [t["strict"] for t in stub.sent["tools"]] == [False, True]
    assert stub.sent["reasoning"] == {"effort": "low"}
    assert "max_output_tokens" in stub.sent and "max_tokens" not in stub.sent


def test_openai_reads_calls_out_of_the_output_list():
    stub = OpenAIStub([
        item(type="reasoning", summary=[]),
        item(type="function_call", call_id="call_a", name="note_save",
             arguments='{"body": "x"}'),
        item(type="function_call", call_id="call_b", name="note_search", arguments="not json"),
    ])
    answer = openai_api.complete(stub, model="gpt-5.6-terra", system=SYSTEM,
                                 messages=[], tools=TOOLS)
    assert [(c.id, c.name, c.args) for c in answer.tool_calls] == [
        ("call_a", "note_save", {"body": "x"}), ("call_b", "note_search", {})]
    # Cached tokens are inside input_tokens here and beside it at Anthropic.
    assert answer.usage == {"input_tokens": 600, "output_tokens": 250,
                            "cache_write_tokens": 0, "cache_read_tokens": 5000}
    # A reasoning model needs its own reasoning handed back with the calls, and
    # what goes back must be plain JSON rather than SDK objects.
    assert [t["type"] for t in answer.turn] == ["reasoning", "function_call", "function_call"]
    json.dumps(answer.turn)


def test_openai_sends_one_item_per_result_not_one_turn():
    out = openai_api.tool_results([ToolOutput("call_a", "Saved note #1"),
                                   ToolOutput("call_b", "Error: nope", is_error=True)])
    assert [o["type"] for o in out] == ["function_call_output", "function_call_output"]
    assert [o["call_id"] for o in out] == ["call_a", "call_b"]
    assert out[0]["output"] == "Saved note #1"


def test_openai_file_blocks():
    pdf = openai_api.file_block("Zm9v", "application/pdf")
    assert pdf["type"] == "input_file" and pdf["file_data"].startswith("data:application/pdf;base64,")
    image = openai_api.file_block("Zm9v", "image/jpeg")
    assert image["type"] == "input_image" and image["image_url"].startswith("data:image/jpeg;base64,")


# --- Both --------------------------------------------------------------------

def test_a_text_only_reply_carries_no_turn_to_append():
    """Nothing is appended when the model is finished: the loop breaks and the
    conversation ends there, whoever answered."""
    a = anthropic_api.complete(AnthropicStub([SimpleNamespace(type="text", text="готово")]),
                               model="m", system=SYSTEM, messages=[], tools=TOOLS)
    o = openai_api.complete(OpenAIStub([item(type="message", content=[SimpleNamespace(text="готово")])]),
                            model="m", system=SYSTEM, messages=[], tools=TOOLS)
    assert a.turn == [] and o.turn == []
    assert a.text == o.text == "готово"
    assert a.tool_calls == o.tool_calls == []


def test_both_providers_price_every_model_they_name():
    """A model with no price records a zero cost, which silently disables the daily
    budget for it (FR-38). Every default must therefore be in its own table."""
    from sveta.core import llm
    table = llm.prices()
    for module in (anthropic_api, openai_api):
        for role, model in module.DEFAULT_MODELS.items():
            assert model in table, f"{module.NAME}.{role} = {model} has no price"
