"""Tests for `app/providers/status.py`.

Covers `spec: ai-provider` § "Provider status reporting" (active providers,
both models, embedding model, embedding dimension, no credential values) and
`spec: configuration` § "Effective configuration is readable" (indicate
whether each required secret is set; name the environment variable when one
is missing).
"""

from __future__ import annotations

import json

from app.config import AppConfig
from app.providers.factory import (
    EMBEDDING_API_KEY_ENV_VARS,
    LLM_API_KEY_ENV_VARS,
)
from app.providers.status import provider_status, secret_status

_SECRET_VALUE = "sk-super-secret-do-not-leak"


def test_provider_status_reports_providers_models_and_dimension():
    cfg = AppConfig()
    cfg.llm.model_classify = "claude-opus-5-classify"
    cfg.llm.model_generate = "claude-opus-5-generate"

    status = provider_status(cfg, env={"ANTHROPIC_API_KEY": _SECRET_VALUE})

    assert status["llm"]["provider"] == "local"
    assert status["llm"]["model_classify"] == "claude-opus-5-classify"
    assert status["llm"]["model_generate"] == "claude-opus-5-generate"
    assert status["embedding"]["provider"] == "local"
    assert status["embedding"]["model"] == "all-MiniLM-L6-v2"
    assert status["embedding"]["dimension"] == 384


def test_provider_status_discloses_no_credential_value():
    cfg = AppConfig()
    cfg.embedding.provider = "openai"

    status = provider_status(
        cfg,
        env={"ANTHROPIC_API_KEY": _SECRET_VALUE, "OPENAI_API_KEY": _SECRET_VALUE},
    )

    assert _SECRET_VALUE not in json.dumps(status)


def test_secret_status_discloses_no_credential_value():
    cfg = AppConfig()

    status = secret_status(cfg, env={"ANTHROPIC_API_KEY": _SECRET_VALUE})

    assert _SECRET_VALUE not in json.dumps(status)
    assert all(isinstance(value, bool) for value in status.values())


def test_secret_status_names_the_env_var_for_the_active_llm_provider():
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"

    status = secret_status(cfg, env={})

    assert status["ANTHROPIC_API_KEY"] is False
    assert "OPENAI_API_KEY" not in status


def test_secret_status_marks_a_present_key_as_set():
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"

    status = secret_status(cfg, env={"ANTHROPIC_API_KEY": _SECRET_VALUE})

    assert status["ANTHROPIC_API_KEY"] is True


def test_secret_status_treats_an_empty_value_as_unset():
    """An empty `ANTHROPIC_API_KEY=` line in `.env` is not a configured key,
    and the factory's `_require_key` already rejects it.
    """
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"

    status = secret_status(cfg, env={"ANTHROPIC_API_KEY": ""})

    assert status["ANTHROPIC_API_KEY"] is False


def test_secret_status_covers_both_providers_when_they_differ():
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"
    cfg.embedding.provider = "openai"

    status = secret_status(cfg, env={})

    assert status["ANTHROPIC_API_KEY"] is False
    assert status["OPENAI_API_KEY"] is False


def test_local_providers_require_no_api_key():
    """spec: ai-provider § "Local providers need no key"."""
    cfg = AppConfig()
    cfg.llm.provider = "local"
    cfg.embedding.provider = "local"

    status = secret_status(cfg, env={})

    assert "ANTHROPIC_API_KEY" not in status
    assert "OPENAI_API_KEY" not in status


def test_provider_status_embeds_the_secret_presence_report():
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"

    status = provider_status(cfg, env={})

    assert status["secrets"]["ANTHROPIC_API_KEY"] is False


def test_status_and_factory_share_one_env_var_table():
    """Exactly one place may know which provider needs which variable —
    otherwise the console can report a key as satisfied that the factory
    then rejects.
    """
    assert LLM_API_KEY_ENV_VARS["anthropic"] == "ANTHROPIC_API_KEY"
    assert LLM_API_KEY_ENV_VARS["openai"] == "OPENAI_API_KEY"
    assert LLM_API_KEY_ENV_VARS["local"] is None
    assert EMBEDDING_API_KEY_ENV_VARS["openai"] == "OPENAI_API_KEY"
    assert EMBEDDING_API_KEY_ENV_VARS["local"] is None
