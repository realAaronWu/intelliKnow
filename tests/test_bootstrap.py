"""Tests for `app/bootstrap.py` — the single composition root.

`spec: configuration` § "Secrets separated from configuration" says secrets
come from environment variables *or a `.env` file*, and `.env.example` tells
the operator to copy it to `.env`. Nothing loaded that file, so following
the documented workflow failed with a missing-variable error.

These tests never construct a provider that reaches the network: the local
backends need no key, and the one remote case asserts the credential check
fires before any client is built.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.bootstrap import bootstrap
from app.config_service import ConfigService
from app.providers.local_embedding import SentenceTransformerEmbedding
from app.providers.local_llm import LocalLLM

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPO_ROOT / "config.yaml"


@pytest.fixture(autouse=True)
def restore_environ():
    """`load_dotenv` mutates `os.environ`; put it back afterwards."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def local_config(tmp_path: Path) -> Path:
    """The shipped config, switched to backends that need no credentials."""
    import yaml

    dest = tmp_path / "config.yaml"
    shutil.copy(SHIPPED_CONFIG, dest)
    raw = yaml.safe_load(dest.read_text())
    raw["llm"]["provider"] = "local"
    raw["embedding"]["provider"] = "local"
    dest.write_text(yaml.safe_dump(raw, sort_keys=False))
    return dest


def test_env_file_values_are_loaded_into_the_environment(tmp_path, local_config):
    env_file = tmp_path / ".env"
    env_file.write_text("INTELLIKNOW_TEST_TOKEN=from-dotenv\n")
    os.environ.pop("INTELLIKNOW_TEST_TOKEN", None)

    bootstrap(config_path=local_config, env_file=env_file)

    assert os.environ["INTELLIKNOW_TEST_TOKEN"] == "from-dotenv"


def test_a_real_environment_variable_wins_over_the_env_file(tmp_path, local_config):
    """An operator exporting a variable for one run must not be silently
    overridden by a stale `.env`.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("INTELLIKNOW_TEST_TOKEN=from-dotenv\n")
    os.environ["INTELLIKNOW_TEST_TOKEN"] = "from-real-environment"

    bootstrap(config_path=local_config, env_file=env_file)

    assert os.environ["INTELLIKNOW_TEST_TOKEN"] == "from-real-environment"


def test_a_missing_env_file_is_not_an_error(tmp_path, local_config):
    result = bootstrap(config_path=local_config, env_file=tmp_path / "nonexistent.env")

    assert result.config.llm.provider == "local"


def test_bootstrap_builds_config_service_and_all_three_providers(local_config):
    result = bootstrap(config_path=local_config, env={})

    assert isinstance(result.config_service, ConfigService)
    assert result.config is result.config_service.current
    assert isinstance(result.classify_llm, LocalLLM)
    assert isinstance(result.generate_llm, LocalLLM)
    assert isinstance(result.embedding, SentenceTransformerEmbedding)


def test_classify_and_generate_providers_use_their_configured_models(
    local_config,
):
    import yaml

    raw = yaml.safe_load(local_config.read_text())
    raw["llm"]["model_classify"] = "local-classify"
    raw["llm"]["model_generate"] = "local-generate"
    local_config.write_text(yaml.safe_dump(raw, sort_keys=False))

    result = bootstrap(config_path=local_config, env={})

    assert result.classify_llm._model == "local-classify"
    assert result.generate_llm._model == "local-generate"


def test_missing_credential_fails_startup_naming_the_env_var(tmp_path):
    """spec: ai-provider § "Missing API key for a remote provider"."""
    dest = tmp_path / "config.yaml"
    shutil.copy(SHIPPED_CONFIG, dest)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        bootstrap(config_path=dest, env={})
