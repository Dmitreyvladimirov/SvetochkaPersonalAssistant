"""A scripted stand-in for the Anthropic client. Each entry in `script` is one
response: either a string (final text) or a list of tool calls (name, input) with an
optional trailing text. Records every request so tests can assert on what the model
was shown."""
from types import SimpleNamespace


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, input, id=None):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id or f"tu_{name}_{abs(hash(str(input))) % 10000}")


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def create(self, **kw):
        self.requests.append(kw)
        if not self.script:
            step = "Готово."
        else:
            step = self.script.pop(0)
        if isinstance(step, str):
            content, stop = [text_block(step)], "end_turn"
        else:
            content = [tool_block(name, inp) for name, inp in step]
            stop = "tool_use"
        return SimpleNamespace(content=content, stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)

    @property
    def calls(self):
        return len(self.messages.requests)
