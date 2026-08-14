"""The single composition root: load secrets, load config, build providers.

`spec: configuration` § "Secrets separated from configuration" says secrets
come from environment variables *or a `.env` file*, and `.env.example` tells
the operator to copy it to `.env`. Something has to actually load that file,
and it must happen exactly once, before anything reads `os.environ` — so it
happens here and nowhere else.

Increments 05 (channels) and 06 (admin console) both need "config plus the
three providers"; this exists so they share one wiring instead of each
inventing their own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from app.config import AppConfig, load_config
from app.config_service import ConfigService, Guard
from app.providers.base import EmbeddingProvider, LLMProvider
from app.providers.factory import build_embedding_provider, build_llm_provider
from app.rag.index_meta import assert_compatible

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_ENV_FILE = Path(".env")


@dataclass(frozen=True)
class Application:
    """Everything the entry points need, wired once."""

    config_service: ConfigService
    classify_llm: LLMProvider
    generate_llm: LLMProvider
    embedding: EmbeddingProvider
    admin_password: str | None = None
    credential_encryption_key: str | None = None

    @property
    def config(self) -> AppConfig:
        """The effective config as of the last load, update, or reload."""
        return self.config_service.current


def bootstrap(
    config_path: Path = DEFAULT_CONFIG_PATH,
    env_file: Path = DEFAULT_ENV_FILE,
    env: Mapping[str, str] | None = None,
    guards: tuple[Guard, ...] = (),
) -> Application:
    """Load `.env`, load `config.yaml`, and build the configured providers.

    A missing `env_file` is not an error — an operator may export the
    variables directly. Real environment variables take precedence over the
    file, so exporting a value for one run is not silently overridden by a
    stale `.env`.

    Credential validation happens here, as a side effect of building the
    providers: a remote backend with no API key fails with a `RuntimeError`
    naming the missing variable, per `spec: ai-provider` § "Startup
    credential validation".

    The embedding-immutability check
    (`app/rag/index_meta.py::assert_compatible`) runs here in both of the
    two ways it has to:

    - **directly, against the config just loaded**, so a `config.yaml`
      edited by hand and picked up at the next restart — the ordinary way
      an operator changes models — fails loudly at startup rather than
      silently degrading every answer; and
    - **as a `ConfigService` guard**, adapted exactly as its own docstring
      suggests (`lambda old, new: assert_compatible(new, faiss_dir)`), so
      a live `update()` or `reload()` is rejected too.

    Any caller-supplied `guards` run in addition to it.
    """
    load_dotenv(env_file, override=False)
    env = env if env is not None else os.environ

    # Read once, unguarded, purely to learn `storage.faiss_dir` — the
    # embedding guard closes over it. `ConfigService.load` below re-reads
    # the (by then certainly existing) file to build the real, guarded
    # service.
    faiss_dir = Path(load_config(config_path).storage.faiss_dir)
    embedding_guard: Guard = lambda old, new: assert_compatible(new, faiss_dir)

    config_service = ConfigService.load(config_path, guards=(embedding_guard, *guards))
    cfg = config_service.current

    # Guards only ever see a *change* to a live config. Editing
    # `config.yaml` and restarting changes nothing at runtime, so the
    # loaded config has to be checked against the index directly, here, or
    # the most likely way an operator swaps models goes unchecked.
    assert_compatible(cfg, faiss_dir)

    return Application(
        config_service=config_service,
        classify_llm=build_llm_provider(cfg, role="classify", env=env),
        generate_llm=build_llm_provider(cfg, role="generate", env=env),
        embedding=build_embedding_provider(cfg, env=env),
        admin_password=env.get("ADMIN_PASSWORD"),
        credential_encryption_key=env.get("CREDENTIAL_ENCRYPTION_KEY"),
    )
