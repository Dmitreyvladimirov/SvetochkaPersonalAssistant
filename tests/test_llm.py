

def test_client_sends_workspace_header_only_when_configured(monkeypatch):
    import sys, types
    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
    from sveta.core import config, llm

    monkeypatch.setattr(config, "ANTHROPIC_WORKSPACE_ID", "")
    llm.client()
    assert captured["default_headers"] == {}

    monkeypatch.setattr(config, "ANTHROPIC_WORKSPACE_ID", "wrkspc_123")
    llm.client()
    assert captured["default_headers"] == {"anthropic-workspace-id": "wrkspc_123"}
