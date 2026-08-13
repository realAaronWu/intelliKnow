"""FastAPI application entrypoint.

`design.md` § "Running the system is not a goal": `uv run uvicorn
app.main:app --port 8000` is the supported way to run the admin API.

`app` is exposed lazily, via `__getattr__` (PEP 562), rather than built
eagerly at module scope. `uvicorn app.main:app` resolves `app` through
`getattr` on this module, which is the only case meant to trigger the
real `bootstrap()` composition root — opening the real SQLite database,
the real FAISS directory, and the real configured LLM/embedding
providers. A plain `from app.main import create_app` — what tests do,
building their own app from fake providers and a tmp-path database
instead — never touches the `app` attribute, so importing this module
for testing has none of those side effects.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.api.documents import build_documents_router
from app.bootstrap import bootstrap
from app.db import create_engine_for, init_schema
from app.ingest.worker import IngestDeps
from app.rag.index_writer import IndexWriter
from app.rag.vector_store import VectorStore


def create_app(deps: IngestDeps) -> FastAPI:
    """Build the FastAPI app bound to `deps`. Pure: no I/O beyond
    constructing the `FastAPI`/`APIRouter` objects themselves.
    """
    fastapi_app = FastAPI(title="IntelliKnow KMS")
    fastapi_app.include_router(build_documents_router(deps))
    return fastapi_app


def _build_default_deps() -> IngestDeps:
    """Wire `IngestDeps` from the real composition root: `bootstrap()`'s
    providers plus a real `Engine`/`VectorStore`/`IndexWriter` opened at
    the paths named in `config.yaml`.
    """
    application = bootstrap()
    cfg = application.config

    engine = create_engine_for(Path(cfg.storage.sqlite_path))
    init_schema(engine)
    vector_store = VectorStore(Path(cfg.storage.faiss_dir), cfg.embedding.dimension)
    index_writer = IndexWriter(
        engine,
        vector_store,
        application.embedding,
        batch_size=cfg.embedding.batch_size,
    )

    return IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=application.classify_llm,
        embedding=application.embedding,
        vector_store=vector_store,
        index_writer=index_writer,
    )


def __getattr__(name: str):
    if name == "app":
        return create_app(_build_default_deps())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
