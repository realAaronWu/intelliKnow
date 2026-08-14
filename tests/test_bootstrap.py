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

import faiss
import numpy as np
import pytest

from app.bootstrap import bootstrap
from app.config_service import ConfigService
from app.providers.local_embedding import SentenceTransformerEmbedding
from app.providers.local_llm import LocalLLM
from app.rag.index_meta import write_meta

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


def test_channel_proxy_urls_are_loaded_from_environment(local_config):
    result = bootstrap(
        config_path=local_config,
        env={
            "TELEGRAM_PROXY_URL": "socks5://127.0.0.1:8119",
            "WHATSAPP_PROXY_URL": "http://127.0.0.1:8118",
        },
    )

    assert result.telegram_proxy_url == "socks5://127.0.0.1:8119"
    assert result.whatsapp_proxy_url == "http://127.0.0.1:8118"


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
    """spec: ai-provider § "Missing API key for a remote provider".

    The shipped config's default backend (`local`) needs no credential, so
    this test switches to the remote (`anthropic`) demo path explicitly —
    the same way `local_config` switches the other direction — rather than
    relying on the shipped default being a remote provider.
    """
    import yaml

    dest = tmp_path / "config.yaml"
    shutil.copy(SHIPPED_CONFIG, dest)
    raw = yaml.safe_load(dest.read_text())
    raw["llm"]["provider"] = "anthropic"
    dest.write_text(yaml.safe_dump(raw, sort_keys=False))

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        bootstrap(config_path=dest, env={})


# --- Embedding-immutability guard (carry-forward item b) ------------------------
#
# `app/rag/index_meta.py::assert_compatible` exists and is unit-tested
# directly (tests/test_index_meta.py), but until it is registered as a
# `ConfigService` guard here, an embedding-model change is only ever
# noticed at the next startup — `assert_compatible` is never actually
# called on a live `update()`. These tests exercise the wiring, not the
# check's own logic.


@pytest.fixture
def local_config_with_faiss_dir(tmp_path: Path) -> tuple[Path, Path]:
    """The shipped config, switched to local backends and pointed at a
    `faiss_dir` under `tmp_path` so a test can plant an index there
    without touching the real `./data` directory.
    """
    import yaml

    dest = tmp_path / "config.yaml"
    shutil.copy(SHIPPED_CONFIG, dest)
    faiss_dir = tmp_path / "faiss"
    raw = yaml.safe_load(dest.read_text())
    raw["llm"]["provider"] = "local"
    raw["embedding"]["provider"] = "local"
    raw["storage"]["faiss_dir"] = str(faiss_dir)
    dest.write_text(yaml.safe_dump(raw, sort_keys=False))
    return dest, faiss_dir


def _plant_index_with_vectors(faiss_dir: Path, slug: str, model: str, dimension: int) -> None:
    faiss_dir.mkdir(parents=True, exist_ok=True)
    write_meta(faiss_dir, model=model, dimension=dimension)
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
    vectors = np.eye(1, dimension, dtype="float32")
    ids = np.array([1], dtype="int64")
    index.add_with_ids(vectors, ids)
    faiss.write_index(index, str(faiss_dir / f"{slug}.index"))


def test_embedding_model_change_is_rejected_at_update_time(local_config_with_faiss_dir):
    config_path, faiss_dir = local_config_with_faiss_dir
    _plant_index_with_vectors(faiss_dir, "hr", model="all-MiniLM-L6-v2", dimension=384)
    app = bootstrap(config_path=config_path, env={})

    with pytest.raises(ValueError, match="all-MiniLM-L6-v2"):
        app.config_service.update({"embedding": {"model": "text-embedding-3-small"}})

    assert app.config_service.current.embedding.model == "all-MiniLM-L6-v2"


def test_embedding_model_kept_the_same_is_accepted(local_config_with_faiss_dir):
    config_path, faiss_dir = local_config_with_faiss_dir
    _plant_index_with_vectors(faiss_dir, "hr", model="all-MiniLM-L6-v2", dimension=384)
    app = bootstrap(config_path=config_path, env={})

    updated = app.config_service.update({"orchestrator": {"confidence_threshold": 0.85}})

    assert updated.embedding.model == "all-MiniLM-L6-v2"
    assert updated.orchestrator.confidence_threshold == 0.85


def test_embedding_model_change_permitted_before_any_document_is_indexed(
    local_config_with_faiss_dir,
):
    config_path, _faiss_dir = local_config_with_faiss_dir
    app = bootstrap(config_path=config_path, env={})

    updated = app.config_service.update({"embedding": {"model": "a-different-model"}})

    assert updated.embedding.model == "a-different-model"


# --- The guard at startup, not only at update() -------------------------------
#
# Registering `assert_compatible` as a `ConfigService` guard only ever
# covered `update()`. The normal way an operator changes the embedding
# model is editing `config.yaml` and restarting — the one path that was
# unchecked — so the guard was inert exactly where it mattered.


def test_startup_rejects_a_config_whose_model_differs_from_the_built_index(
    local_config_with_faiss_dir,
):
    config_path, faiss_dir = local_config_with_faiss_dir
    _plant_index_with_vectors(faiss_dir, "hr", model="the-model-that-built-it", dimension=384)

    with pytest.raises(ValueError) as excinfo:
        bootstrap(config_path=config_path, env={})

    message = str(excinfo.value)
    assert "the-model-that-built-it" in message
    assert "all-MiniLM-L6-v2" in message


def test_startup_accepts_a_config_matching_the_built_index(local_config_with_faiss_dir):
    config_path, faiss_dir = local_config_with_faiss_dir
    _plant_index_with_vectors(faiss_dir, "hr", model="all-MiniLM-L6-v2", dimension=384)

    app = bootstrap(config_path=config_path, env={})

    assert app.config.embedding.model == "all-MiniLM-L6-v2"


def test_startup_permits_any_model_on_a_fresh_install(local_config_with_faiss_dir):
    config_path, _faiss_dir = local_config_with_faiss_dir

    app = bootstrap(config_path=config_path, env={})

    assert app.config.embedding.model == "all-MiniLM-L6-v2"


def test_reload_rejects_an_externally_edited_embedding_model(local_config_with_faiss_dir):
    """`reload()` re-reads the file an operator just edited, so it needs the
    same guard `update()` gets.
    """
    import yaml

    config_path, faiss_dir = local_config_with_faiss_dir
    _plant_index_with_vectors(faiss_dir, "hr", model="all-MiniLM-L6-v2", dimension=384)
    app = bootstrap(config_path=config_path, env={})

    raw = yaml.safe_load(config_path.read_text())
    raw["embedding"]["model"] = "text-embedding-3-small"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))

    with pytest.raises(ValueError, match="all-MiniLM-L6-v2"):
        app.config_service.reload()

    assert app.config_service.current.embedding.model == "all-MiniLM-L6-v2"


def test_caller_supplied_guards_run_alongside_the_embedding_guard(local_config_with_faiss_dir):
    config_path, _faiss_dir = local_config_with_faiss_dir
    calls: list[str] = []

    def extra_guard(old, new) -> None:
        calls.append("extra")

    app = bootstrap(config_path=config_path, env={}, guards=(extra_guard,))

    app.config_service.update({"orchestrator": {"confidence_threshold": 0.85}})

    assert calls == ["extra"]
