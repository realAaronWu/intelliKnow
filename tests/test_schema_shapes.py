"""Every structured-output schema in `app/` is a shape the API accepts.

The Anthropic API rejects any `type: "object"` schema node that does not
set `additionalProperties: false`, with a 400. No other provider in this
codebase enforces it, so a schema written and tested against the local or
OpenAI providers passes every test and only fails the moment
`llm.provider` is switched to `anthropic` — which is exactly how that
defect reached a live run.

The guard that was added for it lived inside `AnthropicLLM` and raised
`ProviderError`, which recreated the original failure in softer form:
`suggest_intent` catches `ProviderError`, so a future schema missing the
key would have filed every uploaded document under the fallback space
again — logged this time, but still wrong — and only when the Anthropic
provider happened to be the configured one.

So the check lives in `app/providers/schema_validation.py` (provider-
independent, and consistent with that module's own documented convention
that a bad *schema* is a caller bug rather than a backend failure), it
raises a non-`ProviderError` type that no caller's fallback path
swallows, and the sweep below applies it to every schema constant in
`app/` at test time — no configured provider required.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import app
from app.providers.base import ProviderError
from app.providers.schema_validation import InvalidSchemaError, validate_schema_shape


def _module_level_schemas() -> list[tuple[str, str, dict]]:
    """Every module-level `*_SCHEMA` dict under the `app` package.

    Discovered by walking the package rather than listed by hand, so a
    schema added in a later increment is covered the moment it exists —
    which is the only way this stays true of code nobody has written yet.
    """
    found: list[tuple[str, str, dict]] = []
    for module_info in pkgutil.walk_packages(app.__path__, prefix="app."):
        module = importlib.import_module(module_info.name)
        for attribute in dir(module):
            if not attribute.endswith("_SCHEMA"):
                continue
            value = getattr(module, attribute)
            if isinstance(value, dict):
                found.append((module_info.name, attribute, value))
    return found


def test_the_sweep_actually_finds_the_known_schemas():
    """A discovery sweep that silently found nothing would pass forever."""
    discovered = {(module, name) for module, name, _ in _module_level_schemas()}

    assert ("app.ingest.classify_doc", "_SUGGEST_SCHEMA") in discovered
    assert ("app.rag.tables", "_TABLE_SCHEMA") in discovered


@pytest.mark.parametrize(
    "module_name,attribute,schema",
    _module_level_schemas(),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_every_schema_in_app_is_accepted_by_every_provider(module_name, attribute, schema):
    validate_schema_shape(schema, path=f"{module_name}.{attribute}")


class TestValidateSchemaShape:
    def test_a_root_object_without_the_key_is_rejected(self):
        with pytest.raises(InvalidSchemaError) as excinfo:
            validate_schema_shape({"type": "object", "properties": {"a": {"type": "string"}}})

        assert "additionalProperties" in str(excinfo.value)

    def test_a_nested_object_without_the_key_is_rejected(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "metadata": {"type": "object", "properties": {"n": {"type": "number"}}}
            },
        }

        with pytest.raises(InvalidSchemaError, match="metadata"):
            validate_schema_shape(schema)

    def test_an_object_inside_array_items_is_rejected(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rows": {"type": "array", "items": {"type": "object", "properties": {}}}
            },
        }

        with pytest.raises(InvalidSchemaError, match="items"):
            validate_schema_shape(schema)

    def test_a_well_formed_schema_passes(self):
        validate_schema_shape(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}
                },
            }
        )

    def test_the_error_is_not_a_provider_error(self):
        """The whole point of the type change: a caller-supplied schema
        that is malformed is a bug in this codebase, not a backend
        failure, and every `except ProviderError` fallback in the app —
        intent suggestion, table repair — must not absorb it into a
        silent default.
        """
        with pytest.raises(InvalidSchemaError) as excinfo:
            validate_schema_shape({"type": "object"})

        assert not isinstance(excinfo.value, ProviderError)
