"""Structured-output parsing and validation shared by every LLM provider.

`spec: ai-provider` § "Structured generation" requires that a schema-guided
`complete` call return "a parsed object conforming to that schema" — not
merely something that parsed as JSON. Syntactically valid but structurally
wrong payloads (`[1, 2]`, `42`, an object missing a required property) must
be rejected, because `LLMResult.parsed` is typed `dict | None` and callers
index into it directly.

A rejection here is deliberately *not* a `ProviderError`: per
§ "Structured generation returns malformed output" the provider retries the
call once, and only a second failure becomes a `ProviderError` naming the
violated schema. Providers therefore catch `SchemaViolation`, retry, and
build the final error message with `describe_schema`.

An invalid *schema* (as opposed to an invalid response) is a caller bug
rather than a backend failure, so `jsonschema.SchemaError` is left to
propagate unmasked instead of being retried. `validate_schema_shape`
follows the same convention for the one structural rule the schema
language itself cannot express — see its docstring.
"""

from __future__ import annotations

import json

import jsonschema


class SchemaViolation(Exception):
    """A structured response failed to parse or did not conform to the schema.

    Internal to the provider layer — callers outside it see the
    `ProviderError` a provider raises after its retry is exhausted.
    """


class InvalidSchemaError(ValueError):
    """A caller-supplied schema is malformed.

    Deliberately **not** a `ProviderError`. Nothing about a bad schema is
    a backend condition — it is a bug in this codebase, identical on every
    request and every provider — and the application's `ProviderError`
    fallback paths (`suggest_intent` files the document under the fallback
    space; `repair_table` keeps the raw extraction) exist to absorb
    transient backend trouble. Letting a schema bug take those paths is
    how "every document filed under `general`" happened once already, and
    a `ProviderError`-typed schema guard would have quietly recreated it.
    """


def validate_schema_shape(schema: dict, path: str = "schema") -> None:
    """Raise `InvalidSchemaError` if any `type: "object"` node in `schema`
    — at any nesting depth — does not set `additionalProperties: false`.

    The Anthropic API returns `400 invalid_request_error:
    output_config.format.schema: For 'object' type, 'additionalProperties'
    must be explicitly set to false`. No other provider enforces it, so a
    schema built and tested against the local or OpenAI backends passes
    every test and only 400s the moment `llm.provider` becomes
    `anthropic` — exactly how that defect reached a live run undetected.
    Checking it here, in the provider-independent layer, is what lets
    `tests/test_schema_shapes.py` sweep every schema in `app/` at test
    time regardless of which provider is configured.

    Walks every place a subschema can appear: `properties`, `items` (both
    the single-schema and tuple-validation list forms), `anyOf`/`oneOf`/
    `allOf`, `not`, and `$defs`/`definitions`. `path` names the offending
    node in the raised error so a multi-object schema doesn't leave the
    caller guessing which part is wrong.
    """
    if not isinstance(schema, dict):
        return

    if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
        raise InvalidSchemaError(
            f"schema node at {path!r} has type 'object' but does not set "
            "'additionalProperties: false'; the Anthropic API rejects "
            "object schemas without it (400 invalid_request_error)"
        )

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            validate_schema_shape(subschema, f"{path}.properties.{key}")

    items = schema.get("items")
    if isinstance(items, dict):
        validate_schema_shape(items, f"{path}.items")
    elif isinstance(items, list):
        for index, subschema in enumerate(items):
            validate_schema_shape(subschema, f"{path}.items[{index}]")

    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(keyword)
        if isinstance(variants, list):
            for index, subschema in enumerate(variants):
                validate_schema_shape(subschema, f"{path}.{keyword}[{index}]")

    not_schema = schema.get("not")
    if isinstance(not_schema, dict):
        validate_schema_shape(not_schema, f"{path}.not")

    defs = schema.get("$defs")
    defs_keyword = "$defs"
    if not isinstance(defs, dict):
        defs = schema.get("definitions")
        defs_keyword = "definitions"
    if isinstance(defs, dict):
        for key, subschema in defs.items():
            validate_schema_shape(subschema, f"{path}.{defs_keyword}.{key}")


def describe_schema(schema: dict) -> str:
    """Return a human-readable name for `schema` to quote in an error.

    Prefers the schema's `title`; falls back to its top-level property names,
    which is what makes an untitled schema still identifiable in a log.
    """
    title = schema.get("title")
    if isinstance(title, str) and title:
        return title
    properties = schema.get("properties")
    if isinstance(properties, dict) and properties:
        return f"<untitled schema with properties {', '.join(sorted(properties))}>"
    return f"<untitled schema {json.dumps(schema, sort_keys=True)}>"


def parse_and_validate(text: str, schema: dict) -> dict:
    """Parse `text` as JSON and validate it against `schema`.

    Raises `SchemaViolation` if the text does not parse, does not parse to a
    JSON object, or does not satisfy the schema.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SchemaViolation(f"response did not parse as JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise SchemaViolation(
            f"response parsed as {type(parsed).__name__}, not a JSON object"
        )

    try:
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError as exc:
        raise SchemaViolation(f"response did not conform to the schema: {exc.message}") from exc

    return parsed
