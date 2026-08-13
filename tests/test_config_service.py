"""Test-plan §2 — ConfigService.

Source: docs/superpowers/test-plans/01-foundation-tests.md §2

Each test copies the shipped `config.yaml` into a pytest `tmp_path` rather
than mutating the repo-root file, per the task-2 brief.
"""

import shutil
from pathlib import Path

import pytest
import yaml

from app.config import AppConfig
from app.config_service import ConfigService

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPO_ROOT / "config.yaml"


@pytest.fixture
def config_path(tmp_path) -> Path:
    dest = tmp_path / "config.yaml"
    shutil.copy(SHIPPED_CONFIG, dest)
    return dest


# --- 2.1 Update threshold to 0.85 -------------------------------------------


def test_2_1_update_threshold_reflected_in_current_and_on_disk(config_path):
    service = ConfigService.load(config_path)

    updated = service.update({"orchestrator": {"confidence_threshold": 0.85}})

    assert updated.orchestrator.confidence_threshold == 0.85
    assert service.current.orchestrator.confidence_threshold == 0.85

    fresh = ConfigService.load(config_path)
    assert fresh.current.orchestrator.confidence_threshold == 0.85


# --- 2.2 Update with threshold 9.9 ------------------------------------------


def test_2_2_invalid_update_raises_and_leaves_state_untouched(config_path):
    original_bytes = config_path.read_bytes()
    service = ConfigService.load(config_path)
    assert service.current.orchestrator.confidence_threshold == 0.70

    with pytest.raises(ValueError):
        service.update({"orchestrator": {"confidence_threshold": 9.9}})

    assert service.current.orchestrator.confidence_threshold == 0.70
    assert config_path.read_bytes() == original_bytes


# --- 2.3 Update writes backup ------------------------------------------------


def test_2_3_update_writes_backup_with_previous_value(config_path):
    service = ConfigService.load(config_path)

    service.update({"orchestrator": {"confidence_threshold": 0.85}})

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    assert backup_path.exists()
    backup_data = yaml.safe_load(backup_path.read_text())
    assert backup_data["orchestrator"]["confidence_threshold"] == 0.70


# --- 2.4 Update leaves no temp file ------------------------------------------


def test_2_4_no_tmp_file_left_after_update(config_path):
    service = ConfigService.load(config_path)

    service.update({"orchestrator": {"confidence_threshold": 0.85}})
    assert list(config_path.parent.glob("*.tmp")) == []

    with pytest.raises(ValueError):
        service.update({"orchestrator": {"confidence_threshold": 9.9}})
    assert list(config_path.parent.glob("*.tmp")) == []


# --- 2.5 Partial patch merges ------------------------------------------------


def test_2_5_partial_patch_merges_leaving_siblings_untouched(config_path):
    service = ConfigService.load(config_path)
    assert service.current.rag.relevance_floor == 0.45

    updated = service.update({"rag": {"final_top_k": 8}})

    assert updated.rag.final_top_k == 8
    assert updated.rag.relevance_floor == 0.45


# --- 2.6 Update intent space keywords ----------------------------------------


def test_2_6_update_intent_space_keywords(config_path):
    service = ConfigService.load(config_path)
    spaces = [space.model_dump(mode="json") for space in service.current.intent_spaces]
    for space in spaces:
        if space["slug"] == "hr":
            space["keywords"].append("parental-leave")

    updated = service.update({"intent_spaces": spaces})

    hr_space = next(space for space in updated.intent_spaces if space.slug == "hr")
    assert "parental-leave" in hr_space.keywords


# --- 2.7 Reload picks up an external edit ------------------------------------


def test_2_7_reload_picks_up_external_edit(config_path):
    service = ConfigService.load(config_path)
    assert service.current.orchestrator.confidence_threshold == 0.70

    raw = yaml.safe_load(config_path.read_text())
    raw["orchestrator"]["confidence_threshold"] = 0.55
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))

    reloaded = service.reload()

    assert reloaded.orchestrator.confidence_threshold == 0.55
    assert service.current.orchestrator.confidence_threshold == 0.55


def test_reload_runs_the_guards_against_the_edited_file(config_path):
    """Editing `config.yaml` by hand and restarting — or reloading — is the
    normal way an operator changes a setting, so a guard that only runs on
    `update()` never sees the change it exists to reject.
    """
    def refuse_embedding_change(old: AppConfig, new: AppConfig) -> None:
        if old.embedding.model != new.embedding.model:
            raise ValueError("embedding.model is immutable while documents exist")

    service = ConfigService.load(config_path, guards=[refuse_embedding_change])
    original_model = service.current.embedding.model

    raw = yaml.safe_load(config_path.read_text())
    raw["embedding"]["model"] = "a-different-model"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))

    with pytest.raises(ValueError, match="immutable"):
        service.reload()

    assert service.current.embedding.model == original_model


def test_a_passing_guard_lets_a_reload_through(config_path):
    calls: list[tuple[float, float]] = []

    def record(old: AppConfig, new: AppConfig) -> None:
        calls.append((old.orchestrator.confidence_threshold, new.orchestrator.confidence_threshold))

    service = ConfigService.load(config_path, guards=[record])

    raw = yaml.safe_load(config_path.read_text())
    raw["orchestrator"]["confidence_threshold"] = 0.55
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))

    reloaded = service.reload()

    assert reloaded.orchestrator.confidence_threshold == 0.55
    assert calls == [(0.70, 0.55)]


# --- 2.8 Failed update leaves no backup churn --------------------------------


def test_2_8_failed_update_does_not_overwrite_existing_backup(config_path):
    service = ConfigService.load(config_path)
    service.update({"orchestrator": {"confidence_threshold": 0.85}})

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    backup_bytes_after_first_update = backup_path.read_bytes()

    with pytest.raises(ValueError):
        service.update({"orchestrator": {"confidence_threshold": 9.9}})

    assert backup_path.read_bytes() == backup_bytes_after_first_update


# --- Update guards -----------------------------------------------------------
#
# `spec: configuration` § "Immutable embedding settings once documents exist"
# needs to reject an embedding-model change while indexed documents exist.
# The check itself needs `index_meta.json`, which plan 03 creates, so only
# the seam lives here — otherwise plan 03 would have to retrofit
# `ConfigService`.


def test_guard_can_veto_an_update(config_path):
    original_bytes = config_path.read_bytes()

    def refuse_embedding_change(old: AppConfig, new: AppConfig) -> None:
        if old.embedding.model != new.embedding.model:
            raise ValueError("embedding.model is immutable while documents exist")

    service = ConfigService.load(config_path, guards=[refuse_embedding_change])

    with pytest.raises(ValueError, match="immutable"):
        service.update({"embedding": {"model": "some-other-model"}})

    assert service.current.embedding.model == "all-MiniLM-L6-v2"
    assert config_path.read_bytes() == original_bytes


def test_vetoed_update_writes_neither_config_nor_backup(config_path):
    def always_refuse(old: AppConfig, new: AppConfig) -> None:
        raise ValueError("nope")

    service = ConfigService.load(config_path, guards=[always_refuse])
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")

    with pytest.raises(ValueError):
        service.update({"orchestrator": {"confidence_threshold": 0.85}})

    assert not backup_path.exists()
    assert list(config_path.parent.glob("*.tmp")) == []


def test_guard_receives_the_current_and_proposed_configs(config_path):
    seen: list[tuple[float, float]] = []

    def record(old: AppConfig, new: AppConfig) -> None:
        seen.append(
            (old.orchestrator.confidence_threshold, new.orchestrator.confidence_threshold)
        )

    service = ConfigService.load(config_path, guards=[record])
    service.update({"orchestrator": {"confidence_threshold": 0.85}})

    assert seen == [(0.70, 0.85)]


def test_a_passing_guard_lets_the_update_through(config_path):
    def allow(old: AppConfig, new: AppConfig) -> None:
        return None

    service = ConfigService.load(config_path, guards=[allow])

    updated = service.update({"orchestrator": {"confidence_threshold": 0.85}})

    assert updated.orchestrator.confidence_threshold == 0.85
    assert ConfigService.load(config_path).current.orchestrator.confidence_threshold == 0.85


def test_guards_run_in_order_and_stop_at_the_first_veto(config_path):
    calls: list[str] = []

    def first(old: AppConfig, new: AppConfig) -> None:
        calls.append("first")
        raise ValueError("first says no")

    def second(old: AppConfig, new: AppConfig) -> None:
        calls.append("second")

    service = ConfigService.load(config_path, guards=[first, second])

    with pytest.raises(ValueError, match="first says no"):
        service.update({"orchestrator": {"confidence_threshold": 0.85}})

    assert calls == ["first"]


def test_no_guards_is_the_default(config_path):
    service = ConfigService.load(config_path)

    updated = service.update({"orchestrator": {"confidence_threshold": 0.85}})

    assert updated.orchestrator.confidence_threshold == 0.85


def test_runtime_update_accepts_only_live_classifier_and_gate_settings(config_path):
    service = ConfigService.load(config_path)

    updated = service.update_runtime(
        {
            "orchestrator": {"confidence_threshold": 0.82},
            "rag": {"relevance_floor": 0.51},
        }
    )

    assert updated.orchestrator.confidence_threshold == 0.82
    assert updated.rag.relevance_floor == 0.51


def test_runtime_update_accepts_intent_spaces_as_one_live_setting(config_path):
    service = ConfigService.load(config_path)
    spaces = service.current.model_dump(mode="json")["intent_spaces"]
    spaces[0]["keywords"].append("parental leave")

    updated = service.update_runtime({"intent_spaces": spaces})

    assert "parental leave" in updated.intent_spaces[0].keywords


@pytest.mark.parametrize(
    "patch, field",
    [
        ({"llm": {"model_generate": "another-model"}}, "llm.model_generate"),
        ({"embedding": {"model": "another-embedding"}}, "embedding.model"),
        ({"rag": {"final_top_k": 3}}, "rag.final_top_k"),
        ({"storage": {"faiss_dir": "/tmp/other"}}, "storage.faiss_dir"),
    ],
)
def test_runtime_update_rejects_restart_required_fields_without_side_effects(
    config_path, patch, field
):
    service = ConfigService.load(config_path)
    before = config_path.read_bytes()

    with pytest.raises(ValueError, match=field):
        service.update_runtime(patch)

    assert config_path.read_bytes() == before
