#!/usr/bin/env python3
"""Live provider smoke check — increment 01 plan, Task 8.

Makes ONE real, schema-constrained LLM completion, ONE real embedding call,
and ONE local reranker call using whatever models `config.yaml` currently
selects. It uses the same `app.bootstrap.bootstrap()` composition root every
other entry point uses and never hard-codes a provider or model name.

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

On success it prints the embedding vector's dimension, reranker scores, the
parsed schema-constrained object, and the model id the backend reports. The
deployment helper runs this script in cache-only mode, after `download-models`
has prepared both local models.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.bootstrap import bootstrap
from app.ingest.classify_doc import preflight_classifier
from app.rag.retrieve.rerank import Reranker


def main() -> int:
    app = bootstrap()
    cfg = app.config
    print(f"llm.provider = {cfg.llm.provider}", flush=True)
    print(f"embedding.provider = {cfg.embedding.provider}", flush=True)

    print("\n--- embedding call ---", flush=True)
    try:
        [vector] = app.embedding.embed(["How many vacation days do I have left?"])
    except Exception as exc:
        print(f"FAILED: cached embedding model is unavailable: {exc}", file=sys.stderr)
        return 1
    print("configured dimension:", app.embedding.dimension)
    print("actual vector length:", len(vector))

    if len(vector) != app.embedding.dimension:
        print("\nFAILED: vector length does not match configured dimension", file=sys.stderr)
        return 1

    print("\n--- reranker call ---", flush=True)
    print("configured model:", cfg.rag.rerank_model, flush=True)
    reranker = Reranker(cfg.rag.rerank_model)
    try:
        scores = reranker.score(
            "Which form should I submit for travel expenses?",
            [
                "Submit travel expenses using form FIN-204.",
                "The office kitchen is cleaned every Friday.",
            ],
        )
    except Exception as exc:
        print(f"FAILED: cached reranker model is unavailable: {exc}", file=sys.stderr)
        return 1
    print("scores:", scores)
    if len(scores) != 2:
        print("\nFAILED: reranker did not return one score per passage", file=sys.stderr)
        return 1

    print("\n--- schema-constrained completion (classify_llm) ---", flush=True)
    # Use the exact document-classification request contract used before an
    # upload is saved. A provider can accept a simpler sample schema while
    # rejecting the production schema, which makes that sample a false pass.
    result = preflight_classifier(cfg, app.classify_llm)
    print("parsed object:", json.dumps(result.parsed, indent=2))
    print("model id:", result.model)
    if result.parsed is None:
        print("\nFAILED: no parsed object returned", file=sys.stderr)
        return 1

    print("\nsmoke check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
