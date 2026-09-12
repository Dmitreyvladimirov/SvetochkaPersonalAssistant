import pytest



def test_client_sends_workspace_header_only_when_configured(monkeypatch):
    """An identity-linked Anthropic key needs the workspace id on every request; a
    workspace-scoped one must not be sent one."""
    import sys, types
    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.clear()
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
    from sveta.core.providers import anthropic_api

    anthropic_api.client("k", "")
    assert "default_headers" not in captured

    anthropic_api.client("k", "wrkspc_123")
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
    from sveta.core.providers import anthropic_api
    old = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20))
    assert anthropic_api._usage(old) == {"input_tokens": 100, "output_tokens": 20,
                                         "cache_write_tokens": 0, "cache_read_tokens": 0}
    new = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                cache_creation_input_tokens=5000,
                                                cache_read_input_tokens=None))
    assert anthropic_api._usage(new)["cache_write_tokens"] == 5000
    assert anthropic_api._usage(new)["cache_read_tokens"] == 0


def test_openai_cached_tokens_are_not_counted_twice():
    """OpenAI reports cached tokens INSIDE input_tokens; Anthropic reports them
    beside it. Without the subtraction the cached prefix is billed at full price
    and at the cache rate, on every single call."""
    from types import SimpleNamespace
    from sveta.core.providers import openai_api
    response = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=5600, output_tokens=250,
        input_tokens_details=SimpleNamespace(cached_tokens=5000, cache_write_tokens=0)))
    assert openai_api._usage(response) == {"input_tokens": 600, "output_tokens": 250,
                                           "cache_write_tokens": 0, "cache_read_tokens": 5000}
    bare = SimpleNamespace(usage=SimpleNamespace(input_tokens=79, output_tokens=81,
                                                 input_tokens_details=None))
    assert openai_api._usage(bare)["input_tokens"] == 79


def test_a_spend_cap_is_named_as_a_spend_cap():
    """Live on 2026-09-12: the workspace hit its cap, classify_error knew only
    'credit balance', and every message got 'Не смогла разобрать — модель не
    ответила'. Svetochka looked stupid instead of blocked."""
    import anthropic
    import httpx
    from sveta.core import llm

    def err(message, status=400):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(status, request=request, json={"type": "error", "error": {"message": message}})
        return anthropic.BadRequestError(message, response=response, body=None)

    cap = llm.classify_error(err("You have reached your specified API usage limits. "
                                 "You will regain access on 2026-10-01 at 00:00 UTC."))
    assert cap is not None
    assert "лимит расходов" in cap and "2026-10-01" in cap
    assert "кредит" not in cap                    # not confused with an empty balance
    assert llm.classify_error(err("Your credit balance is too low")) is not None
    assert llm.classify_error(err("Schema is too complex")) is None
