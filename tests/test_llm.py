import pytest



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


def test_cached_tokens_are_priced_at_their_own_rates():
    """A cache write costs a quarter more than plain input, a read a tenth. Both
    used to be invisible to price(), which would have under-reported the first
    call of a conversation and over-reported every one after it."""
    from sveta.core import llm
    plain = llm.price("claude-sonnet-5", {"input_tokens": 5000, "output_tokens": 0})
    write = llm.price("claude-sonnet-5", {"input_tokens": 0, "output_tokens": 0,
                                          "cache_write_tokens": 5000})
    read = llm.price("claude-sonnet-5", {"input_tokens": 0, "output_tokens": 0,
                                         "cache_read_tokens": 5000})
    assert write == pytest.approx(plain * 1.25)
    assert read == pytest.approx(plain * 0.10)
    # The three counts add up, they do not overlap: the API's input_tokens
    # already excludes whatever it served from the cache.
    both = llm.price("claude-sonnet-5", {"input_tokens": 600, "output_tokens": 250,
                                         "cache_read_tokens": 5000})
    assert both == pytest.approx(600 / 1e6 * 2 + 5000 * 0.1 / 1e6 * 2 + 250 / 1e6 * 10)


def test_usage_survives_a_response_that_never_heard_of_caching():
    from types import SimpleNamespace
    from sveta.core import llm
    old = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20))
    assert llm.usage_of(old) == {"input_tokens": 100, "output_tokens": 20,
                                 "cache_write_tokens": 0, "cache_read_tokens": 0}
    new = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                cache_creation_input_tokens=5000,
                                                cache_read_input_tokens=None))
    assert llm.usage_of(new)["cache_write_tokens"] == 5000
    assert llm.usage_of(new)["cache_read_tokens"] == 0
