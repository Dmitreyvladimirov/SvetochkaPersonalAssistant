"""A scripted stand-in for the model, speaking the normalised provider interface.

Each entry in `script` is one reply: either a string (final text) or a list of
tool calls as (name, arguments). Every request is recorded so a test can assert on
what the model was actually shown.

The double carries its own provider (`FakeClient.provider`), which is how the
tests stay out of any real wire format: they assert on `system`, `tools` and
`messages` as the loop assembled them, not on Anthropic's content blocks or
OpenAI's output items. Each provider's own translation is tested where it lives.
"""
from sveta.core.providers import Completion, ToolCall


class FakeMessages:
    """Kept under `client.messages` because that is where the tests look."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def next_reply(self, kw) -> Completion:
        self.requests.append(kw)
        step = self.script.pop(0) if self.script else "Готово."
        if isinstance(step, str):
            return Completion(text=step, tool_calls=[], usage=USAGE, turn=[])
        calls = [ToolCall(id=f"tu_{name}_{abs(hash(str(args))) % 10000}", name=name, args=args)
                 for name, args in step]
        return Completion(text="", tool_calls=calls, usage=USAGE,
                          turn=[{"role": "assistant", "content": [c.name for c in calls]}])


USAGE = {"input_tokens": 100, "output_tokens": 20,
         "cache_write_tokens": 0, "cache_read_tokens": 0}


class FakeProvider:
    """The provider protocol, answering from the script instead of the network."""
    NAME = "fake"
    PRICES = {}
    DEFAULT_MODELS = {"agent": "fake-agent", "cheap": "fake-cheap",
                      "vision": "fake-vision", "brief": "fake-agent"}

    @staticmethod
    def complete(client, **kw) -> Completion:
        return client.messages.next_reply(kw)

    @staticmethod
    def tool_results(outputs) -> list:
        return [{"role": "user", "content": [
            {"call_id": o.call_id, "content": o.content, "is_error": o.is_error} for o in outputs]}]

    @staticmethod
    def file_block(data_b64: str, mime: str) -> dict:
        return {"type": "file", "media_type": mime, "data": data_b64}

    @staticmethod
    def text_block(text: str) -> dict:
        return {"type": "text", "text": text}

    @staticmethod
    def classify_error(exc):
        return None


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)
        self.provider = FakeProvider

    @property
    def calls(self):
        return len(self.messages.requests)


def scripted(*replies, capture=None) -> FakeClient:
    """A cheap-model double for the extraction call sites (attachments, Gmail query
    planning, trip parsing). `capture` receives the request the tool assembled."""
    client = FakeClient(list(replies))
    if capture is not None:
        original = client.messages.next_reply

        def watched(kw):
            capture.update(kw)
            return original(kw)
        client.messages.next_reply = watched
    return client


class FailingClient:
    """A model that is reachable but refuses, so the caller's fallback and its
    FR-39 wording are what the test sees."""

    def __init__(self, exc):
        class Provider(FakeProvider):
            @staticmethod
            def complete(client, **kw):
                raise exc
        self.provider = Provider
        self.messages = FakeMessages([])

    @property
    def calls(self):
        return 0
