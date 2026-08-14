"""Test-plan §1 — Configuration schema.

Source: superpowers/test-plans/01-foundation-tests.md §1

NOTE on 1.1 relevance_floor: the test-plan table lists 0.35 for
`rag.relevance_floor`, but the authoritative config content in
openspec/changes/add-intelliknow-kms/design.md § Configuration ships
`relevance_floor: 0.45` and explicitly states (§ Classification without an
LLM on the common path): "the previous 0.35 does not carry over" — 0.35 was
the floor for the old bi-encoder-cosine gate; 0.45 is the floor for the new
sigmoid(cross-encoder score) gate. The task brief instructs config.yaml
values to be copied verbatim from design.md. This test therefore asserts
0.45, not the stale 0.35 in the test-plan table. See task-1-report.md for
the full note.

NOTE on 1.1 llm.provider/model: the demo uses Anthropic's fast model for
classification and generation. The local Ollama-compatible path remains
available by changing these three config values, but is not the shipped demo
default because an unavailable local server makes every answer fail.
"""

import copy
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AppConfig, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPO_ROOT / "config.yaml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _valid_config_dict() -> dict:
    """A minimal, fully valid config dict mirroring config.yaml's shape."""
    return {
        "llm": {
            "provider": "anthropic",
            "model_classify": "claude-opus-5",
            "model_generate": "claude-opus-5",
            "timeout_seconds": 20,
            "max_retries": 2,
            "effort": "low",
        },
        "embedding": {
            "provider": "local",
            "model": "all-MiniLM-L6-v2",
            "dimension": 384,
            "batch_size": 64,
            "timeout_seconds": 20,
            "max_retries": 2,
        },
        "rag": {
            "chunk_chars": 800,
            "chunk_overlap_chars": 100,
            "vector_top_n": 20,
            "keyword_top_n": 20,
            "rrf_k": 60,
            "rerank_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "rerank_candidates": 20,
            "final_top_k": 5,
            "max_context_chars": 6000,
            "relevance_floor": 0.45,
        },
        "orchestrator": {
            "confidence_threshold": 0.70,
            "fallback_space": "general",
            "centroid_temperature": 0.05,
            "escalate_to_llm": True,
        },
        "intent_spaces": [
            {
                "slug": "hr",
                "name": "HR",
                "description": "Employee policies, leave, benefits, payroll, onboarding.",
                "keywords": ["leave", "vacation", "salary"],
            },
            {
                "slug": "general",
                "name": "General",
                "description": "Fallback — searches every space.",
                "keywords": [],
            },
        ],
        "channels": {
            "telegram": {"enabled": True, "mode": "polling", "max_message_chars": 4096},
            "teams": {"enabled": False, "max_message_chars": 28000},
        },
        "ingestion": {
            "max_upload_mb": 25,
            "allowed_extensions": [".pdf", ".docx", ".xlsx"],
        },
        "storage": {
            "sqlite_path": "./data/intelliknow.db",
            "faiss_dir": "./data/faiss",
            "upload_dir": "./data/uploads",
        },
        "public_base_url": None,
    }


# --- 1.1 Load shipped config.yaml ---------------------------------------


def test_1_1_load_shipped_config_yaml():
    cfg = load_config(SHIPPED_CONFIG)
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model_classify == "claude-haiku-4-5"
    assert cfg.llm.model_generate == "claude-haiku-4-5"
    assert cfg.llm.base_url == "http://localhost:11434/v1"
    assert cfg.embedding.model == "all-MiniLM-L6-v2"
    assert cfg.embedding.dimension == 384
    assert cfg.orchestrator.confidence_threshold == 0.70
    assert cfg.rag.relevance_floor == 0.45


# --- 1.2 Default intent spaces -------------------------------------------


def test_1_2_default_intent_spaces():
    cfg = load_config(SHIPPED_CONFIG)
    slugs = {space.slug for space in cfg.intent_spaces}
    assert {"hr", "legal", "finance", "operations", "general"} <= slugs


# --- 1.3 Each space is complete -------------------------------------------


def test_1_3_each_space_is_complete():
    cfg = load_config(SHIPPED_CONFIG)
    for space in cfg.intent_spaces:
        assert space.description.strip() != ""
        assert isinstance(space.keywords, list)


# --- 1.4 Threshold above 1.0 ----------------------------------------------


def test_1_4_threshold_above_one_rejected():
    data = _valid_config_dict()
    data["orchestrator"]["confidence_threshold"] = 1.5
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


# --- 1.5 Threshold below 0.0 ----------------------------------------------


def test_1_5_threshold_below_zero_rejected():
    data = _valid_config_dict()
    data["orchestrator"]["confidence_threshold"] = -0.1
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


# --- 1.6 Relevance floor above 1.0 -----------------------------------------


def test_1_6_relevance_floor_above_one_rejected():
    data = _valid_config_dict()
    data["rag"]["relevance_floor"] = 1.2
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


# --- 1.7 Unknown top-level field --------------------------------------------


def test_1_7_unknown_top_level_field_rejected():
    data = _valid_config_dict()
    data["not_a_real_field"] = "surprise"
    with pytest.raises(ValidationError) as exc_info:
        AppConfig.model_validate(data)
    assert "not_a_real_field" in str(exc_info.value)


# --- 1.8 Unknown llm.provider value -----------------------------------------


def test_1_8_unknown_llm_provider_rejected():
    data = _valid_config_dict()
    data["llm"]["provider"] = "not-a-provider"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


# --- 1.9 chunk_overlap_chars >= chunk_chars ---------------------------------


def test_1_9_overlap_greater_than_or_equal_to_chunk_rejected():
    data = _valid_config_dict()
    data["rag"]["chunk_overlap_chars"] = data["rag"]["chunk_chars"]
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


# --- 1.10 Slug not kebab-case -----------------------------------------------


def test_1_10_slug_not_kebab_case_rejected():
    data = _valid_config_dict()
    data["intent_spaces"][0]["slug"] = "HR_Space"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


# --- 1.11 Duplicate slugs ----------------------------------------------------


def test_1_11_duplicate_slugs_rejected():
    data = _valid_config_dict()
    dup = copy.deepcopy(data["intent_spaces"][0])
    data["intent_spaces"].append(dup)
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


# --- 1.12 fallback_space names no existing space -----------------------------


def test_1_12_fallback_space_must_exist():
    data = _valid_config_dict()
    data["orchestrator"]["fallback_space"] = "nonexistent-space"
    with pytest.raises(ValidationError) as exc_info:
        AppConfig.model_validate(data)
    assert "nonexistent-space" in str(exc_info.value)


# --- 1.13 Missing config file ------------------------------------------------


def test_1_13_missing_config_file_writes_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    assert not path.exists()
    cfg = load_config(path)
    assert path.exists()
    assert cfg.llm.model_classify == "llama3.1"
    assert cfg.orchestrator.confidence_threshold == 0.70
    slugs = {space.slug for space in cfg.intent_spaces}
    assert {"hr", "legal", "finance", "operations", "general"} <= slugs


# --- Channel mode is a closed set, and webhook mode has a prerequisite -------


def test_channel_mode_rejects_an_unknown_value():
    """`mode` was an unvalidated `str | None` while every other provider-ish
    field used a Literal, so a typo like "webook" was accepted silently.
    """
    data = _valid_config_dict()
    data["channels"]["telegram"]["mode"] = "webook"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_channel_mode_accepts_polling_and_webhook():
    data = _valid_config_dict()
    data["channels"]["telegram"]["mode"] = "polling"
    assert AppConfig.model_validate(data).channels.telegram.mode == "polling"

    data["channels"]["telegram"]["mode"] = "webhook"
    data["public_base_url"] = "https://kms.example.com"
    assert AppConfig.model_validate(data).channels.telegram.mode == "webhook"


def test_channel_mode_may_be_omitted():
    data = _valid_config_dict()
    data["channels"]["teams"].pop("mode", None)
    assert AppConfig.model_validate(data).channels.teams.mode is None


def test_webhook_mode_requires_public_base_url():
    data = _valid_config_dict()
    data["channels"]["telegram"]["mode"] = "webhook"
    data["public_base_url"] = None

    with pytest.raises(ValidationError) as exc_info:
        AppConfig.model_validate(data)

    message = str(exc_info.value)
    assert "public_base_url" in message
    assert "telegram" in message


def test_webhook_mode_accepted_when_public_base_url_is_set():
    data = _valid_config_dict()
    data["channels"]["teams"]["mode"] = "webhook"
    data["public_base_url"] = "https://kms.example.com"

    cfg = AppConfig.model_validate(data)

    assert cfg.channels.teams.mode == "webhook"


def test_polling_mode_does_not_require_public_base_url():
    data = _valid_config_dict()
    data["channels"]["telegram"]["mode"] = "polling"
    data["public_base_url"] = None

    assert AppConfig.model_validate(data).public_base_url is None


# --- keyword_top_n = 0 is a supported setting, not a bug ---------------------


def test_keyword_top_n_accepts_zero():
    """`spec: knowledge-retrieval` § "Keyword retrieval disabled by
    configuration": zero disables keyword retrieval by design and retrieval
    proceeds on dense results alone. The bound is `ge=0`, not `gt=0` —
    tightening it would silently break that requirement.
    """
    data = _valid_config_dict()
    data["rag"]["keyword_top_n"] = 0

    assert AppConfig.model_validate(data).rag.keyword_top_n == 0


def test_keyword_top_n_rejects_a_negative_value():
    data = _valid_config_dict()
    data["rag"]["keyword_top_n"] = -1
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


# --- Tunables that must live in config.yaml, not in code ---------------------


def test_llm_effort_defaults_to_none_and_is_read_from_config():
    """`spec: configuration` § "Single configuration file": the reasoning
    effort used to be inferred in code from the substring "opus-5" in the
    model name, which is a tunable living outside config.yaml.

    The shipped default is `null` (opt-out), not a literal level: `effort`
    is rejected outright by some models (e.g. claude-haiku-4-5, Sonnet 4.5),
    so it must be omitted from the request unless an operator explicitly
    turns it on for a model that supports it.
    """
    cfg = load_config(SHIPPED_CONFIG)
    assert cfg.llm.effort is None


def test_llm_effort_accepts_null_meaning_unset():
    data = _valid_config_dict()
    data["llm"]["effort"] = None
    assert AppConfig.model_validate(data).llm.effort is None


def test_llm_effort_rejects_an_unsupported_level():
    data = _valid_config_dict()
    data["llm"]["effort"] = "turbo"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_llm_effort_accepts_every_supported_level():
    for level in ("low", "medium", "high", "xhigh", "max"):
        data = _valid_config_dict()
        data["llm"]["effort"] = level
        assert AppConfig.model_validate(data).llm.effort == level


def test_embedding_timeout_and_retries_are_configurable():
    """These were hard-coded as 20s / 2 retries in
    `app/providers/openai_embedding.py`, out of reach of config.yaml.
    """
    cfg = load_config(SHIPPED_CONFIG)
    assert cfg.embedding.timeout_seconds == 20
    assert cfg.embedding.max_retries == 2


def test_embedding_timeout_must_be_positive():
    data = _valid_config_dict()
    data["embedding"]["timeout_seconds"] = 0
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_llm_base_url_defaults_to_ollama_endpoint():
    """`base_url` is only consulted by the `local` backend, but it lives on
    `LLMConfig` unconditionally like every other tunable — see
    `spec: configuration` § "Single configuration file".
    """
    data = _valid_config_dict()
    assert AppConfig.model_validate(data).llm.base_url == "http://localhost:11434/v1"


def test_llm_base_url_is_configurable():
    data = _valid_config_dict()
    data["llm"]["base_url"] = "http://localhost:8000/v1"
    assert (
        AppConfig.model_validate(data).llm.base_url == "http://localhost:8000/v1"
    )


def test_embedding_max_retries_may_be_zero_but_not_negative():
    data = _valid_config_dict()
    data["embedding"]["max_retries"] = 0
    assert AppConfig.model_validate(data).embedding.max_retries == 0

    data["embedding"]["max_retries"] = -1
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


# --- 1.14 .env.example contents ----------------------------------------------


def test_1_14_env_example_contents():
    text = ENV_EXAMPLE.read_text()
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CREDENTIAL_ENCRYPTION_KEY",
        "ADMIN_PASSWORD",
    ):
        assert name in text
    for channel_secret in (
        "TELEGRAM_BOT_TOKEN=",
        "TEAMS_APP_ID=",
        "TEAMS_APP_PASSWORD=",
    ):
        assert channel_secret not in text

    # every KEY=... assignment line must have an empty value — no real secrets
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", stripped)
        assert match is not None, f"unexpected line in .env.example: {line!r}"
        key, value = match.groups()
        assert value == "", f"{key} has a non-empty value in .env.example"
