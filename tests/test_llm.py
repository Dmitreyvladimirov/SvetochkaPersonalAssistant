

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


def test_fernet_key_without_padding_is_accepted(monkeypatch):
    """The key in Railway was pasted as 43 url-safe chars, no trailing '='."""
    from cryptography.fernet import Fernet
    from sveta.core import config, crypto
    full = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "TOKEN_KEY", full.rstrip("="))
    crypto.validate()
    assert crypto.decrypt(crypto.encrypt("секрет")) == "секрет"
    monkeypatch.setattr(config, "TOKEN_KEY", "too-short")
    try:
        crypto.validate()
    except EnvironmentError as e:
        assert "SVETA_TOKEN_KEY" in str(e)
    else:
        raise AssertionError("must refuse")
