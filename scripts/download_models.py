#!/usr/bin/env python3
"""Download and validate the configured local retrieval models."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.bootstrap import bootstrap
from app.rag.retrieve.rerank import Reranker


def main() -> int:
    app = bootstrap()
    cfg = app.config

    print(f"Embedding model: {cfg.embedding.model}", flush=True)
    [vector] = app.embedding.embed(["IntelliKnow model download check"])
    if len(vector) != app.embedding.dimension:
        print("Embedding dimension does not match config", file=sys.stderr)
        return 1
    print(f"Embedding ready ({len(vector)} dimensions).", flush=True)

    print(f"Reranker model: {cfg.rag.rerank_model}", flush=True)
    scores = Reranker(cfg.rag.rerank_model).score(
        "Which form covers travel expenses?",
        [
            "Use form FIN-204 for travel expenses.",
            "The office kitchen is cleaned on Friday.",
        ],
    )
    if len(scores) != 2:
        print("Reranker did not return one score per passage", file=sys.stderr)
        return 1
    print(f"Reranker ready (scores: {scores}).", flush=True)
    print("Both local models are cached and executable.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
