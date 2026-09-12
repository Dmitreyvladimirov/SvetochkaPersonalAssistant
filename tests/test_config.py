"""Startup must fail loudly and name the missing variable by its canonical name."""
import importlib

import pytest


def _reload(monkeypatch, **env):
    # A developer's .env must not leak into these tests: config reads it on import.
    monkeypatch.setenv("SVETA_SKIP_DOTENV", "1")
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import sveta.core.config as cfg
    return importlib.reload(cfg)


def test_all_present_validates(monkeypatch):
    cfg = _reload(monkeypatch)
    cfg.validate_secrets()


def test_missing_variable_is_named_canonically(monkeypatch):
    cfg = _reload(monkeypatch, SVETA_PROVIDER="anthropic", SVETA_WEBHOOK_SECRET=None,
                  ANTHROPIC_API_KEY=None, sveta_anthropic=None)
    with pytest.raises(EnvironmentError) as exc:
        cfg.validate_secrets()
    message = str(exc.value)
    assert "SVETA_WEBHOOK_SECRET" in message
    assert "ANTHROPIC_API_KEY" in message
    assert "sveta_anthropic" not in message, "aliases are accepted, never advertised"


def test_only_the_working_provider_key_is_required(monkeypatch):
    """A deployment on OpenAI must not refuse to start over a missing Anthropic
    key, and the other way round. The Whisper key is needed either way."""
    cfg = _reload(monkeypatch, SVETA_PROVIDER="openai", ANTHROPIC_API_KEY=None,
                  sveta_anthropic=None)
    cfg.validate_secrets()
    cfg = _reload(monkeypatch, SVETA_PROVIDER="openai", OPENAI_API_KEY=None,
                  sveta_openai_api=None)
    with pytest.raises(EnvironmentError) as exc:
        cfg.validate_secrets()
    assert "OPENAI_API_KEY" in str(exc.value)
    assert str(exc.value).count("OPENAI_API_KEY") == 1, "named once, not twice"


def test_each_provider_brings_its_own_default_models(monkeypatch):
    openai = _reload(monkeypatch, SVETA_PROVIDER="openai")
    assert openai.AGENT_MODEL.startswith("gpt-") and openai.VISION_MODEL.startswith("gpt-")
    anthropic = _reload(monkeypatch, SVETA_PROVIDER="anthropic")
    assert anthropic.AGENT_MODEL.startswith("claude-")
    # Attachments never run on the agent model: that is the whole point of the role.
    assert openai.VISION_MODEL != openai.AGENT_MODEL
    assert anthropic.VISION_MODEL != anthropic.AGENT_MODEL
    named = _reload(monkeypatch, SVETA_PROVIDER="openai", SVETA_VISION_MODEL="gpt-5.4-nano")
    assert named.VISION_MODEL == "gpt-5.4-nano"


def test_aliases_are_accepted(monkeypatch):
    # The names Dimitry actually typed into Railway on 2026-09-03.
    cfg = _reload(monkeypatch, SVETA_TELEGRAM_TOKEN=None, sveta_telegram_token="alias-token",
                  ANTHROPIC_API_KEY=None, sveta_anthropic="alias-key")
    cfg.validate_secrets()
    assert cfg.TELEGRAM_TOKEN == "alias-token"
    assert cfg.ANTHROPIC_API_KEY == "alias-key"


def test_canonical_name_wins_over_alias(monkeypatch):
    cfg = _reload(monkeypatch, SVETA_TELEGRAM_TOKEN="canonical", sveta_telegram_token="alias")
    assert cfg.TELEGRAM_TOKEN == "canonical"


def test_chat_ids_are_a_list(monkeypatch):
    cfg = _reload(monkeypatch, SVETA_ALLOWED_CHAT_IDS=" 111, 222 ,,")
    assert cfg.ALLOWED_CHAT_IDS == ["111", "222"]


def test_model_ids_have_no_date_suffix(monkeypatch):
    cfg = _reload(monkeypatch)
    for model in (cfg.AGENT_MODEL, cfg.CHEAP_MODEL):
        assert not model[-8:].isdigit(), model


def test_workspace_id_is_optional_and_accepts_alias(monkeypatch):
    cfg = _reload(monkeypatch, ANTHROPIC_WORKSPACE_ID=None, SVETA_ANTHROPIC_WORKSPACE_ID=None)
    assert cfg.ANTHROPIC_WORKSPACE_ID == ""
    cfg.validate_secrets()  # not required: a workspace-scoped key needs no id
    cfg = _reload(monkeypatch, SVETA_ANTHROPIC_WORKSPACE_ID="wrkspc_123")
    assert cfg.ANTHROPIC_WORKSPACE_ID == "wrkspc_123"
