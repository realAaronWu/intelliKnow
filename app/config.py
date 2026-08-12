"""Typed configuration schema for IntelliKnow KMS.

Single source of truth: `config.yaml` at the repo root, validated against the
models below. Every model forbids unknown fields — configuration mistakes
must fail loudly rather than being silently dropped.

See `openspec/changes/add-intelliknow-kms/design.md` § Configuration for the
authoritative field list, defaults, and rationale.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.base import EffortLevel

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class _StrictModel(BaseModel):
    """Base class: unknown fields are a validation error, not a silent drop."""

    model_config = ConfigDict(extra="forbid")


class LLMConfig(_StrictModel):
    provider: Literal["anthropic", "openai", "local"] = "anthropic"
    model_classify: str = "claude-opus-5"
    model_generate: str = "claude-opus-5"
    timeout_seconds: int = Field(default=20, gt=0)
    max_retries: int = Field(default=2, ge=0)
    # Reasoning effort requested of a thinking-capable model. A tunable, so
    # it belongs here — it was previously inferred in provider code from the
    # substring "opus-5" in the model name.
    effort: EffortLevel = "low"
    # Only consulted when provider == "local": the OpenAI-compatible base URL
    # of the local model server. Ignored by the anthropic/openai backends,
    # which talk to their own fixed endpoints. Defaults to Ollama's
    # OpenAI-compatible endpoint.
    base_url: str = "http://localhost:11434/v1"


class EmbeddingConfig(_StrictModel):
    provider: Literal["local", "openai"] = "local"
    model: str = "all-MiniLM-L6-v2"
    dimension: int = Field(default=384, gt=0)
    batch_size: int = Field(default=64, gt=0)
    # Only the remote (OpenAI) embedding backend uses these; the local
    # backend makes no network call. Previously hard-coded in
    # `app/providers/openai_embedding.py`.
    timeout_seconds: int = Field(default=20, gt=0)
    max_retries: int = Field(default=2, ge=0)


class RAGConfig(_StrictModel):
    chunk_chars: int = Field(default=800, gt=0)
    chunk_overlap_chars: int = Field(default=100, ge=0)
    vector_top_n: int = Field(default=20, gt=0)
    # `ge=0`, deliberately not `gt=0`: `spec: knowledge-retrieval`
    # § "Keyword retrieval disabled by configuration" makes 0 a supported
    # setting that turns keyword retrieval off and leaves dense results to
    # answer alone. Tightening this to `gt=0` would silently break that
    # requirement.
    keyword_top_n: int = Field(default=20, ge=0)
    rrf_k: int = Field(default=60, gt=0)
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_candidates: int = Field(default=20, gt=0)
    final_top_k: int = Field(default=5, gt=0)
    max_context_chars: int = Field(default=6000, gt=0)
    # On sigmoid(cross-encoder score) — see design.md § Classification without
    # an LLM on the common path: "the previous 0.35 does not carry over".
    relevance_floor: float = Field(default=0.45, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _overlap_less_than_chunk(self) -> "RAGConfig":
        if self.chunk_overlap_chars >= self.chunk_chars:
            raise ValueError(
                "rag.chunk_overlap_chars "
                f"({self.chunk_overlap_chars}) must be strictly less than "
                f"rag.chunk_chars ({self.chunk_chars})"
            )
        return self


class OrchestratorConfig(_StrictModel):
    confidence_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    fallback_space: str = "general"
    centroid_temperature: float = Field(default=0.05, gt=0.0)
    escalate_to_llm: bool = True


class IntentSpace(_StrictModel):
    slug: str = Field(pattern=_SLUG_PATTERN.pattern)
    name: str
    description: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)


class ChannelConfig(_StrictModel):
    enabled: bool = False
    # A closed set, like every other provider-ish field: an unvalidated
    # `str | None` accepted typos such as "webook" silently. `None` means the
    # channel declares no mode (Teams, which has no polling/webhook choice to
    # make until plan 05 wires it up).
    mode: Literal["polling", "webhook"] | None = None
    max_message_chars: int = Field(gt=0)


class ChannelsConfig(_StrictModel):
    telegram: ChannelConfig = Field(
        default_factory=lambda: ChannelConfig(
            enabled=True, mode="polling", max_message_chars=4096
        )
    )
    teams: ChannelConfig = Field(
        default_factory=lambda: ChannelConfig(enabled=False, max_message_chars=28000)
    )


class IngestionConfig(_StrictModel):
    max_upload_mb: int = Field(default=25, gt=0)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [".pdf", ".docx", ".xlsx"]
    )


class StorageConfig(_StrictModel):
    sqlite_path: str = "./data/intelliknow.db"
    faiss_dir: str = "./data/faiss"
    upload_dir: str = "./data/uploads"


def _default_intent_spaces() -> list[IntentSpace]:
    return [
        IntentSpace(
            slug="hr",
            name="HR",
            description="Employee policies, leave, benefits, payroll, onboarding.",
            keywords=[
                "leave",
                "vacation",
                "salary",
                "band",
                "benefits",
                "onboarding",
                "appraisal",
            ],
        ),
        IntentSpace(
            slug="legal",
            name="Legal",
            description="Contracts, compliance, data protection, terms.",
            keywords=["contract", "NDA", "GDPR", "compliance", "liability", "clause"],
        ),
        IntentSpace(
            slug="finance",
            name="Finance",
            description="Expenses, reimbursement, budgets, invoicing, salary bands.",
            keywords=[
                "expense",
                "reimbursement",
                "invoice",
                "budget",
                "procurement",
                "tax",
            ],
        ),
        IntentSpace(
            slug="operations",
            name="Operations",
            description="Internal processes, tooling, facilities, IT requests.",
            keywords=["access", "laptop", "VPN", "ticket", "facilities", "process"],
        ),
        IntentSpace(
            slug="general",
            name="General",
            description="Fallback — searches every space.",
            keywords=[],
        ),
    ]


class AppConfig(_StrictModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    intent_spaces: list[IntentSpace] = Field(default_factory=_default_intent_spaces)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    public_base_url: str | None = None

    @model_validator(mode="after")
    def _slugs_unique(self) -> "AppConfig":
        slugs = [space.slug for space in self.intent_spaces]
        seen: set[str] = set()
        for slug in slugs:
            if slug in seen:
                raise ValueError(f"duplicate intent space slug: {slug!r}")
            seen.add(slug)
        return self

    @model_validator(mode="after")
    def _webhook_mode_requires_public_base_url(self) -> "AppConfig":
        """A webhook channel has nowhere to receive callbacks without a
        public URL, so the combination must fail at startup rather than at
        the first inbound message.
        """
        if self.public_base_url:
            return self
        for name, channel in (
            ("telegram", self.channels.telegram),
            ("teams", self.channels.teams),
        ):
            if channel.mode == "webhook":
                raise ValueError(
                    f"channels.{name}.mode is 'webhook' but public_base_url "
                    "is not set; webhook mode needs a publicly reachable "
                    "base URL for the platform to call back to"
                )
        return self

    @model_validator(mode="after")
    def _fallback_space_exists(self) -> "AppConfig":
        slugs = {space.slug for space in self.intent_spaces}
        if self.orchestrator.fallback_space not in slugs:
            raise ValueError(
                "orchestrator.fallback_space "
                f"{self.orchestrator.fallback_space!r} does not name an "
                "existing intent space"
            )
        return self


def load_config(path: Path) -> AppConfig:
    """Load and validate `config.yaml` from `path`.

    If no file exists at `path`, an `AppConfig` built from documented
    defaults is written there (creating parent directories as needed) and
    then returned, so a fresh checkout can start with no manual setup.
    """
    path = Path(path)
    if not path.exists():
        default_config = AppConfig()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                default_config.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
            )
        )
        return default_config

    with path.open("r") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)
