"""Runtime configuration service.

Owns `config.yaml` at runtime: reads it, applies validated partial patches,
and rewrites it atomically. Validation — schema first, then any registered
guards — always happens before any write, so a rejected patch leaves both
the file on disk and the in-memory config byte-identical to before.

See `app/config.py` for the validated schema (`AppConfig`, `load_config`)
this service builds on.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml

from app.config import AppConfig, load_config


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `patch` onto `base`, without mutating either.

    Dict values are merged key-by-key; any other value type (including
    lists) in `patch` replaces the corresponding value in `base` wholesale.
    """
    merged = dict(base)
    for key, patch_value in patch.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(patch_value, dict):
            merged[key] = _deep_merge(base_value, patch_value)
        else:
            merged[key] = patch_value
    return merged


#: A guard inspects `(current_config, proposed_config)` and raises to reject
#: the update. Returning `None` accepts it.
Guard = Callable[[AppConfig, AppConfig], None]


class ConfigService:
    """Reads, patches, and atomically rewrites `config.yaml` at runtime.

    `guards` are cross-cutting rules that the schema cannot express because
    they depend on state outside the config file. Each runs after schema
    validation and before anything is written, so a rejection leaves both
    disk and memory untouched.

    The motivating case is `spec: configuration` § "Immutable embedding
    settings once documents exist": the embedding model and dimension may
    not change while indexed documents exist. That check needs
    `index_meta.json`, which plan 03 creates, so only the seam lives here —
    without it plan 03 would have to retrofit this class.
    """

    def __init__(
        self,
        path: Path,
        config: AppConfig,
        guards: Sequence[Guard] = (),
    ) -> None:
        self._path = Path(path)
        self._current = config
        self._guards: tuple[Guard, ...] = tuple(guards)

    @classmethod
    def load(cls, path: Path, guards: Sequence[Guard] = ()) -> "ConfigService":
        """Load and validate the config file at `path`.

        Delegates to `app.config.load_config`, so a missing file is
        populated with documented defaults exactly as it is there.
        """
        path = Path(path)
        config = load_config(path)
        return cls(path, config, guards)

    @property
    def current(self) -> AppConfig:
        """The in-memory config as of the last load, update, or reload."""
        return self._current

    def reload(self) -> AppConfig:
        """Re-read the config file from disk, picking up external edits.

        Guards run against `(current, on-disk)` before the new config is
        adopted, so a vetoed reload raises and leaves `current` untouched.
        An operator editing `config.yaml` by hand is the *most* likely way
        a guarded setting changes — a guard that only covered `update()`
        would be inert on the path that actually matters.
        """
        new_config = load_config(self._path)
        for guard in self._guards:
            guard(self._current, new_config)
        self._current = new_config
        return self._current

    def update(self, patch: dict[str, Any]) -> AppConfig:
        """Deep-merge `patch` onto the file's current contents and persist it.

        Validation happens before any write: an invalid merged result raises
        `ValueError` (pydantic's `ValidationError` is a `ValueError`
        subclass) and leaves the file on disk, its `.bak` sibling, and
        `current` all untouched. Registered guards run next, before any
        write too, so a vetoed update has the same no-side-effect
        guarantee.

        On a valid patch: the previous file contents are copied to a `.bak`
        sibling, then the new contents are written to a temp file in the
        same directory and atomically renamed over the config file via
        `os.replace`. No `*.tmp` file survives either the success or the
        failure path.
        """
        old_bytes = self._path.read_bytes()
        raw = yaml.safe_load(old_bytes) or {}
        merged = _deep_merge(raw, patch)

        # Validate before touching anything on disk.
        new_config = AppConfig.model_validate(merged)

        # Then the guards, still before any write, so a veto leaves the file,
        # its `.bak` sibling, and `current` exactly as they were.
        for guard in self._guards:
            guard(self._current, new_config)

        new_content = yaml.safe_dump(
            new_config.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        )

        backup_path = self._path.with_suffix(self._path.suffix + ".bak")
        backup_path.write_bytes(old_bytes)

        fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as tmp_file:
                tmp_file.write(new_content)
            os.replace(tmp_name, self._path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise

        self._current = new_config
        return self._current
