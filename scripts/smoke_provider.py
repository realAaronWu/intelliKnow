#!/usr/bin/env python3
"""Live provider smoke check — increment 01 plan, Task 8.

Makes ONE real, schema-constrained LLM completion and ONE real embedding
call against whatever backend `config.yaml` currently selects (local,
openai, or anthropic), via the same `app.bootstrap.bootstrap()` composition
root every other entry point uses. This script never hard-codes a provider,
so it stays valid whichever backend `llm.provider` / `embedding.provider`
currently name — including across the local-default / anthropic-demo flip
described in `config.yaml`'s `llm:` comment block.

Why this exists (from the plan): every provider test injects a stub client,
so the suite proves our request/response shape is internally consistent —
never that the real SDK accepts it. A wrong structured-output request shape
would otherwise stay hidden until increment 04's first real classification.
Ten minutes here catches it early.

NOT part of the automated test suite and NOT run by CI or by this task: it
requires a real backend to be reachable.

    - `llm.provider: local` (the shipped default): needs an Ollama-compatible
      server reachable at `llm.base_url`, serving the model named by
      `llm.model_classify` -- e.g. `ollama pull llama3.1 && ollama serve`.
    - `llm.provider: anthropic` / `openai`: needs the matching API key set
      (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, see `.env.example`) and will
      incur real, small usage cost.

Run manually once a backend is available:

    uv run python scripts/smoke_provider.py

On success it prints the parsed schema-constrained object, the model id the
backend reports, and the embedding vector's dimension -- so a human can
eyeball that both match expectations before moving on.
"""

from __future__ import annotations

import json
import sys

from app.bootstrap import bootstrap

# A small, representative schema in the same shape the orchestrator's intent
# classifier will use starting in increment 04 -- exercising the real
# structured-output request path this early is the whole point of this
# script.
_SCHEMA = {
    "title": "smoke_check_classification",
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["intent", "confidence"],
    "additionalProperties": False,
}


def main() -> int:
    app = bootstrap()
    cfg = app.config
    print(f"llm.provider = {cfg.llm.provider}")
    print(f"embedding.provider = {cfg.embedding.provider}")

    print("\n--- schema-constrained completion (classify_llm) ---")
    result = app.classify_llm.complete(
        system=(
            "You are a routing classifier. Reply with ONLY a JSON object "
            "matching the given schema -- no prose, no markdown fences."
        ),
        user=(
            "Classify this message into one of: hr, legal, finance, "
            "operations, general. Message: 'How many vacation days do I "
            "have left this year?'"
        ),
        schema=_SCHEMA,
    )
    print("parsed object:", json.dumps(result.parsed, indent=2))
    print("model id:", result.model)

    print("\n--- embedding call ---")
    [vector] = app.embedding.embed(["How many vacation days do I have left?"])
    print("configured dimension:", app.embedding.dimension)
    print("actual vector length:", len(vector))

    if result.parsed is None:
        print("\nFAILED: no parsed object returned", file=sys.stderr)
        return 1
    if len(vector) != app.embedding.dimension:
        print("\nFAILED: vector length does not match configured dimension", file=sys.stderr)
        return 1

    print("\nsmoke check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
