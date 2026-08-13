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
propagate unmasked instead of being retried.
"""

from __future__ import annotations

import json

import jsonschema


class SchemaViolation(Exception):
    """A structured response failed to parse or did not conform to the schema.

    Internal to the provider layer — callers outside it see the
    `ProviderError` a provider raises after its retry is exhausted.
    """


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
