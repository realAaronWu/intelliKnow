"""Test-plan §1 — Configuration schema.

Source: docs/superpowers/test-plans/01-foundation-tests.md §1

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
        },
        "embedding": {
            "provider": "local",
            "model": "all-MiniLM-L6-v2",
            "dimension": 384,
            "batch_size": 64,
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
    assert cfg.llm.model_classify == "claude-opus-5"
    assert cfg.llm.model_generate == "claude-opus-5"
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
    assert cfg.llm.model_classify == "claude-opus-5"
    assert cfg.orchestrator.confidence_threshold == 0.70
    slugs = {space.slug for space in cfg.intent_spaces}
    assert {"hr", "legal", "finance", "operations", "general"} <= slugs


# --- 1.14 .env.example contents ----------------------------------------------


def test_1_14_env_example_contents():
    text = ENV_EXAMPLE.read_text()
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TEAMS_APP_ID",
        "TEAMS_APP_PASSWORD",
        "CREDENTIAL_ENCRYPTION_KEY",
        "ADMIN_PASSWORD",
    ):
        assert name in text

    # every KEY=... assignment line must have an empty value — no real secrets
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", stripped)
        assert match is not None, f"unexpected line in .env.example: {line!r}"
        key, value = match.groups()
        assert value == "", f"{key} has a non-empty value in .env.example"
